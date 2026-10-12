#!/usr/bin/env python3
"""Nexus chat-session connector. Stdlib only. Machine-first: every command prints one JSON document to stdout.

Env:
  NX_URL          base URL            (default http://127.0.0.1:8000)
  NX_TOKEN        bearer token        (or NX_TOKEN_FILE, default ./data/token)

Exit codes: 0 ok · 1 server returned an error (JSON error on stderr) · 2 usage / connection error.

Commands:
  project create --name N [--instructions T] [--scope PATH ...]
  project get ID
  chat create --project ID [--mode work|research|code] [--title T]
  chat get ID
  chat send ID (--text T | --stdin)          prints {assistant, context}
  chat context ID                            exact payload the model would receive
  chat save ID MSG_ID PATH                   writes assistant message to project workspace
  file list PROJECT
  file get PROJECT PATH
  file put PROJECT PATH (--text T | --stdin)
  actions [--project ID] [--chat ID] [--limit N]
  status [--probe]
  skill draft --description T
"""
import argparse
import json
import os
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request


class CliError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


def _token():
    tok = os.environ.get("NX_TOKEN", "").strip()
    if tok:
        return tok
    path = pathlib.Path(os.environ.get("NX_TOKEN_FILE", "data/token"))
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def call(method, path, body=None, query=None):
    base = os.environ.get("NX_URL", "http://127.0.0.1:8000").rstrip("/")
    url = base + path
    if query:
        qs = urllib.parse.urlencode({k: v for k, v in query.items() if v not in (None, "")})
        if qs:
            url += "?" + qs
    headers = {"Accept": "application/json"}
    tok = _token()
    if tok:
        headers["Authorization"] = "Bearer " + tok
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            detail = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            detail = raw.decode("utf-8", "replace")[:500]
        raise CliError(1, json.dumps({"status": e.code, "error": detail}))
    except urllib.error.URLError as e:
        raise CliError(2, f"connection failed: {e.reason}")
    return json.loads(raw) if raw else None


def _text(args):
    if args.stdin:
        return sys.stdin.read()
    if args.text is None:
        raise CliError(2, "--text or --stdin required")
    return args.text


def run(args):
    c, a = args.cmd, args
    if c == "project":
        if a.action == "create":
            body = {"name": a.name, "instructions": a.instructions or "",
                    "scope": a.scope or [], "status": {"state": "active", "note": "", "next_step": "", "updated": ""}}
            return call("POST", "/api/doc/project", body)
        return call("GET", f"/api/projects/{a.id}")
    if c == "chat":
        if a.action == "create":
            return call("POST", "/api/chats", {"project_id": a.project, "mode": a.mode, "title": a.title})
        if a.action == "get":
            return call("GET", f"/api/chats/{a.id}")
        if a.action == "send":
            return call("POST", f"/api/chats/{a.id}/messages", {"content": _text(a)})
        if a.action == "context":
            return call("GET", f"/api/chats/{a.id}/context")
        if a.action == "save":
            return call("POST", f"/api/chats/{a.id}/messages/{a.msg}/save", {"path": a.path})
    if c == "file":
        if a.action == "list":
            return call("GET", "/api/files", query={"project": a.project})
        if a.action == "get":
            return call("GET", "/api/files", query={"project": a.project, "path": a.path})
        if a.action == "put":
            return call("PUT", "/api/files", {"project": a.project, "path": a.path, "content": _text(a)})
    if c == "actions":
        return call("GET", "/api/actions", query={"project": a.project, "chat": a.chat, "limit": a.limit})
    if c == "status":
        return call("GET", "/api/status", query={"probe": "1" if a.probe else ""})
    if c == "skill":
        return call("POST", "/api/skills/draft", {"description": a.description})
    raise CliError(2, "unknown command")


def parser():
    p = argparse.ArgumentParser(prog="nexus", description="Nexus connector (JSON output)")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("project")
    pr.add_argument("action", choices=["create", "get"])
    pr.add_argument("id", nargs="?")
    pr.add_argument("--name")
    pr.add_argument("--instructions")
    pr.add_argument("--scope", action="append")

    ch = sub.add_parser("chat")
    ch.add_argument("action", choices=["create", "get", "send", "context", "save"])
    ch.add_argument("id", nargs="?")
    ch.add_argument("msg", nargs="?")
    ch.add_argument("path", nargs="?")
    ch.add_argument("--project")
    ch.add_argument("--mode", default="work", choices=["work", "research", "code"])
    ch.add_argument("--title")
    ch.add_argument("--text")
    ch.add_argument("--stdin", action="store_true")

    fl = sub.add_parser("file")
    fl.add_argument("action", choices=["list", "get", "put"])
    fl.add_argument("project")
    fl.add_argument("path", nargs="?")
    fl.add_argument("--text")
    fl.add_argument("--stdin", action="store_true")

    ac = sub.add_parser("actions")
    ac.add_argument("--project")
    ac.add_argument("--chat")
    ac.add_argument("--limit", type=int, default=50)

    st = sub.add_parser("status")
    st.add_argument("--probe", action="store_true")

    sk = sub.add_parser("skill")
    sk.add_argument("action", choices=["draft"])
    sk.add_argument("--description", required=True)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.cmd == "project" and args.action == "create" and not args.name:
            raise CliError(2, "--name required")
        if args.cmd == "project" and args.action == "get" and not args.id:
            raise CliError(2, "project id required")
        if args.cmd == "chat" and args.action == "create" and not args.project:
            raise CliError(2, "--project required")
        if args.cmd == "chat" and args.action in ("get", "send", "context", "save") and not args.id:
            raise CliError(2, "chat id required")
        if args.cmd == "chat" and args.action == "save" and not (args.msg and args.path):
            raise CliError(2, "usage: chat save ID MSG_ID PATH")
        if args.cmd == "file" and args.action in ("get", "put") and not args.path:
            raise CliError(2, "file path required")
        result = run(args)
    except CliError as e:
        if e.code == 1:
            print(str(e), file=sys.stderr)
        else:
            print(json.dumps({"error": str(e)}), file=sys.stderr)
        return e.code
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
