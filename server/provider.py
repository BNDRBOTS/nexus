"""OpenAI-compatible chat client (streaming). Two routes:
  custom  -> user's base_url + key (key arrives per request from the browser, never stored server-side)
  gateway -> operator's paid gateway from env (NX_GATEWAY_*)
"""
import json
import time
import urllib.error
import urllib.request

from . import config as C


class ProviderError(Exception):
    pass


def resolve(settings, key=None):
    p = settings["provider"]
    if p.get("mode") == "gateway":
        if not C.GATEWAY["url"]:
            raise ProviderError("gateway not configured (NX_GATEWAY_URL)")
        return {"base": C.GATEWAY["url"].rstrip("/"), "key": C.GATEWAY["key"],
                "model": p.get("model") or C.GATEWAY["model"], "route": "gateway"}
    base = (p.get("base_url") or "").rstrip("/")
    if not base:
        raise ProviderError("no base_url set in settings")
    if not p.get("model"):
        raise ProviderError("no model set in settings")
    return {"base": base, "key": key or "", "model": p["model"], "route": "custom"}


def _headers(key, accept="text/event-stream, application/json"):
    h = {"Content-Type": "application/json", "Accept": accept}
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h


def chat_stream(cfg, messages, tools=None, temperature=None, timeout=180):
    """Yields ("delta", text) while streaming, then ("final", {content, tool_calls})."""
    body = {"model": cfg["model"], "messages": messages, "stream": True}
    if temperature is not None:
        body["temperature"] = temperature
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    req = urllib.request.Request(cfg["base"] + "/chat/completions", data=json.dumps(body).encode("utf-8"),
                                 headers=_headers(cfg["key"]), method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        raise ProviderError(f"HTTP {e.code}: {e.read(400).decode('utf-8', 'replace')}")
    except Exception as e:  # network / DNS / TLS
        raise ProviderError(f"{type(e).__name__}: {e}")

    parts, calls = [], {}
    with resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            if obj.get("error"):
                raise ProviderError(str(obj["error"])[:400])
            choices = obj.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            text = delta.get("content")
            if text:
                parts.append(text)
                yield ("delta", text)
            for tc in delta.get("tool_calls") or []:
                slot = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "arguments": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                slot["name"] += fn.get("name") or ""
                slot["arguments"] += fn.get("arguments") or ""
    yield ("final", {"content": "".join(parts),
                     "tool_calls": [calls[k] for k in sorted(calls) if calls[k]["name"]]})


def status(settings, key=None, timeout=6):
    """Model status probe: GET {base}/models. Never raises."""
    out = {"route": settings["provider"].get("mode"), "model": settings["provider"].get("model"),
           "reachable": False, "latency_ms": None, "error": None, "checked_at": time.time()}
    try:
        cfg = resolve(settings, key)
    except ProviderError as e:
        out["error"] = str(e)
        return out
    out["route"] = cfg["route"]
    out["model"] = cfg["model"]
    if cfg["route"] == "custom" and not cfg["key"]:
        out["error"] = "no API key supplied"
    req = urllib.request.Request(cfg["base"] + "/models", headers=_headers(cfg["key"], "application/json"))
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out["latency_ms"] = int((time.time() - t0) * 1000)
            out["reachable"] = 200 <= r.status < 300
    except urllib.error.HTTPError as e:
        out["latency_ms"] = int((time.time() - t0) * 1000)
        # 404 = provider has no /models route; the endpoint itself answered.
        out["reachable"] = e.code in (401, 403, 404)
        out["error"] = f"HTTP {e.code}"
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"[:300]
    return out
