"""Skill maker. Drafts a skill {name, instructions} from a description with one model call (no tools).
The draft is returned for review. Nothing is saved here: the user saves through POST /api/doc/skill."""
import json
import re

from . import provider as P
from . import store

DESC_MAX = 4000
NAME_MAX = 60
INSTR_MAX = 4000

SYSTEM = (
    "You write skills for an AI workspace. A skill is a reusable block of instructions that is injected "
    "into a chat's system context. Given a description, return ONLY a JSON object with keys "
    "\"name\" (max 60 characters) and \"instructions\" (max 4000 characters). Instructions: imperative "
    "sentences, concrete steps, the output format, and what to avoid. No text outside the JSON object."
)


def _extract(text):
    s = (text or "").strip()
    try:
        obj = json.loads(s)
    except ValueError:
        m = re.search(r"\{.*\}", s, re.S)   # tolerate a fenced or prefixed JSON object
        if not m:
            raise store.Invalid("model did not return a JSON object")
        try:
            obj = json.loads(m.group(0))
        except ValueError:
            raise store.Invalid("model returned malformed JSON")
    if not isinstance(obj, dict):
        raise store.Invalid("model returned JSON that is not an object")
    return obj


def draft(description, settings, key=None, timeout=60):
    desc = str(description or "").strip()
    if not desc or len(desc) > DESC_MAX:
        raise store.Invalid(f"description must be 1-{DESC_MAX} characters")
    cfg = P.resolve(settings, key)
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": desc}]
    final = None
    for kind, val in P.chat_stream(cfg, messages, tools=None, temperature=0.3, timeout=timeout):
        if kind == "final":
            final = val
    if final is None:
        raise store.Invalid("model returned no output")
    obj = _extract(final.get("content"))
    name = str(obj.get("name", "")).strip()
    instructions = str(obj.get("instructions", "")).strip()
    if not name or len(name) > NAME_MAX:
        raise store.Invalid(f"draft name must be 1-{NAME_MAX} characters")
    if not instructions or len(instructions) > INSTR_MAX:
        raise store.Invalid(f"draft instructions must be 1-{INSTR_MAX} characters")
    return {"name": name, "instructions": instructions, "model": cfg["model"], "route": cfg["route"]}
