"""Tool registry. Built-in tools + MCP tools, filtered by mode groups and chat toggles.
Every executed tool returns {ok, output, summary}; errors are returned to the model, never raised.
Every side effect (file write, status change) appends to the action record.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

from . import config as C
from . import mcp
from . import plugins as PL
from . import store


class ToolError(Exception):
    pass


SAFE_PATH = re.compile(r"^[A-Za-z0-9._\- ]+(/[A-Za-z0-9._\- ]+)*$")
TEXT_READ_CAP = 256_000


def norm_path(p):
    p = str(p or "").strip().replace("\\", "/").lstrip("/")
    if not p or len(p) > 200 or not SAFE_PATH.match(p):
        raise ToolError(f"invalid path: {p!r}")
    if any(seg in ("", ".", "..") for seg in p.split("/")):
        raise ToolError(f"invalid path: {p!r}")
    return p


def save_file(project_id, path, content, source, chat_id=None):
    """Single write path for workspace files. Records file.saved."""
    path = norm_path(path)
    content = "" if content is None else str(content)
    if len(content) > C.FILE_CAP:
        raise ToolError(f"file exceeds {C.FILE_CAP} chars")
    meta = store.put_file(project_id, path, content)
    store.add_action("file.saved", path, {"path": path, "bytes": meta["bytes"], "sha": meta["sha"],
                                          "source": source}, project_id=project_id, chat_id=chat_id)
    return meta


def _fn(name, desc, props=None, req=None):
    return {"type": "function", "function": {"name": name, "description": desc,
                                             "parameters": {"type": "object", "properties": props or {},
                                                            "required": req or []}}}


BUILTIN = {
    "workspace_list": _fn("workspace_list", "List files in the current project workspace."),
    "workspace_read": _fn("workspace_read", "Read one workspace file.",
                          {"path": {"type": "string"}}, ["path"]),
    "workspace_write": _fn("workspace_write",
                           "Create or overwrite a workspace file. Use for every deliverable that must persist.",
                           {"path": {"type": "string", "description": "relative path, e.g. notes/plan.md"},
                            "content": {"type": "string"}}, ["path", "content"]),
    "search_sources": _fn("search_sources",
                          "Keyword search over the project's Source Scope only. Returns file path and excerpt.",
                          {"query": {"type": "string"}}, ["query"]),
    "project_set_status": _fn("project_set_status",
                              "Record the verified project state. Call only with facts confirmed by tool results.",
                              {"state": {"type": "string", "enum": ["active", "paused", "done"]},
                               "note": {"type": "string"}, "next_step": {"type": "string"}},
                              ["state", "note", "next_step"]),
    "python_run": _fn("python_run",
                      "Run Python 3 in a temp directory holding the Source Scope files. "
                      "New or changed text files are saved to the workspace.",
                      {"code": {"type": "string"}}, ["code"]),
}

GROUP_TOOLS = {
    "workspace": ["workspace_list", "workspace_read", "workspace_write"],
    "status": ["project_set_status"],
    "sources": ["search_sources"],
    "python": ["python_run"],
}


def _mcp_name(server_id, tool):
    return re.sub(r"[^A-Za-z0-9_]", "_", f"mcp_{server_id}_{tool}")[:64]


def specs_for(chat, mode, settings):
    """Returns (openai tool specs, routes). routes: name -> ("builtin", n) | ("mcp", server_id, tool)."""
    groups = mode["tool_groups"] if chat.get("tool_groups") is None else chat["tool_groups"]
    specs, routes = [], {}
    for g in groups:
        if g == "mcp":
            for srv in store.list_docs("mcp"):
                if not srv.get("enabled"):
                    continue
                try:
                    listed = mcp.list_tools(srv)
                except mcp.McpError:
                    continue
                for t in listed:
                    name = _mcp_name(srv["id"], t.get("name", "tool"))
                    schema = t.get("inputSchema") if isinstance(t.get("inputSchema"), dict) else {
                        "type": "object", "properties": {}}
                    specs.append({"type": "function", "function": {
                        "name": name, "description": f"[{srv['name']}] {t.get('description', '')}"[:1000],
                        "parameters": schema}})
                    routes[name] = ("mcp", srv["id"], t["name"])
            continue
        if g == "plugins":
            p_specs, p_routes = PL.specs()
            specs.extend(p_specs)
            routes.update(p_routes)
            continue
        if g == "python" and not C.PYTHON_ENABLED:
            continue
        for n in GROUP_TOOLS.get(g, []):
            specs.append(BUILTIN[n])
            routes[n] = ("builtin", n)
    return specs, routes


# ---------- retrieval (Source Scope only) ----------

def _chunks(path, content, size=900):
    out, buf = [], ""
    for para in re.split(r"\n\s*\n", content):
        if buf and len(buf) + len(para) > size:
            out.append(buf)
            buf = ""
        buf = para if not buf else buf + "\n\n" + para
    if buf:
        out.append(buf)
    return [{"path": path, "idx": i, "text": t} for i, t in enumerate(out)]


def retrieve(project_id, scope, query, k=4):
    terms = list(dict.fromkeys(re.findall(r"[a-z0-9]{3,}", (query or "").lower())))[:20]
    if not terms or not scope:
        return []
    scored = []
    for path in scope:
        f = store.get_file(project_id, path)
        if not f:
            continue
        for ch in _chunks(path, f["content"]):
            low = ch["text"].lower()
            score = sum(min(low.count(t), 5) for t in terms) + sum(3 for t in terms if t in path.lower())
            if score > 0:
                ch["score"] = score
                scored.append(ch)
    scored.sort(key=lambda c: (-c["score"], c["path"], c["idx"]))
    return scored[:k]


# ---------- python ----------

def _walk(root):
    seen = {}
    for dp, _, files in os.walk(root):
        for fn in files:
            full = os.path.join(dp, fn)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            st = os.stat(full)
            seen[rel] = (st.st_size, st.st_mtime_ns)
    return seen


_PY_BOOT = (
    "import resource, sys\n"
    "_t = int(sys.argv[1])\n"
    "for _lim, _val in ((resource.RLIMIT_CPU, _t + 5), (resource.RLIMIT_AS, 768 << 20),\n"
    "                   (resource.RLIMIT_FSIZE, 10 << 20), (resource.RLIMIT_NOFILE, 64)):\n"
    "    try:\n"
    "        resource.setrlimit(_lim, (_val, _val))\n"
    "    except (ValueError, OSError):\n"
    "        pass\n"
    "exec(compile(sys.stdin.read(), '<nexus>', 'exec'), {'__name__': '__main__'})\n"
)


def run_python(code, project_id, scope, chat_id=None, timeout=20):
    if not C.PYTHON_ENABLED:
        raise ToolError("python disabled (start server with NX_ENABLE_PYTHON=1)")
    if not code or len(code) > 20000:
        raise ToolError("code empty or longer than 20000 chars")
    with tempfile.TemporaryDirectory(prefix="nx-") as d:
        for path in scope:
            f = store.get_file(project_id, path)
            if f:
                full = os.path.join(d, path)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, "w", encoding="utf-8") as fh:
                    fh.write(f["content"])
        before = _walk(d)
        try:
            # Minimal env (no NX_TOKEN / NX_GATEWAY_KEY), isolated mode, rlimits applied in-process.
            # NOT a filesystem sandbox: code can still read host files the server user can read.
            proc = subprocess.run([sys.executable, "-I", "-c", _PY_BOOT, str(int(timeout))], input=code, cwd=d,
                                  capture_output=True, text=True, timeout=timeout + 5,
                                  env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONIOENCODING": "utf-8"})
            rc, out, err = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired:
            rc, out, err = -1, "", f"timed out after {timeout}s"
        after = _walk(d)
        saved = []
        for rel, sig in after.items():
            if before.get(rel) == sig or rel.startswith("__pycache__"):
                continue
            try:
                norm_path(rel)
                with open(os.path.join(d, rel), "r", encoding="utf-8") as fh:
                    text = fh.read(TEXT_READ_CAP)
            except (ValueError, OSError, ToolError, UnicodeDecodeError):
                continue
            save_file(project_id, rel, text, "python_run", chat_id)
            saved.append(rel)
    text = f"[exit {rc}]\n{out[:4000]}"
    if err.strip():
        text += f"\nstderr:\n{err[:2000]}"
    if saved:
        text += f"\nsaved: {', '.join(saved)}"
    return {"ok": rc == 0, "output": text[:C.TOOL_OUTPUT_CAP], "summary": f"python exit {rc}, saved {len(saved)}"}


# ---------- built-in implementations ----------

def _ws_list(args, ctx):
    files = store.list_files(ctx["project_id"])
    lines = [f"{f['path']} ({f['bytes']} chars)" for f in files] or ["(empty workspace)"]
    return {"ok": True, "output": "\n".join(lines), "summary": f"{len(files)} files"}


def _ws_read(args, ctx):
    path = norm_path(args.get("path"))
    f = store.get_file(ctx["project_id"], path)
    if not f:
        raise ToolError(f"not found: {path}")
    return {"ok": True, "output": f["content"][:C.TOOL_OUTPUT_CAP], "summary": f"read {path}"}


def _ws_write(args, ctx):
    path = norm_path(args.get("path"))
    meta = save_file(ctx["project_id"], path, args.get("content", ""), "tool:workspace_write", ctx.get("chat_id"))
    return {"ok": True, "output": f"saved {path} ({meta['bytes']} chars, sha {meta['sha'][:12]})",
            "summary": f"saved {path}"}


def _search(args, ctx):
    query = str(args.get("query", ""))
    scope = ctx["project"].get("scope") or []
    if not scope:
        return {"ok": True, "output": "Source Scope is empty for this project.", "summary": "scope empty"}
    hits = retrieve(ctx["project_id"], scope, query, k=5)
    if not hits:
        return {"ok": True, "output": "no matches in scope", "summary": "0 hits"}
    text = "\n\n".join(f"[{h['path']}#{h['idx']}]\n{h['text']}" for h in hits)
    return {"ok": True, "output": text[:C.TOOL_OUTPUT_CAP], "summary": f"{len(hits)} hits"}


def _status(args, ctx):
    state = args.get("state")
    if state not in ("active", "paused", "done"):
        raise ToolError("state must be active|paused|done")
    project = store.get_doc("project", ctx["project_id"])
    status = {"state": state, "note": str(args.get("note", ""))[:600],
              "next_step": str(args.get("next_step", ""))[:300], "updated": store.now()}
    project["status"] = status
    store.put_doc("project", ctx["project_id"], project)
    store.add_action("status.updated", ctx["project_id"], status, project_id=ctx["project_id"],
                     chat_id=ctx.get("chat_id"))
    return {"ok": True, "output": f"status recorded: {state}", "summary": f"status {state}"}


def _python(args, ctx):
    return run_python(str(args.get("code", "")), ctx["project_id"], ctx["project"].get("scope") or [],
                      ctx.get("chat_id"))


_IMPL = {
    "workspace_list": _ws_list, "workspace_read": _ws_read, "workspace_write": _ws_write,
    "search_sources": _search, "project_set_status": _status, "python_run": _python,
}


def execute(name, args, routes, ctx):
    try:
        route = routes.get(name)
        if not route:
            raise ToolError(f"tool not enabled for this chat: {name}")
        if route[0] == "plugin":
            plugin = store.get_doc("plugin", route[1])
            if not plugin:
                raise ToolError("plugin missing")
            text = PL.call(plugin, args, ctx.get("project_id"))
            return {"ok": True, "output": text, "summary": f"plugin {plugin['name']}"}
        if route[0] == "mcp":
            srv = store.get_doc("mcp", route[1])
            if not srv:
                raise ToolError("mcp server missing")
            text = mcp.call(srv, route[2], args)
            return {"ok": True, "output": text[:C.TOOL_OUTPUT_CAP], "summary": f"mcp {route[2]}"}
        return _IMPL[route[1]](args, ctx)
    except Exception as e:  # any failure is a tool result, not a crash
        return {"ok": False, "output": f"error: {e}"[:2000], "summary": f"{name} failed: {str(e)[:160]}"}


def parse_args(raw):
    if isinstance(raw, dict):
        return raw
    try:
        val = json.loads(raw or "{}")
        return val if isinstance(val, dict) else {}
    except json.JSONDecodeError:
        return {"__raw__": raw}
