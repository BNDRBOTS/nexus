"""HTTP server (stdlib only). Serves the UI and the JSON/SSE API.

Run:  python3 -m server.app          (from repo root)
Env:  NX_HOST NX_PORT NX_DATA_DIR NX_DB_URL NX_TOKEN NX_ALLOW_OPEN NX_GATEWAY_URL NX_GATEWAY_KEY NX_GATEWAY_MODEL NX_ENABLE_PYTHON
Auth: every /api/* call needs  Authorization: Bearer <token>  (or X-NX-Token). Token = NX_TOKEN, else
      DATA_DIR/token (created 0600 on first start). NX_ALLOW_OPEN=1 disables auth (local dev only).
Provider key (custom route): header X-NX-Provider-Key. Never stored, never logged.
"""
import hmac
import json
import mimetypes
import os
import re
import secrets
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import config as C
from . import engine as E
from . import mcp as MCP
from . import plugins as PL
from . import skillmaker as SM
from . import provider as P
from . import store
from . import tools as T

ROUTES = []
DOC_KINDS = {"project", "role", "skill", "workflow", "mcp", "plugin"}
CHAT_PATCH_FIELDS = {"title", "mode", "role_id", "chat_prompt", "reminder", "tool_groups", "project_id", "skill_ids"}


class HttpError(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status = status


def route(method, pattern):
    rx = re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", pattern) + "$")

    def deco(fn):
        ROUTES.append((method, rx, fn))
        return fn
    return deco


class Req:
    def __init__(self, handler, params, query, raw_body):
        self.h = handler
        self.p = params
        self.q = {k: v[0] for k, v in parse_qs(query).items()}
        self._raw = raw_body

    def json(self):
        if not self._raw:
            return {}
        try:
            val = json.loads(self._raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise HttpError(400, "body must be JSON")
        if not isinstance(val, dict):
            raise HttpError(400, "body must be a JSON object")
        return val

    @property
    def provider_key(self):
        return self.h.headers.get("X-NX-Provider-Key") or None


def _need_doc(kind, doc_id):
    doc = store.get_doc(kind, doc_id)
    if doc is None:
        raise HttpError(404, f"{kind} {doc_id} not found")
    return doc


# ---------- state & status ----------

@route("GET", "/api/state")
def state(req):
    chats = []
    for c in store.list_docs("chat"):
        chats.append({"id": c["id"], "title": c.get("title", ""), "project_id": c.get("project_id"),
                      "mode": c.get("mode"), "role_id": c.get("role_id"), "updated": c.get("updated"),
                      "messages": len(c.get("messages") or [])})
    return 200, {
        "version": C.VERSION, "modes": C.MODES, "tool_groups": C.TOOL_GROUPS, "inbox": C.INBOX,
        "settings": store.get_settings(), "projects": store.list_docs("project"), "chats": chats,
        "roles": store.list_docs("role"), "skills": store.list_docs("skill"),
        "workflows": store.list_docs("workflow"), "mcp": store.list_docs("mcp"),
        "plugins": store.list_docs("plugin"),
        "python_enabled": C.PYTHON_ENABLED, "gateway_configured": bool(C.GATEWAY["url"]),
        "token_required": bool(C.TOKEN),
        "provider_presets": C.PROVIDER_PRESETS, "reasoning_efforts": C.REASONING_EFFORTS,
        "thinking_modes": C.THINKING_MODES,
    }


@route("GET", "/api/status")
def status(req):
    st = store.get_settings()
    provider = P.status(st, req.provider_key) if req.q.get("probe", "1") == "1" else None
    mcp_rows = []
    for srv in store.list_docs("mcp"):
        mcp_rows.append({"id": srv["id"], "name": srv.get("name"), "enabled": bool(srv.get("enabled"))})
    return 200, {"provider": provider, "python_enabled": C.PYTHON_ENABLED, "mcp": mcp_rows,
                 "db": {"path": store.db_path(), **store.counts()}, "version": C.VERSION}


@route("GET", "/api/actions")
def actions(req):
    rows = store.list_actions(req.q.get("project"), req.q.get("chat"), int(req.q.get("limit", "100")))
    return 200, {"actions": rows}


# ---------- settings & docs ----------

def normalize_knacks(raw):
    """Validates user knacks. Returns a clean list of {id, label, prompt}; raises HttpError(400) on any violation."""
    if not isinstance(raw, list):
        raise HttpError(400, "knacks must be a list")
    if len(raw) > C.KNACK_MAX:
        raise HttpError(400, f"at most {C.KNACK_MAX} knacks")
    out, seen = [], set()
    for i, k in enumerate(raw):
        where = f"knacks[{i}]"
        if not isinstance(k, dict):
            raise HttpError(400, f"{where} must be an object")
        extra = set(k) - {"id", "label", "prompt"}
        if extra:
            raise HttpError(400, f"{where}: unknown fields {sorted(extra)}")
        kid = k.get("id")
        if not isinstance(kid, str) or not re.match(C.KNACK_ID_RE, kid):
            raise HttpError(400, f"{where}.id must match {C.KNACK_ID_RE}")
        if kid in C.KNACK_BUILTIN_IDS:
            raise HttpError(400, f"{where}.id '{kid}' is a built-in knack")
        if kid in seen:
            raise HttpError(400, f"{where}.id '{kid}' is duplicated")
        label = k.get("label")
        if not isinstance(label, str) or not label.strip() or len(label.strip()) > C.KNACK_LABEL_MAX:
            raise HttpError(400, f"{where}.label must be 1-{C.KNACK_LABEL_MAX} chars")
        prompt = k.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt.strip()) > C.KNACK_PROMPT_MAX:
            raise HttpError(400, f"{where}.prompt must be 1-{C.KNACK_PROMPT_MAX} chars")
        seen.add(kid)
        out.append({"id": kid, "label": label.strip(), "prompt": prompt.strip()})
    return out


@route("PUT", "/api/settings")
def put_settings(req):
    body = req.json()
    cur = store.get_settings()
    for k, v in body.items():
        if k not in C.DEFAULT_SETTINGS:
            raise HttpError(400, f"unknown setting {k}")
        if isinstance(v, dict) and isinstance(cur.get(k), dict):
            cur[k].update(v)
        else:
            cur[k] = v
    prov = cur["provider"]
    if prov["mode"] not in ("custom", "gateway"):
        raise HttpError(400, "provider.mode must be custom|gateway")
    if prov.get("preset") not in C.PROVIDER_PRESETS:
        raise HttpError(400, f"provider.preset must be one of {sorted(C.PROVIDER_PRESETS)}")
    if prov.get("thinking") not in C.THINKING_MODES:
        raise HttpError(400, "provider.thinking must be null|enabled|disabled")
    if prov.get("reasoning_effort") not in C.REASONING_EFFORTS:
        raise HttpError(400, f"provider.reasoning_effort must be one of {list(C.REASONING_EFFORTS)}")
    if not isinstance(cur.get("context_chars"), int) or cur["context_chars"] < 1000:
        raise HttpError(400, "context_chars must be an integer >= 1000")
    cur["knacks"] = normalize_knacks(cur.get("knacks", []))
    store.put_doc("setting", "app", cur)
    return 200, store.get_settings()


@route("POST", "/api/skills/draft")
def draft_skill(req):
    body = req.json()
    result = SM.draft(body.get("description"), store.get_settings(), req.provider_key)
    store.add_action("skill.drafted", result["name"], {"name": result["name"], "chars": len(result["instructions"]),
                                                       "model": result["model"]})
    return 200, result


@route("POST", "/api/doc/{kind}")
def create_doc(req):
    kind = req.p["kind"]
    if kind not in DOC_KINDS:
        raise HttpError(400, f"kind must be one of {sorted(DOC_KINDS)}")
    body = req.json()
    prefix = {"project": "prj", "role": "role", "skill": "skl", "workflow": "wf", "mcp": "mcp", "plugin": "plg"}[kind]
    doc_id = body.pop("id", None) or store.new_id(prefix)
    if store.get_doc(kind, doc_id):
        raise HttpError(409, "id exists; use PUT")
    if kind == "plugin":
        body = PL.normalize(body)
    return 201, store.put_doc(kind, doc_id, body)


@route("PUT", "/api/doc/{kind}/{id}")
def put_doc(req):
    kind = req.p["kind"]
    if kind not in DOC_KINDS:
        raise HttpError(400, f"kind must be one of {sorted(DOC_KINDS)}")
    body = req.json()
    if kind == "project" and req.p["id"] == C.INBOX:
        body["name"] = body.get("name") or "Inbox"
    if kind == "plugin":
        body = PL.normalize(body)
    return 200, store.put_doc(kind, req.p["id"], body)


@route("DELETE", "/api/doc/{kind}/{id}")
def del_doc(req):
    kind = req.p["kind"]
    if kind not in DOC_KINDS or req.p["id"] == C.INBOX and kind == "project":
        raise HttpError(400, "cannot delete this document")
    store.del_doc(kind, req.p["id"])
    return 200, {"deleted": req.p["id"]}


@route("GET", "/api/projects/{id}")
def get_project(req):
    project = _need_doc("project", req.p["id"])
    chats = [{"id": c["id"], "title": c.get("title", ""), "mode": c.get("mode"), "updated": c.get("updated"),
              "messages": len(c.get("messages") or [])}
             for c in store.list_docs("chat") if c.get("project_id") == req.p["id"]]
    return 200, {"project": project, "chats": chats,
                 "files": store.list_files(req.p["id"]),
                 "actions": store.list_actions(project_id=req.p["id"], limit=50)}


# ---------- chats ----------

@route("POST", "/api/chats")
def create_chat(req):
    body = req.json()
    mode = body.get("mode", "work")
    if mode not in C.MODES:
        raise HttpError(400, "mode must be work|research|code")
    pid = body.get("project_id") or C.INBOX
    _need_doc("project", pid)
    chat_id = store.new_id("chat")
    chat = store.put_doc("chat", chat_id, {
        "title": body.get("title") or "New chat", "project_id": pid, "mode": mode,
        "role_id": body.get("role_id"), "chat_prompt": body.get("chat_prompt", ""),
        "reminder": body.get("reminder", ""), "tool_groups": body.get("tool_groups"),
        "skill_ids": body.get("skill_ids") or [], "messages": []})
    store.add_action("chat.created", chat_id, {"mode": mode}, project_id=pid, chat_id=chat_id)
    return 201, chat


@route("GET", "/api/chats/{id}")
def get_chat(req):
    return 200, store.get_chat(req.p["id"])


@route("PATCH", "/api/chats/{id}")
def patch_chat(req):
    chat = store.get_chat(req.p["id"])
    body = req.json()
    for k, v in body.items():
        if k not in CHAT_PATCH_FIELDS:
            raise HttpError(400, f"field not patchable: {k}")
    if "mode" in body and body["mode"] not in C.MODES:
        raise HttpError(400, "mode must be work|research|code")
    if "project_id" in body:
        _need_doc("project", body["project_id"])
    chat.update({k: v for k, v in body.items()})
    store.put_chat(chat)
    if "project_id" in body:
        store.add_action("chat.moved", chat["id"], {"project_id": body["project_id"]},
                         project_id=body["project_id"], chat_id=chat["id"])
    return 200, chat


@route("DELETE", "/api/chats/{id}")
def delete_chat(req):
    chat = store.get_chat(req.p["id"])
    store.del_doc("chat", chat["id"])
    store.add_action("chat.deleted", chat["id"], {"messages": len(chat.get("messages") or [])},
                     project_id=chat.get("project_id"), chat_id=chat["id"])
    return 200, {"deleted": chat["id"]}


@route("GET", "/api/chats/{id}/context")
def chat_context(req):
    return 200, E.context_preview(req.p["id"])


@route("POST", "/api/chats/{id}/messages")
def post_message(req):
    body = req.json()
    return _turn_response(req, req.p["id"], body.get("content"), regenerate=False,
                          stream=bool(body.get("stream")) or req.q.get("stream") == "1",
                          display=body.get("display"))


@route("POST", "/api/chats/{id}/regenerate")
def regenerate(req):
    body = req.json()
    return _turn_response(req, req.p["id"], None, regenerate=True,
                          stream=bool(body.get("stream")) or req.q.get("stream") == "1")


def _turn_response(req, chat_id, content, regenerate, stream, display=None):
    if not stream:
        result = E.turn(chat_id, content, regenerate=regenerate, provider_key=req.provider_key, display=display)
        return 200, result
    h = req.h
    h.send_response(200)
    h.send_header("Content-Type", "text/event-stream; charset=utf-8")
    h.send_header("Cache-Control", "no-cache")
    h.end_headers()
    dead = {"v": False}

    def emit(event, data):
        if dead["v"]:
            return          # client left: the turn still completes and is recorded
        try:
            h.wfile.write(f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8"))
            h.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            dead["v"] = True

    try:
        result = E.turn(chat_id, content, regenerate=regenerate, provider_key=req.provider_key, emit=emit, display=display)
        emit("done", result)
    except Exception as e:
        emit("error", {"status": _status_for(e), "error": str(e)})
    return None, None


def _status_for(e):
    if isinstance(e, E.Busy):
        return 409
    if isinstance(e, (store.NotFound,)):
        return 404
    if isinstance(e, (E.EngineError, store.Invalid, T.ToolError)):
        return 400
    if isinstance(e, P.ProviderError):
        return 502
    return 500


@route("POST", "/api/chats/{id}/messages/{mid}/save")
def save_message(req):
    body = req.json()
    meta = E.save_message_to_workspace(req.p["id"], req.p["mid"], body.get("path", ""))
    return 200, meta


@route("POST", "/api/chats/{id}/messages/{mid}/pin")
def pin_message(req):
    body = req.json()
    return 200, E.set_pin(req.p["id"], req.p["mid"], body.get("pinned", True))


@route("DELETE", "/api/chats/{id}/messages/{mid}")
def delete_message(req):
    E.delete_message(req.p["id"], req.p["mid"])
    return 200, {"deleted": req.p["mid"]}


# ---------- workspace files ----------

@route("GET", "/api/files")
def get_files(req):
    pid = req.q.get("project", C.INBOX)
    if "path" in req.q:
        f = store.get_file(pid, req.q["path"])
        if not f:
            raise HttpError(404, "file not found")
        return 200, f
    return 200, {"files": store.list_files(pid)}


@route("PUT", "/api/files")
def put_file(req):
    body = req.json()
    pid = body.get("project") or C.INBOX
    _need_doc("project", pid)
    meta = T.save_file(pid, body.get("path", ""), body.get("content", ""), "ui:canvas")
    return 200, meta


@route("DELETE", "/api/files")
def del_file(req):
    pid = req.q.get("project", C.INBOX)
    path = T.norm_path(req.q.get("path", ""))
    if not store.del_file(pid, path):
        raise HttpError(404, "file not found")
    store.add_action("file.deleted", path, {"path": path}, project_id=pid)
    return 200, {"deleted": path}


# ---------- workflows, runs, python, mcp ----------

@route("POST", "/api/workflows/{id}/run")
def run_workflow(req):
    body = req.json()
    run = E.start_run(req.p["id"], str(body.get("input", "")), body.get("project_id"),
                      provider_key=req.provider_key, background=True)
    return 202, run


@route("GET", "/api/runs/{id}")
def get_run(req):
    return 200, _need_doc("run", req.p["id"])


@route("POST", "/api/python")
def run_python(req):
    body = req.json()
    pid = body.get("project_id") or C.INBOX
    project = _need_doc("project", pid)
    return 200, T.run_python(str(body.get("code", "")), pid, project.get("scope") or [])


@route("POST", "/api/mcp/{id}/probe")
def probe_mcp(req):
    srv = _need_doc("mcp", req.p["id"])
    return 200, MCP.probe(srv)


@route("GET", "/api/backup")
def backup(req):
    return 200, store.export_all()


@route("POST", "/api/restore")
def restore(req):
    store.restore(req.json())
    return 200, {"restored": True}


# ---------- dispatch ----------

def _authed(handler):
    if not C.TOKEN:
        return True
    auth = handler.headers.get("Authorization", "")
    given = auth[7:] if auth.startswith("Bearer ") else handler.headers.get("X-NX-Token", "")
    return hmac.compare_digest(given.encode("utf-8"), C.TOKEN.encode("utf-8"))


STATIC_OK = re.compile(r"^/(css|js)/[A-Za-z0-9_\-./]+$")


class Handler(BaseHTTPRequestHandler):
    server_version = "Nexus/2"

    def log_message(self, fmt, *args):
        if os.environ.get("NX_LOG") == "1":
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _send_json(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, rel):
        full = (C.ROOT / rel).resolve()
        if not str(full).startswith(str(C.ROOT)) or not full.is_file():
            return self._send_json(404, {"error": "not found"})
        data = full.read_bytes()
        ctype = mimetypes.guess_type(str(full))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _dispatch(self, method):
        url = urlparse(self.path)
        path = url.path
        if not path.startswith("/api/"):
            if method != "GET":
                return self._send_json(405, {"error": "method not allowed"})
            if path in ("/", "/index.html"):
                return self._send_file("index.html")
            if path == "/favicon.svg" or STATIC_OK.match(path):
                return self._send_file(path.lstrip("/"))
            return self._send_json(404, {"error": "not found"})

        matched, params, path_hit = None, None, False
        for m, rx, fn in ROUTES:
            mt = rx.match(path)
            if mt:
                path_hit = True
                if m == method:
                    matched, params = fn, mt.groupdict()
                    break
        if not matched:
            return self._send_json(405 if path_hit else 404, {"error": "method not allowed" if path_hit else "not found"})
        if not _authed(self):
            return self._send_json(401, {"error": "token required"})

        length = int(self.headers.get("Content-Length") or 0)
        if length > C.BODY_CAP:
            return self._send_json(413, {"error": "body too large"})
        raw = self.rfile.read(length) if length else b""
        req = Req(self, params, url.query, raw)
        try:
            status, payload = matched(req)
            if status is None:       # streamed response already written
                return
            self._send_json(status, payload)
        except HttpError as e:
            self._send_json(e.status, {"error": str(e)})
        except (store.NotFound,) as e:
            self._send_json(404, {"error": str(e)})
        except (store.Invalid, E.EngineError, T.ToolError) as e:
            self._send_json(400 if not isinstance(e, E.Busy) else 409, {"error": str(e)})
        except P.ProviderError as e:
            self._send_json(502, {"error": str(e)})
        except Exception as e:
            sys.stderr.write(traceback.format_exc())
            self._send_json(500, {"error": f"{type(e).__name__}: {e}"})


TOKEN_FILE_MIN = 32


def _load_or_create_token():
    """Token for the API. NX_TOKEN wins; otherwise DATA_DIR/token (0600) is read, or created on first start."""
    path = C.DATA_DIR / "token"
    C.DATA_DIR.mkdir(parents=True, exist_ok=True)
    if path.exists():
        tok = path.read_text(encoding="utf-8").strip()
        if len(tok) >= TOKEN_FILE_MIN:
            return tok
    tok = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(tok + "\n")
    os.chmod(path, 0o600)
    return tok


def main():
    store.init()
    if not C.TOKEN and not C.ALLOW_OPEN:
        C.TOKEN = _load_or_create_token()
        print(f"[nexus] auth on: token in {C.DATA_DIR / 'token'} (set NX_TOKEN to override)", file=sys.stderr)
    elif not C.TOKEN:
        print("[nexus] NX_ALLOW_OPEN=1: API is open to anyone who can reach this port. Local use only.",
              file=sys.stderr)
    httpd = ThreadingHTTPServer((C.HOST, C.PORT), Handler)
    print(f"[nexus] v{C.VERSION} on http://{C.HOST}:{C.PORT}  db={store.db_path()}", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
