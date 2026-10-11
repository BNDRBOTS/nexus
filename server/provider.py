"""OpenAI-compatible chat client (streaming). Routes:
  custom  -> user's base_url + key (key arrives per request from the browser, never stored server-side)
  gateway -> operator's paid gateway from env (NX_GATEWAY_*)

Presets (config.PROVIDER_PRESETS) only fill base_url/model defaults and select the provider-specific
request fields below. Verified against provider docs (2026-10):
  DeepSeek   base https://api.deepseek.com ; body.thinking={"type": enabled|disabled}; body.reasoning_effort low|high|max
             delta.reasoning_content = chain of thought. With body.tools, prior reasoning_content MUST be sent back.
  OpenRouter base https://openrouter.ai/api/v1 ; body.reasoning={"effort": ...}; delta.reasoning = reasoning text
  Z.ai       base https://api.z.ai/api/paas/v4 (standard API). The GLM Coding Plan endpoint
             (/api/coding/paas/v4) is restricted to supported coding tools and is NOT used.
"""
import codecs
import json
import re
import time
import urllib.error
import urllib.request

from . import config as C


class ProviderError(Exception):
    pass


class SSEDecoder:
    """Stateful, byte-safe SSE decoder. Handles fragmented UTF-8, CRLF/CR/LF endings (a trailing CR waits
    for the next chunk), comment lines, multi-line data (joined with \\n), and events split across chunks."""

    _EOL = re.compile(r"\r\n|\r|\n")

    def __init__(self):
        self._dec = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._buf = ""
        self._data = []
        self._event = "message"

    def feed(self, chunk):
        self._buf += self._dec.decode(chunk)
        events = []
        while True:
            m = self._EOL.search(self._buf)
            if not m:
                break
            if m.group() == "\r" and m.end() == len(self._buf):
                break               # lone CR at the chunk edge: may be the first half of CRLF
            line = self._buf[:m.start()]
            self._buf = self._buf[m.end():]
            ev = self._line(line)
            if ev:
                events.append(ev)
        return events

    def flush(self):
        events = []
        self._buf += self._dec.decode(b"", final=True)
        if self._buf:
            ev = self._line(self._buf)
            if ev:
                events.append(ev)
            self._buf = ""
        ev = self._dispatch()
        if ev:
            events.append(ev)
        return events

    def _line(self, line):
        if line == "":
            return self._dispatch()
        if line.startswith(":"):
            return None
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "data":
            self._data.append(value)
        elif field == "event":
            self._event = value
        return None

    def _dispatch(self):
        if not self._data:
            self._event = "message"
            return None
        ev = (self._event, "\n".join(self._data))
        self._data, self._event = [], "message"
        return ev


def preset_of(settings):
    name = settings["provider"].get("preset") or "custom"
    return name if name in C.PROVIDER_PRESETS else "custom"


def resolve(settings, key=None):
    p = settings["provider"]
    if p.get("mode") == "gateway":
        if not C.GATEWAY["url"]:
            raise ProviderError("gateway not configured (NX_GATEWAY_URL)")
        return {"base": C.GATEWAY["url"].rstrip("/"), "key": C.GATEWAY["key"], "preset": "custom",
                "model": p.get("model") or C.GATEWAY["model"], "route": "gateway", "thinking": None,
                "reasoning_effort": p.get("reasoning_effort"), "headers": {}}
    preset = preset_of(settings)
    base = (p.get("base_url") or C.PROVIDER_PRESETS[preset]["base_url"] or "").rstrip("/")
    if not base:
        raise ProviderError("no base_url set in settings")
    if not p.get("model"):
        raise ProviderError("no model set in settings")
    return {"base": base, "key": key or "", "model": p["model"], "route": "custom", "preset": preset,
            "thinking": p.get("thinking"), "reasoning_effort": p.get("reasoning_effort"),
            "headers": dict(C.PROVIDER_PRESETS[preset].get("headers") or {})}


def _headers(cfg, accept="text/event-stream, application/json"):
    h = {"Content-Type": "application/json", "Accept": accept}
    h.update(cfg.get("headers") or {})
    if cfg.get("key"):
        h["Authorization"] = f"Bearer {cfg['key']}"
    return h


def _extras(cfg, body, tools):
    """Provider-specific request fields. Only fields documented for that provider are sent."""
    preset = cfg.get("preset")
    effort = cfg.get("reasoning_effort")
    if preset == "deepseek":
        if cfg.get("thinking") in ("enabled", "disabled"):
            body["thinking"] = {"type": cfg["thinking"]}
        if effort in C.PROVIDER_PRESETS["deepseek"]["efforts"]:
            body["reasoning_effort"] = effort
    elif preset == "openrouter":
        if effort in C.PROVIDER_PRESETS["openrouter"]["efforts"]:
            body["reasoning"] = {"effort": effort}
    return body


def _strip_reasoning(messages):
    """reasoning_content is only meaningful for requests that carry tools (DeepSeek). Otherwise drop it."""
    return [{k: v for k, v in m.items() if k != "reasoning_content"} if "reasoning_content" in m else m
            for m in messages]


class _Acc:
    """Accumulates one completion. Reasoning and visible content are separate channels."""

    def __init__(self):
        self.content, self.reasoning, self.calls = [], [], {}
        self.finish, self.usage = None, None

    def apply(self, obj):
        out = []
        if obj.get("error"):
            raise ProviderError(str(obj["error"])[:400])
        if obj.get("usage"):
            self.usage = obj["usage"]
        choices = obj.get("choices") or []
        if not choices:
            return out
        ch = choices[0]
        delta = ch.get("delta") or ch.get("message") or {}
        r = delta.get("reasoning_content") or delta.get("reasoning")
        if r:
            self.reasoning.append(r)
            out.append(("reasoning", r))
        text = delta.get("content")
        if text:
            self.content.append(text)
            out.append(("delta", text))
        for i, tc in enumerate(delta.get("tool_calls") or []):
            slot = self.calls.setdefault(tc.get("index", i), {"id": "", "name": "", "arguments": ""})
            if tc.get("id"):
                slot["id"] = tc["id"]
            fn = tc.get("function") or {}
            slot["name"] += fn.get("name") or ""
            slot["arguments"] += fn.get("arguments") or ""
        if ch.get("finish_reason"):
            self.finish = ch["finish_reason"]
        return out

    def final(self):
        return {"content": "".join(self.content), "reasoning": "".join(self.reasoning),
                "tool_calls": [self.calls[k] for k in sorted(self.calls) if self.calls[k]["name"]],
                "finish_reason": self.finish, "usage": self.usage}


def _frame(line_data):
    try:
        return json.loads(line_data)
    except json.JSONDecodeError as e:
        raise ProviderError(f"malformed stream frame: {e}"[:200])


def chat_stream(cfg, messages, tools=None, temperature=None, timeout=180):
    """Yields ("reasoning", text) and ("delta", text) while streaming, then ("final", {...}).
    final = {content, reasoning, tool_calls, finish_reason, usage}."""
    body = {"model": cfg["model"], "messages": messages if tools else _strip_reasoning(messages), "stream": True}
    if temperature is not None:
        body["temperature"] = temperature
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    body = _extras(cfg, body, tools)
    req = urllib.request.Request(cfg["base"] + "/chat/completions", data=json.dumps(body).encode("utf-8"),
                                 headers=_headers(cfg), method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        raise ProviderError(f"HTTP {e.code}: {e.read(400).decode('utf-8', 'replace')}")
    except Exception as e:  # network / DNS / TLS
        raise ProviderError(f"{type(e).__name__}: {e}")

    acc = _Acc()
    with resp:
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if "text/event-stream" not in ctype and "json" in ctype:
            # Provider ignored stream=true: parse the whole completion, never return silent empty output.
            obj = _frame(resp.read().decode("utf-8", "replace"))
            if obj.get("error"):
                raise ProviderError(str(obj["error"])[:400])
            ch = (obj.get("choices") or [{}])[0]
            for kind, val in acc.apply({"choices": [{"delta": ch.get("message") or {},
                                                     "finish_reason": ch.get("finish_reason")}],
                                        "usage": obj.get("usage")}):
                yield (kind, val)
        else:
            dec = SSEDecoder()
            done = False
            while not done:
                chunk = resp.read1(8192)
                if not chunk:
                    break
                for _, data in dec.feed(chunk):
                    if data == "[DONE]":
                        done = True
                        break
                    for kind, val in acc.apply(_frame(data)):
                        yield (kind, val)
            if not done:
                for _, data in dec.flush():
                    if data != "[DONE]":
                        for kind, val in acc.apply(_frame(data)):
                            yield (kind, val)
    yield ("final", acc.final())


def status(settings, key=None, timeout=6):
    """Model status probe: GET {base}/models. Never raises. 401/403/404 mean the endpoint answered."""
    out = {"route": settings["provider"].get("mode"), "preset": preset_of(settings),
           "model": settings["provider"].get("model"), "reachable": False, "latency_ms": None,
           "error": None, "checked_at": time.time()}
    try:
        cfg = resolve(settings, key)
    except ProviderError as e:
        out["error"] = str(e)
        return out
    out["route"] = cfg["route"]
    out["model"] = cfg["model"]
    if cfg["route"] == "custom" and not cfg["key"]:
        out["error"] = "no API key supplied"
    req = urllib.request.Request(cfg["base"] + "/models", headers=_headers(cfg, "application/json"))
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out["latency_ms"] = int((time.time() - t0) * 1000)
            out["reachable"] = 200 <= r.status < 300
    except urllib.error.HTTPError as e:
        out["latency_ms"] = int((time.time() - t0) * 1000)
        out["reachable"] = e.code in (401, 403, 404)
        out["error"] = f"HTTP {e.code}"
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"[:300]
    return out
