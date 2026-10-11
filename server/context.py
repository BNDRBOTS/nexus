"""Context builder. One function produces the exact message list sent to the model
and a snapshot (block sizes, dropped history, scope, retrieval, hash). Snapshot hash is stored on
the assistant message, so the record ties each reply to the context that produced it.

Block order (fixed, machine-stable):
  system -> mode -> role -> skills -> project -> chat_prompt -> pins -> sources -> history -> reminder
"""
import hashlib
import json

from . import config as C
from . import tools as T

PINS_MAX = 5
PINS_CHARS = 6000
SOURCES_CHARS = 6000


def _project_block(project):
    st = project.get("status") or {}
    lines = [f"Project: {project.get('name', '')}"]
    if project.get("instructions"):
        lines.append("Instructions:\n" + project["instructions"])
    lines.append(f"Status: {st.get('state', 'active')}")
    if st.get("note"):
        lines.append(f"Last verified progress: {st['note']}")
    if st.get("next_step"):
        lines.append(f"Next step: {st['next_step']}")
    scope = project.get("scope") or []
    if scope:
        lines.append("Source Scope (only these files may be used as sources):\n" + "\n".join(f"- {p}" for p in scope))
    return "\n".join(lines)


def build(chat, project, settings, mode, role, query, skills=None):
    budget = int(settings.get("context_chars") or C.DEFAULT_SETTINGS["context_chars"])
    blocks = []

    def add(tag, role_name, content):
        if content:
            blocks.append({"tag": tag, "role": role_name, "content": content})

    add("system", "system", settings["system_prompt"])
    add("mode", "system", f"Mode: {mode['label']}. {mode['prompt']}")
    if role:
        add("role", "system", f"Role: {role.get('name', '')} - {role.get('title', '')}\n{role.get('system_prompt', '')}".strip())
    for sk in skills or []:
        add("skill", "system", f"Skill: {sk.get('name', '')}\n{sk.get('instructions', '')}")
    if project:
        add("project", "system", _project_block(project))
    if chat.get("chat_prompt"):
        add("chat_prompt", "system", chat["chat_prompt"])

    msgs = chat.get("messages") or []
    pins = [m for m in msgs if m.get("pinned")][-PINS_MAX:]
    if pins:
        body, used = [], 0
        for m in pins:
            line = f"- ({m['role']}) {m['content']}"[:1500]
            if used + len(line) > PINS_CHARS:
                break
            body.append(line)
            used += len(line)
        add("pins", "system", "Pinned anchors (keep these facts in view):\n" + "\n".join(body))

    scope = (project or {}).get("scope") or []
    pid = (project or {}).get("id", C.INBOX)
    hits = T.retrieve(pid, scope, query, k=4) if scope else []
    if hits:
        body, used, kept = [], 0, []
        for h in hits:
            part = f"[{h['path']}#{h['idx']}]\n{h['text']}"
            if used + len(part) > SOURCES_CHARS:
                break
            body.append(part)
            used += len(part)
            kept.append(f"{h['path']}#{h['idx']}")
        add("sources", "system", "Retrieved from Source Scope:\n" + "\n\n".join(body))
    else:
        kept = []

    tail = []
    if chat.get("reminder"):
        tail.append({"tag": "reminder", "role": "system", "content": chat["reminder"]})

    fixed = sum(len(b["content"]) for b in blocks) + sum(len(t["content"]) for t in tail)
    remaining = budget - fixed
    history = []
    for m in reversed(msgs):
        if m.get("role") not in ("user", "assistant") or not m.get("content"):
            continue
        c = m["content"]
        if history and remaining - len(c) < 0:
            break
        rc = (m.get("meta") or {}).get("reasoning") if m["role"] == "assistant" else None
        history.insert(0, {"tag": "history", "role": m["role"], "content": c, "id": m["id"], "reasoning": rc})
        remaining -= len(c)
    dropped = sum(1 for m in msgs if m.get("role") in ("user", "assistant") and m.get("content")) - len(history)

    api_messages = [{"role": b["role"], "content": b["content"]} for b in blocks]
    for h in history:
        msg = {"role": h["role"], "content": h["content"]}
        if h.get("reasoning"):
            msg["reasoning_content"] = h["reasoning"]   # sent only when the request carries tools (provider.py)
        api_messages.append(msg)
    api_messages += [{"role": t["role"], "content": t["content"]} for t in tail]

    total = sum(len(m["content"]) for m in api_messages)
    digest = hashlib.sha256(json.dumps(api_messages, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    snapshot = {
        "hash": digest[:16],
        "blocks": [{"tag": b["tag"], "chars": len(b["content"])} for b in blocks],
        "history": {"included": len(history), "dropped": dropped},
        "reminder": bool(tail),
        "chars": total,
        "tokens_est": total // 4,
        "budget_chars": budget,
        "scope": scope,
        "retrieved": kept,
        "tools": [],
    }
    return api_messages, snapshot
