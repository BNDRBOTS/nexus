"""Store. SQLite file by default (stdlib only); Postgres when NX_DB_URL is set (psycopg, optional).

Tables:
  doc(kind, id, data JSON, updated)   projects, chats, roles, skills, workflows, runs, mcp, setting
  file(project_id, path, content, sha, bytes, updated)   project workspace
  action(id, ts, project_id, chat_id, type, ref, payload JSON)   append-only record (triggers enforce)
"""
import hashlib
import json
import pathlib
import re
import secrets
import sqlite3
import threading
import time

from . import config as C


class NotFound(Exception):
    pass


class Invalid(Exception):
    pass


_lock = threading.RLock()
_conn = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS doc(
  kind TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL, updated REAL NOT NULL,
  PRIMARY KEY(kind, id));
CREATE TABLE IF NOT EXISTS file(
  project_id TEXT NOT NULL, path TEXT NOT NULL, content TEXT NOT NULL,
  sha TEXT NOT NULL, bytes INTEGER NOT NULL, updated REAL NOT NULL,
  PRIMARY KEY(project_id, path));
CREATE TABLE IF NOT EXISTS action(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, project_id TEXT, chat_id TEXT,
  type TEXT NOT NULL, ref TEXT, payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS action_project ON action(project_id, id);
CREATE INDEX IF NOT EXISTS action_chat ON action(chat_id, id);
"""

TRIGGERS = [
    "CREATE TRIGGER IF NOT EXISTS action_no_update BEFORE UPDATE ON action "
    "BEGIN SELECT RAISE(ABORT, 'action record is append-only'); END;",
    "CREATE TRIGGER IF NOT EXISTS action_no_delete BEFORE DELETE ON action "
    "BEGIN SELECT RAISE(ABORT, 'action record is append-only'); END;",
]
DROP_TRIGGERS = ["DROP TRIGGER IF EXISTS action_no_update", "DROP TRIGGER IF EXISTS action_no_delete"]

PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS doc(
  kind TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL, updated DOUBLE PRECISION NOT NULL,
  PRIMARY KEY(kind, id));
CREATE TABLE IF NOT EXISTS file(
  project_id TEXT NOT NULL, path TEXT NOT NULL, content TEXT NOT NULL,
  sha TEXT NOT NULL, bytes BIGINT NOT NULL, updated DOUBLE PRECISION NOT NULL,
  PRIMARY KEY(project_id, path));
CREATE TABLE IF NOT EXISTS action(
  id BIGSERIAL PRIMARY KEY, ts DOUBLE PRECISION NOT NULL, project_id TEXT, chat_id TEXT,
  type TEXT NOT NULL, ref TEXT, payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS action_project ON action(project_id, id);
CREATE INDEX IF NOT EXISTS action_chat ON action(chat_id, id);
"""

# Postgres: function + drop/create pairs (CREATE TRIGGER has no IF NOT EXISTS). Each statement is run alone.
PG_TRIGGERS = [
    "CREATE OR REPLACE FUNCTION nx_action_immutable() RETURNS trigger AS $$ BEGIN "
    "RAISE EXCEPTION 'action record is append-only'; END; $$ LANGUAGE plpgsql",
    "DROP TRIGGER IF EXISTS action_no_update ON action",
    "CREATE TRIGGER action_no_update BEFORE UPDATE ON action FOR EACH ROW EXECUTE FUNCTION nx_action_immutable()",
    "DROP TRIGGER IF EXISTS action_no_delete ON action",
    "CREATE TRIGGER action_no_delete BEFORE DELETE ON action FOR EACH ROW EXECUTE FUNCTION nx_action_immutable()",
]
PG_DROP_TRIGGERS = ["DROP TRIGGER IF EXISTS action_no_update ON action",
                    "DROP TRIGGER IF EXISTS action_no_delete ON action"]


class _Db:
    """One connection. Code writes SQL with `?`; Postgres gets `%s`. Rows support `row["col"]` on both backends."""

    def __init__(self, conn, pg):
        self.conn = conn
        self.pg = pg

    def execute(self, sql, args=()):
        if self.pg:
            sql = sql.replace("?", "%s")
            return self.conn.execute(sql, tuple(args) if args else None)
        return self.conn.execute(sql, tuple(args))

    def triggers(self):
        return PG_TRIGGERS if self.pg else TRIGGERS

    def drop_triggers(self):
        return PG_DROP_TRIGGERS if self.pg else DROP_TRIGGERS


def now():
    return time.time()


def new_id(prefix):
    return f"{prefix}_{secrets.token_hex(5)}"


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def init(path=None):
    global _conn
    if C.DB_URL:
        if not C.DB_URL.startswith(("postgres://", "postgresql://")):
            raise RuntimeError("NX_DB_URL must start with postgresql:// (or postgres://)")
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as e:
            raise RuntimeError("NX_DB_URL is set but psycopg is missing: pip install 'psycopg[binary]'") from e
        _conn = _Db(psycopg.connect(C.DB_URL, autocommit=True, row_factory=dict_row), pg=True)
        for stmt in PG_SCHEMA.split(";"):
            if stmt.strip():
                _conn.execute(stmt)
    else:
        p = pathlib.Path(path or C.DB_PATH)
        p.parent.mkdir(parents=True, exist_ok=True)
        raw = sqlite3.connect(str(p), check_same_thread=False, isolation_level=None)
        raw.row_factory = sqlite3.Row
        raw.execute("PRAGMA journal_mode=WAL")
        raw.executescript(SCHEMA)
        _conn = _Db(raw, pg=False)
    for t in _conn.triggers():
        _conn.execute(t)
    with _lock:
        if not get_doc("project", C.INBOX):
            put_doc("project", C.INBOX, {"name": "Inbox", "instructions": "", "scope": [],
                                         "status": {"state": "active", "note": "", "next_step": ""}})
        for r in C.DEFAULT_ROLES:
            if not get_doc("role", r["id"]):
                put_doc("role", r["id"], dict(r))
        if not get_doc("setting", "app"):
            put_doc("setting", "app", json.loads(json.dumps(C.DEFAULT_SETTINGS)))
    return _conn


def db_path():
    if _conn is not None and _conn.pg:
        return re.sub(r"://([^:/@]+):[^@]*@", r"://\1:***@", C.DB_URL)   # never expose the password
    return str(C.DB_PATH)


# ---------- docs ----------

def get_doc(kind, doc_id):
    with _lock:
        row = _conn.execute("SELECT data FROM doc WHERE kind=? AND id=?", (kind, doc_id)).fetchone()
    return json.loads(row["data"]) if row else None


def list_docs(kind):
    with _lock:
        rows = _conn.execute("SELECT data FROM doc WHERE kind=? ORDER BY updated DESC", (kind,)).fetchall()
    return [json.loads(r["data"]) for r in rows]


def put_doc(kind, doc_id, data):
    data = dict(data)
    data["id"] = doc_id
    data.setdefault("created", now())
    data["updated"] = now()
    with _lock:
        _conn.execute(
            "INSERT INTO doc(kind,id,data,updated) VALUES(?,?,?,?) "
            "ON CONFLICT(kind,id) DO UPDATE SET data=excluded.data, updated=excluded.updated",
            (kind, doc_id, json.dumps(data, ensure_ascii=False), data["updated"]))
    return data


def del_doc(kind, doc_id):
    with _lock:
        _conn.execute("DELETE FROM doc WHERE kind=? AND id=?", (kind, doc_id))


def get_chat(chat_id):
    chat = get_doc("chat", chat_id)
    if not chat:
        raise NotFound(f"chat {chat_id}")
    return chat


def put_chat(chat):
    return put_doc("chat", chat["id"], chat)


def get_settings():
    base = json.loads(json.dumps(C.DEFAULT_SETTINGS))
    stored = get_doc("setting", "app") or {}
    for k, v in stored.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k].update(v)
        else:
            base[k] = v
    # Migration: the first build stored the retired DeepSeek defaults. Rewrite only that exact pair.
    sp = (stored.get("provider") or {})
    if sp.get("base_url") == "https://api.deepseek.com/v1" and sp.get("model") == "deepseek-chat":
        base["provider"].update({"base_url": C.DEFAULT_SETTINGS["provider"]["base_url"],
                                 "model": C.DEFAULT_SETTINGS["provider"]["model"], "preset": "deepseek"})
    return base


# ---------- workspace files ----------

def put_file(project_id, path, content):
    digest = sha(content)
    size = len(content)
    t = now()
    with _lock:
        _conn.execute(
            "INSERT INTO file(project_id,path,content,sha,bytes,updated) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(project_id,path) DO UPDATE SET content=excluded.content, sha=excluded.sha, "
            "bytes=excluded.bytes, updated=excluded.updated",
            (project_id, path, content, digest, size, t))
    return {"path": path, "sha": digest, "bytes": size, "updated": t}


def get_file(project_id, path):
    with _lock:
        row = _conn.execute("SELECT * FROM file WHERE project_id=? AND path=?", (project_id, path)).fetchone()
    return dict(row) if row else None


def list_files(project_id):
    with _lock:
        rows = _conn.execute(
            "SELECT path, sha, bytes, updated FROM file WHERE project_id=? ORDER BY path", (project_id,)).fetchall()
    return [dict(r) for r in rows]


def del_file(project_id, path):
    with _lock:
        cur = _conn.execute("DELETE FROM file WHERE project_id=? AND path=?", (project_id, path))
    return cur.rowcount > 0


# ---------- action record (append-only) ----------

def add_action(atype, ref=None, payload=None, project_id=None, chat_id=None):
    sql = "INSERT INTO action(ts,project_id,chat_id,type,ref,payload) VALUES(?,?,?,?,?,?)"
    args = (now(), project_id, chat_id, atype, ref, json.dumps(payload or {}, ensure_ascii=False))
    with _lock:
        if _conn.pg:
            return _conn.execute(sql + " RETURNING id", args).fetchone()["id"]
        return _conn.execute(sql, args).lastrowid


def list_actions(project_id=None, chat_id=None, limit=100):
    where, args = [], []
    if project_id:
        where.append("project_id=?")
        args.append(project_id)
    if chat_id:
        where.append("chat_id=?")
        args.append(chat_id)
    sql = "SELECT * FROM action"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(max(1, min(int(limit), 1000)))
    with _lock:
        rows = _conn.execute(sql, args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d["payload"])
        out.append(d)
    return out


def counts():
    with _lock:
        return {
            "docs": _conn.execute("SELECT COUNT(*) AS n FROM doc").fetchone()["n"],
            "files": _conn.execute("SELECT COUNT(*) AS n FROM file").fetchone()["n"],
            "actions": _conn.execute("SELECT COUNT(*) AS n FROM action").fetchone()["n"],
        }


# ---------- backup / restore ----------

def export_all():
    with _lock:
        docs = [dict(r) for r in _conn.execute("SELECT kind,id,data,updated FROM doc")]
        files = [dict(r) for r in _conn.execute("SELECT * FROM file")]
        actions = [dict(r) for r in _conn.execute("SELECT * FROM action ORDER BY id")]
    for d in docs:
        d["data"] = json.loads(d["data"])
    for a in actions:
        a["payload"] = json.loads(a["payload"])
    return {"format": "nexus-backup", "version": 1, "exported_at": now(),
            "docs": docs, "files": files, "actions": actions}


def restore(payload):
    if not isinstance(payload, dict) or payload.get("format") != "nexus-backup":
        raise Invalid("not a nexus-backup payload")
    with _lock:
        _conn.execute("BEGIN")
        try:
            for t in _conn.drop_triggers():
                _conn.execute(t)
            for t in ("doc", "file", "action"):
                _conn.execute(f"DELETE FROM {t}")
            for d in payload.get("docs", []):
                _conn.execute("INSERT INTO doc(kind,id,data,updated) VALUES(?,?,?,?)",
                              (d["kind"], d["id"], json.dumps(d["data"], ensure_ascii=False), d["updated"]))
            for f in payload.get("files", []):
                _conn.execute("INSERT INTO file(project_id,path,content,sha,bytes,updated) VALUES(?,?,?,?,?,?)",
                              (f["project_id"], f["path"], f["content"], f["sha"], f["bytes"], f["updated"]))
            for a in payload.get("actions", []):
                _conn.execute("INSERT INTO action(id,ts,project_id,chat_id,type,ref,payload) VALUES(?,?,?,?,?,?,?)",
                              (a["id"], a["ts"], a.get("project_id"), a.get("chat_id"), a["type"], a.get("ref"),
                               json.dumps(a["payload"], ensure_ascii=False)))
            if _conn.pg:   # explicit ids do not advance the BIGSERIAL sequence
                _conn.execute("SELECT setval(pg_get_serial_sequence('action','id'), "
                              "COALESCE(MAX(id),0)+1, false) FROM action")
            for t in _conn.triggers():
                _conn.execute(t)
            _conn.execute("COMMIT")
        except Exception:
            _conn.execute("ROLLBACK")
            for t in _conn.triggers():
                _conn.execute(t)
            raise
    add_action("backup.restored", None, {"docs": len(payload.get("docs", [])),
                                         "files": len(payload.get("files", []))})
