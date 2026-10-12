"""Test doubles: an OpenAI-compatible streaming model and a Streamable-HTTP MCP server.
Scripted by keywords in the last user message:
  SAVE:<path>|<text>   -> workspace_write tool call, then confirmation text
  STATUS:<note>        -> project_set_status tool call
  SEARCH:<query>       -> search_sources tool call
  PY:<code>            -> python_run tool call
  MCP:<a>,<b>          -> mcp add tool call
  FAIL                 -> HTTP 500 from the model
  anything else        -> echoes the last user message
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _Model(BaseHTTPRequestHandler):
    seen = []  # recorded requests: {"auth": str, "body": dict}

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.endswith("/models"):
            body = json.dumps({"data": [{"id": "mock-1"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        _Model.seen.append({"auth": self.headers.get("Authorization", ""), "body": body})
        msgs = body.get("messages", [])
        last_user = next((m["content"] for m in reversed(msgs) if m["role"] == "user"), "")
        after_tool = msgs and msgs[-1]["role"] == "tool"
        if "FAIL" in last_user:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            payload = b'{"error":"boom"}'
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        chunks = _script(last_user, after_tool, msgs, body.get("tools") or [])
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for c in chunks:
            self.wfile.write(("data: " + json.dumps(c) + "\n\n").encode())
        self.wfile.write(b"data: [DONE]\n\n")

    def _tool(self, idx, name, args):
        return {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": f"call_{idx}", "type": "function",
                                                      "function": {"name": name, "arguments": json.dumps(args)}}]}}]}


def _text(s):
    return {"choices": [{"delta": {"content": s}}]}


def _script(last_user, after_tool, msgs, tools):
    names = {t["function"]["name"] for t in tools}
    if last_user.startswith("THINK:"):
        if after_tool:
            return [_text("Done after think.")]
        return [{"choices": [{"delta": {"reasoning_content": "step one. "}}]},
                {"choices": [{"delta": {"reasoning_content": "step two."}}]},
                _call("workspace_write", {"path": "think/out.md", "content": "thought"})]
    if last_user.startswith("CUT:"):
        return [{"choices": [{"delta": {"reasoning_content": "still thinking"}, "finish_reason": "length"}]}]
    if after_tool:
        return [_text("Done: " + msgs[-1]["content"][:120])]
    u = last_user
    if u.startswith("SAVE:") and "workspace_write" in names:
        path, _, content = u[5:].partition("|")
        return [_call("workspace_write", {"path": path.strip(), "content": content.strip()})]
    if u.startswith("STATUS:") and "project_set_status" in names:
        return [_call("project_set_status", {"state": "active", "note": u[7:].strip(), "next_step": "continue"})]
    if u.startswith("SKILLDRAFT:"):
        return [_text('Draft:\n```json\n{"name": "Contract Reviewer", "instructions": "Check every clause against the brief. Output risks first, then fixes."}\n```')]
    if u.startswith("SKILLBAD:"):
        return [_text("I cannot produce JSON for that.")]
    if u.startswith("SEARCH:") and "search_sources" in names:
        return [_call("search_sources", {"query": u[7:].strip()})]
    if u.startswith("PY:") and "python_run" in names:
        return [_call("python_run", {"code": u[3:].strip()})]
    if u.startswith("MCP:") and any(n.startswith("mcp_") for n in names):
        mcp_name = next(n for n in names if n.startswith("mcp_"))
        a, _, b = u[4:].partition(",")
        return [_call(mcp_name, {"a": int(a), "b": int(b)})]
    reply = "ECHO: " + u[:200]
    mid = max(1, len(reply) // 2)
    return [_text(reply[:mid]), _text(reply[mid:])]


def _call(name, args):
    return {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_x", "type": "function",
                                                  "function": {"name": name, "arguments": json.dumps(args)}}]}}]}


class _Mcp(BaseHTTPRequestHandler):
    sessions = set()

    def log_message(self, *a):
        pass

    def _send(self, status, obj=None, sid=None):
        data = json.dumps(obj).encode() if obj is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        if sid:
            self.send_header("Mcp-Session-Id", sid)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        msg = json.loads(self.rfile.read(length) or b"{}")
        method = msg.get("method")
        if method == "initialize":
            sid = "sess-1"
            _Mcp.sessions.add(sid)
            return self._send(200, {"jsonrpc": "2.0", "id": msg["id"], "result": {
                "protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                "serverInfo": {"name": "mock", "version": "1"}}}, sid)
        if method == "notifications/initialized":
            return self._send(202)
        sid = self.headers.get("MCP-Session-Id")
        if sid not in _Mcp.sessions:
            return self._send(404, {"error": "unknown session"})
        if method == "tools/list":
            return self._send(200, {"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": [{
                "name": "add", "description": "Add two integers",
                "inputSchema": {"type": "object", "properties": {"a": {"type": "integer"},
                                                                 "b": {"type": "integer"}}, "required": ["a", "b"]}}]}})
        if method == "tools/call":
            a = msg["params"]["arguments"]["a"]
            b = msg["params"]["arguments"]["b"]
            return self._send(200, {"jsonrpc": "2.0", "id": msg["id"], "result": {
                "content": [{"type": "text", "text": str(a + b)}], "isError": False}})
        return self._send(400, {"error": "bad method"})


def start(handler_cls):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def start_model():
    return start(_Model)


def start_mcp():
    return start(_Mcp)
