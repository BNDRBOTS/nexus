"""Engine: turns, tool loop, regenerate, workflows. The only place that combines provider + tools + store.

Loop: open project -> build context -> stream model -> execute tools -> save message -> record actions.
Invariant: an action record entry exists for every saved message, executed tool, file write, status change.
"""
import hashlib
import threading
import time

from . import config as C
from . import context as X
from . import provider as P
from . import store
from . import tools as T


class EngineError(Exception):
    pass


class Busy(EngineError):
    pass


_busy = set()
_busy_lock = threading.Lock()


class _ChatLock:
    def __init__(self, chat_id):
        self.chat_id = chat_id

    def __enter__(self):
        with _busy_lock:
            if self.chat_id in _busy:
                raise Busy(f"chat {self.chat_id} is processing another turn")
            _busy.add(self.chat_id)

    def __exit__(self, *a):
        with _busy_lock:
            _busy.discard(self.chat_id)


def _h(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def _mode(chat):
    return C.MODES.get(chat.get("mode")) or C.MODES["work"]


def _project(chat):
    p = store.get_doc("project", chat.get("project_id") or C.INBOX)
    if p is None:
        p = store.get_doc("project", C.INBOX)
    return p


def _role(chat):
    rid = chat.get("role_id")
    return store.get_doc("role", rid) if rid else None


def _skills(chat):
    return [s for s in (store.get_doc("skill", sid) for sid in chat.get("skill_ids") or []) if s]


def new_message(role, content, meta=None):
    return {"id": store.new_id("m"), "role": role, "content": content, "ts": store.now(),
            "pinned": False, "meta": meta or {}}


def _complete(cfg, messages, specs, routes, ctx, temperature, emit=None):
    """Tool loop. Returns (final_text, tool_log, rounds, reasoning, meta).
    reasoning = native reasoning of all rounds, joined (kept separate from content).
    Within a tool loop every round's reasoning_content is sent back (DeepSeek requires it when tools are present)."""
    tool_log, all_reasoning = [], []
    text, meta = "", {}
    for rnd in range(1, C.MAX_ROUNDS + 1):
        final = None
        for kind, val in P.chat_stream(cfg, messages, specs or None, temperature):
            if kind == "delta":
                if emit:
                    emit("delta", {"text": val})
            elif kind == "reasoning":
                if emit:
                    emit("reasoning", {"text": val})
            else:
                final = val
        text, reasoning = final["content"], final["reasoning"]
        if reasoning:
            all_reasoning.append(reasoning)
        calls = final["tool_calls"]
        meta = {"finish_reason": final["finish_reason"], "usage": final["usage"]}
        if not calls:
            if final["finish_reason"] == "length" and not text.strip():
                raise P.ProviderError("reply cut off by max_tokens before any visible content "
                                      "(finish_reason=length); raise the output limit or lower reasoning effort")
            return text, tool_log, rnd, "\n".join(all_reasoning), meta
        msg = {"role": "assistant", "content": text or None, "tool_calls": [
            {"id": c["id"] or store.new_id("call"), "type": "function",
             "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}} for c in calls]}
        if reasoning:
            msg["reasoning_content"] = reasoning
        messages.append(msg)
        for c in calls:
            cid = c["id"] or store.new_id("call")
            args = T.parse_args(c["arguments"])
            res = T.execute(c["name"], args, routes, ctx)
            entry = {"name": c["name"], "ok": res["ok"], "summary": res["summary"]}
            tool_log.append(entry)
            store.add_action("tool.executed", c["name"], {"ok": res["ok"], "summary": res["summary"],
                                                          "args_hash": _h(c["arguments"]),
                                                          "output_chars": len(res["output"])},
                             project_id=ctx["project_id"], chat_id=ctx.get("chat_id"))
            if emit:
                emit("tool", entry)
            messages.append({"role": "tool", "tool_call_id": cid, "content": res["output"][:C.TOOL_OUTPUT_CAP]})
    return ((text or "") + "\n\n[stopped: tool round limit reached]", tool_log, C.MAX_ROUNDS,
            "\n".join(all_reasoning), meta)


def _tool_ctx(project, chat_id):
    return {"project_id": project.get("id", C.INBOX), "project": project, "chat_id": chat_id}


def _load_turn(chat, st, provider_key):
    project = _project(chat)
    mode = _mode(chat)
    role = _role(chat)
    skills = _skills(chat)
    specs, routes = T.specs_for(chat, mode, st)
    cfg = P.resolve(st, provider_key)
    temp = st["provider"].get("temperature")
    if temp is None:
        temp = mode["temperature"]
    return project, mode, role, skills, specs, routes, cfg, temp


def turn(chat_id, content=None, regenerate=False, provider_key=None, emit=None, display=None):
    with _ChatLock(chat_id):
        chat = store.get_chat(chat_id)
        old = None
        if regenerate:
            if not chat["messages"] or chat["messages"][-1]["role"] != "assistant":
                raise EngineError("nothing to regenerate: last message is not an assistant reply")
            old = chat["messages"][-1]
            ctx_chat = dict(chat, messages=chat["messages"][:-1])   # old reply stays until success
        else:
            text = (content or "").strip()
            if not text:
                raise EngineError("empty message")
            # display: optional short label shown in the UI in place of the full content (e.g. knack prompts)
            um = new_message("user", text, {"display": str(display)[:300]} if display else None)
            chat["messages"].append(um)
            store.put_chat(chat)
            store.add_action("message.saved", um["id"], {"role": "user", "hash": _h(text), "chars": len(text)},
                             project_id=chat.get("project_id"), chat_id=chat_id)
            ctx_chat = chat

        t0 = time.time()
        try:
            st = store.get_settings()
            project, mode, role, skills, specs, routes, cfg, temp = _load_turn(ctx_chat, st, provider_key)
            query = next((m["content"] for m in reversed(ctx_chat["messages"]) if m["role"] == "user"), "")
            messages, snap = X.build(ctx_chat, project, st, mode, role, query, skills)
            snap["tools"] = [s["function"]["name"] for s in specs]
            if emit:
                emit("context", {"hash": snap["hash"], "tokens_est": snap["tokens_est"],
                                 "dropped": snap["history"]["dropped"], "tools": snap["tools"]})
            reply, tool_log, rounds, reasoning, fmeta = _complete(cfg, messages, specs, routes,
                                                                  _tool_ctx(project, chat_id), temp, emit)
        except (P.ProviderError, EngineError) as e:
            store.add_action("turn.error", chat_id, {"error": str(e)[:500], "regenerate": regenerate},
                             project_id=chat.get("project_id"), chat_id=chat_id)
            raise

        chat = store.get_chat(chat_id)
        if old is not None:
            chat["messages"].pop()
            store.add_action("message.superseded", old["id"], {"role": "assistant", "hash": _h(old["content"]),
                                                              "chars": len(old["content"]),
                                                              "content": old["content"]},
                             project_id=chat.get("project_id"), chat_id=chat_id)
        am = new_message("assistant", reply, {"ctx_hash": snap["hash"], "model": cfg["model"],
                                              "route": cfg["route"], "preset": cfg.get("preset"),
                                              "mode": chat.get("mode", "work"), "tools": tool_log, "rounds": rounds,
                                              "reasoning": reasoning, "finish_reason": fmeta.get("finish_reason"),
                                              "usage": fmeta.get("usage"),
                                              "ms": int((time.time() - t0) * 1000), "saved": []})
        chat["messages"].append(am)
        store.put_chat(chat)
        store.add_action("message.saved", am["id"], {"role": "assistant", "hash": _h(reply), "chars": len(reply),
                                                     "ctx_hash": snap["hash"], "tools": len(tool_log)},
                         project_id=chat.get("project_id"), chat_id=chat_id)
        return {"assistant": am, "context": snap}


def context_preview(chat_id):
    chat = store.get_chat(chat_id)
    st = store.get_settings()
    project = _project(chat)
    mode = _mode(chat)
    query = next((m["content"] for m in reversed(chat["messages"]) if m["role"] == "user"), "")
    messages, snap = X.build(chat, project, st, mode, _role(chat), query, _skills(chat))
    specs, _ = T.specs_for(chat, mode, st)
    snap["tools"] = [s["function"]["name"] for s in specs]
    return {"messages": messages, "snapshot": snap}


def save_message_to_workspace(chat_id, msg_id, path):
    chat = store.get_chat(chat_id)
    msg = next((m for m in chat["messages"] if m["id"] == msg_id), None)
    if not msg:
        raise store.NotFound(f"message {msg_id}")
    project_id = chat.get("project_id") or C.INBOX
    meta = T.save_file(project_id, path, msg["content"], "ui:save_response", chat_id)
    msg["meta"].setdefault("saved", []).append(meta["path"])
    store.put_chat(chat)
    return meta


def set_pin(chat_id, msg_id, pinned):
    with _ChatLock(chat_id):
        chat = store.get_chat(chat_id)
        msg = next((m for m in chat["messages"] if m["id"] == msg_id), None)
        if not msg:
            raise store.NotFound(f"message {msg_id}")
        msg["pinned"] = bool(pinned)
        store.put_chat(chat)
        store.add_action("message.pinned" if pinned else "message.unpinned", msg_id, {"role": msg["role"]},
                         project_id=chat.get("project_id"), chat_id=chat_id)
        return msg


def delete_message(chat_id, msg_id):
    with _ChatLock(chat_id):
        chat = store.get_chat(chat_id)
        idx = next((i for i, m in enumerate(chat["messages"]) if m["id"] == msg_id), None)
        if idx is None:
            raise store.NotFound(f"message {msg_id}")
        gone = chat["messages"].pop(idx)
        store.put_chat(chat)
        store.add_action("message.deleted", msg_id, {"role": gone["role"], "hash": _h(gone["content"]),
                                                     "chars": len(gone["content"]), "content": gone["content"]},
                         project_id=chat.get("project_id"), chat_id=chat_id)


# ---------- workflows ----------

def _fill(template, values):
    out = template
    for k, v in values.items():
        out = out.replace("{{" + k + "}}", v or "")
    return out


def start_run(wf_id, input_text="", project_id=None, provider_key=None, background=True):
    wf = store.get_doc("workflow", wf_id)
    if not wf:
        raise store.NotFound(f"workflow {wf_id}")
    if not wf.get("steps"):
        raise EngineError("workflow has no steps")
    pid = project_id or wf.get("project_id") or C.INBOX
    run = {"id": store.new_id("run"), "workflow_id": wf_id, "project_id": pid, "status": "queued",
           "input_chars": len(input_text or ""), "steps": [], "error": None, "ended": None}
    store.put_doc("run", run["id"], run)
    if background:
        threading.Thread(target=_execute_run, args=(run["id"], wf, input_text or "", provider_key),
                         daemon=True).start()
    else:
        _execute_run(run["id"], wf, input_text or "", provider_key)
    return store.get_doc("run", run["id"])


def _execute_run(run_id, wf, input_text, provider_key):
    run = store.get_doc("run", run_id)
    pid = run["project_id"]
    project = store.get_doc("project", pid) or store.get_doc("project", C.INBOX)
    st = store.get_settings()
    run["status"] = "running"
    store.put_doc("run", run_id, run)
    prev, done = "", 0
    try:
        for i, step in enumerate(wf["steps"]):
            pseudo = {"id": run_id, "mode": step.get("mode") if step.get("mode") in C.MODES else "work",
                      "messages": [], "project_id": pid, "role_id": wf.get("role_id")}
            project_, mode_, role, skills, specs, routes, cfg, temp = _load_turn(pseudo, st, provider_key)
            prompt = _fill(step["prompt"], {"input": input_text, "prev": prev})
            msgs, snap = X.build(pseudo, project_, st, mode_, role, prompt + "\n" + input_text, skills)
            msgs.append({"role": "user", "content": prompt})
            reply, tool_log, rounds, _reasoning, _fmeta = _complete(cfg, msgs, specs, routes,
                                                                    _tool_ctx(project_, None), temp)
            entry = {"index": i, "step_id": step.get("id", str(i)), "status": "done", "chars": len(reply),
                     "hash": _h(reply), "ctx_hash": snap["hash"], "tools": len(tool_log), "saved": None}
            if step.get("save_path"):
                meta = T.save_file(pid, step["save_path"], reply, "workflow:" + wf["id"], None)
                entry["saved"] = meta["path"]
            store.add_action("workflow.step", run_id, {k: entry[k] for k in
                                                       ("step_id", "status", "chars", "hash", "ctx_hash", "saved")},
                             project_id=pid)
            run["steps"].append(entry)
            store.put_doc("run", run_id, run)
            prev = reply
            done += 1
        run["status"] = "done"
    except Exception as e:  # recorded, not raised: the run record carries the failure
        run["status"] = "failed"
        run["error"] = f"{type(e).__name__}: {e}"[:500]
    run["ended"] = store.now()
    store.put_doc("run", run_id, run)
    summary = f"{wf.get('name', wf['id'])}: {done}/{len(wf['steps'])} steps, {run['status']}"
    project = store.get_doc("project", pid) or project
    project["status"] = {"state": (project.get("status") or {}).get("state", "active"), "note": summary,
                         "next_step": "" if run["status"] == "done" else "review failed run " + run_id,
                         "updated": store.now()}
    store.put_doc("project", pid, project)
    store.add_action("status.updated", pid, project["status"], project_id=pid)
    store.add_action("run.finished", run_id, {"status": run["status"], "steps_done": done,
                                              "error": run["error"]}, project_id=pid)
