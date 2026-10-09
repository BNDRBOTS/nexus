"""MCP client, Streamable HTTP transport (JSON-RPC 2.0 over HTTP POST).
Supports JSON and SSE responses. stdio servers are out of scope (no subprocess trust by default).
Server config doc: {id, name, url, enabled, headers: {}}.
"""
import json
import time
import urllib.error
import urllib.request

PROTOCOL = "2025-06-18"
SESSION_TTL = 600

_sessions = {}   # server_id -> {"sid", "tools", "ts"}
_next_id = [0]


class McpError(Exception):
    pass


def _rpc_id():
    _next_id[0] += 1
    return _next_id[0]


def _post(server, payload, sid=None, timeout=20):
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    headers.update(server.get("headers") or {})
    if sid:
        headers["MCP-Session-Id"] = sid
    req = urllib.request.Request(server["url"], data=json.dumps(payload).encode("utf-8"),
                                 headers=headers, method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        raise McpError(f"HTTP {e.code}")
    except Exception as e:
        raise McpError(f"{type(e).__name__}: {e}")
    with resp:
        ctype = resp.headers.get("Content-Type", "")
        sid_out = resp.headers.get("MCP-Session-Id") or sid
        body = resp.read().decode("utf-8", "replace")
    if payload.get("id") is None:          # notification: no response body expected
        return None, sid_out
    if "text/event-stream" in ctype:
        for line in body.splitlines():
            if line.startswith("data:"):
                try:
                    msg = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                if msg.get("id") == payload["id"]:
                    return msg, sid_out
        raise McpError("no matching response in event stream")
    try:
        return json.loads(body), sid_out
    except json.JSONDecodeError:
        raise McpError("non-JSON response")


def _request(server, method, params, sid):
    msg, sid = _post(server, {"jsonrpc": "2.0", "id": _rpc_id(), "method": method, "params": params}, sid)
    if msg is None:
        raise McpError("empty response")
    if "error" in msg:
        raise McpError(str(msg["error"])[:300])
    return msg.get("result") or {}, sid


def _session(server, force=False):
    cached = _sessions.get(server["id"])
    if cached and not force and time.time() - cached["ts"] < SESSION_TTL:
        return cached
    init, sid = _request(server, "initialize", {
        "protocolVersion": PROTOCOL, "capabilities": {},
        "clientInfo": {"name": "nexus", "version": "2.0.0"}}, None)
    _post(server, {"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
    tools, cursor = [], None
    while True:
        params = {"cursor": cursor} if cursor else {}
        res, sid = _request(server, "tools/list", params, sid)
        tools.extend(res.get("tools") or [])
        cursor = res.get("nextCursor")
        if not cursor:
            break
    entry = {"sid": sid, "tools": tools, "ts": time.time(), "protocol": init.get("protocolVersion")}
    _sessions[server["id"]] = entry
    return entry


def list_tools(server):
    return _session(server)["tools"]


def call(server, tool, arguments):
    for attempt in (1, 2):   # one retry with a fresh session (sessions expire server-side)
        try:
            sess = _session(server, force=attempt == 2)
            res, _ = _request(server, "tools/call", {"name": tool, "arguments": arguments or {}}, sess["sid"])
            text = "\n".join(c.get("text", "") for c in res.get("content") or [] if c.get("type") == "text")
            if res.get("isError"):
                raise McpError(text or "tool reported error")
            return text or json.dumps(res.get("structuredContent") or res)[:TOOL_TEXT_CAP]
        except McpError:
            if attempt == 2:
                raise
            _sessions.pop(server["id"], None)


def probe(server):
    try:
        tools = list_tools(server)
        return {"ok": True, "tools": [t.get("name") for t in tools], "protocol": PROTOCOL}
    except McpError as e:
        _sessions.pop(server["id"], None)
        return {"ok": False, "error": str(e)}


TOOL_TEXT_CAP = 12000
