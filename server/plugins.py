"""Plugins: user-defined HTTP tools. Stdlib only.

A plugin doc = {name, description, url, enabled, parameters (JSON schema, type object), headers{}}.
It becomes tool `plugin_<id>` in group `plugins`, which is off by default and enabled per chat.
Call: POST url. Body = tool arguments (JSON). Headers = plugin headers + X-NX-Project.
Response body (text, capped) is the tool output. Non-2xx or transport failure = tool error.

Trust: same as MCP servers. The server fetches the plugin URL, so any token holder can point a
plugin at any host the server can reach. Plugin output is model input: treat it as untrusted text.
"""
import json
import re
import urllib.error
import urllib.request

from . import config as C
from . import store

FIELDS = {"name", "description", "url", "enabled", "parameters", "headers"}
IGNORED = {"id", "created", "updated"}      # present when the UI echoes a whole doc back
NAME_MAX = 60
DESC_MAX = 500
URL_MAX = 2000
PARAMS_MAX = 8000
HEADERS_MAX = 10
HEADER_VAL_MAX = 500
TIMEOUT = 20
READ_CAP = C.TOOL_OUTPUT_CAP * 4     # bytes read before truncation to TOOL_OUTPUT_CAP chars
HEADER_KEY_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")


class PluginError(Exception):
    pass


def normalize(raw):
    """Validates a plugin body. Returns a clean dict. Raises store.Invalid on any violation."""
    if not isinstance(raw, dict):
        raise store.Invalid("plugin must be an object")
    extra = set(raw) - FIELDS - IGNORED
    if extra:
        raise store.Invalid(f"plugin: unknown fields {sorted(extra)}")
    name = raw.get("name")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= NAME_MAX:
        raise store.Invalid(f"plugin.name must be 1-{NAME_MAX} characters")
    desc = raw.get("description", "")
    if not isinstance(desc, str) or len(desc) > DESC_MAX:
        raise store.Invalid(f"plugin.description must be a string of at most {DESC_MAX} characters")
    url = raw.get("url")
    if not isinstance(url, str) or len(url) > URL_MAX or not url.startswith(("http://", "https://")):
        raise store.Invalid("plugin.url must be an http(s) URL")
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise store.Invalid("plugin.enabled must be true or false")
    params = raw.get("parameters", {"type": "object", "properties": {}})
    if not isinstance(params, dict) or params.get("type") != "object":
        raise store.Invalid("plugin.parameters must be a JSON schema object with type 'object'")
    if len(json.dumps(params)) > PARAMS_MAX:
        raise store.Invalid(f"plugin.parameters exceeds {PARAMS_MAX} characters")
    headers = raw.get("headers", {})
    if not isinstance(headers, dict) or len(headers) > HEADERS_MAX:
        raise store.Invalid(f"plugin.headers must be an object with at most {HEADERS_MAX} entries")
    for k, v in headers.items():
        if not isinstance(k, str) or not HEADER_KEY_RE.match(k) or not isinstance(v, str) \
                or len(v) > HEADER_VAL_MAX or "\r" in v or "\n" in v:
            raise store.Invalid(f"plugin.headers: invalid entry {k!r}")
    return {"name": name.strip(), "description": desc, "url": url, "enabled": enabled,
            "parameters": params, "headers": headers}


def tool_name(plugin_id):
    return re.sub(r"[^A-Za-z0-9_]", "_", f"plugin_{plugin_id}")[:64]


def specs():
    """Tool specs and routes for enabled plugins. routes: name -> ("plugin", id)."""
    out, routes = [], {}
    for p in store.list_docs("plugin"):
        if not p.get("enabled"):
            continue
        name = tool_name(p["id"])
        out.append({"type": "function", "function": {
            "name": name, "description": f"[plugin {p['name']}] {p.get('description', '')}"[:1000],
            "parameters": p.get("parameters") or {"type": "object", "properties": {}}}})
        routes[name] = ("plugin", p["id"])
    return out, routes


def call(plugin, arguments, project_id):
    """POSTs arguments as JSON. Returns response text (capped). Raises PluginError."""
    body = json.dumps(arguments or {}, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "text/plain, application/json",
               "X-NX-Project": project_id or ""}
    headers.update(plugin.get("headers") or {})
    req = urllib.request.Request(plugin["url"], data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read(READ_CAP)
    except urllib.error.HTTPError as e:
        raise PluginError(f"plugin HTTP {e.code}") from e
    except (urllib.error.URLError, OSError) as e:
        raise PluginError(f"plugin unreachable: {getattr(e, 'reason', e)}") from e
    return raw.decode("utf-8", "replace")[:C.TOOL_OUTPUT_CAP]
