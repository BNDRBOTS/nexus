"""Global constants and defaults. Single source of truth; served to the UI via GET /api/state."""
import os
import pathlib

ENV = os.environ
ROOT = pathlib.Path(__file__).resolve().parent.parent
VERSION = "2.0.0"

HOST = ENV.get("NX_HOST", "0.0.0.0")
PORT = int(ENV.get("NX_PORT", "8000"))
DATA_DIR = pathlib.Path(ENV.get("NX_DATA_DIR", ROOT / "data"))
DB_PATH = DATA_DIR / "nexus.db"

TOKEN = ENV.get("NX_TOKEN", "")                       # empty: app.main() loads or creates DATA_DIR/token
ALLOW_OPEN = ENV.get("NX_ALLOW_OPEN") == "1"          # explicit opt-out: unauthenticated API (local dev only)
GATEWAY = {                                            # "Use mine (paid)" route; key never leaves the server
    "url": ENV.get("NX_GATEWAY_URL", ""),
    "key": ENV.get("NX_GATEWAY_KEY", ""),
    "model": ENV.get("NX_GATEWAY_MODEL", ""),
}
PYTHON_ENABLED = ENV.get("NX_ENABLE_PYTHON") == "1"   # python_run executes host code; opt-in only

INBOX = "inbox"
MAX_ROUNDS = 6            # max tool rounds per turn
TOOL_OUTPUT_CAP = 12000   # chars returned to the model per tool call
BODY_CAP = 5_000_000      # request body bytes
FILE_CAP = 2_000_000      # chars per workspace file

MODES = {
    "work": {
        "label": "Work",
        "prompt": ("Execute the requested work exactly. Produce complete deliverables, not outlines. "
                   "When a deliverable must persist, save it with workspace_write. "
                   "When progress changes, record the verified state with project_set_status."),
        "tool_groups": ["workspace", "status", "sources", "mcp"],
        "temperature": 0.4,
    },
    "research": {
        "label": "Research",
        "prompt": ("Answer from Source Scope first: call search_sources and cite file paths. "
                   "Separate verified facts from inference. State gaps explicitly. "
                   "Save finished briefs with workspace_write."),
        "tool_groups": ["sources", "workspace", "mcp"],
        "temperature": 0.5,
    },
    "code": {
        "label": "Code",
        "prompt": ("Write complete, runnable code with minimal dependencies. Save files with workspace_write. "
                   "Use python_run to verify behaviour and report the actual output."),
        "tool_groups": ["workspace", "sources", "python", "mcp"],
        "temperature": 0.2,
    },
}

TOOL_GROUPS = ["workspace", "status", "sources", "python", "mcp"]

# Provider presets: defaults only. Every value here was checked against the provider's current docs.
# openrouter has no default model: model IDs are user-supplied (discover via GET /api/v1/models).
PROVIDER_PRESETS = {
    "deepseek": {"label": "DeepSeek", "base_url": "https://api.deepseek.com", "model": "deepseek-flash",
                 "thinking": True, "efforts": ["low", "high", "max"]},
    "zai": {"label": "Z.ai (GLM)", "base_url": "https://api.z.ai/api/paas/v4", "model": "glm-5.3",
            "thinking": False, "efforts": []},
    "openrouter": {"label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "model": "",
                   "thinking": False, "efforts": ["minimal", "low", "medium", "high", "xhigh", "max", "none"],
                   "headers": {"X-OpenRouter-Title": "Nexus"}},
    "custom": {"label": "Custom OpenAI-compatible", "base_url": "", "model": "", "thinking": False, "efforts": []},
}
THINKING_MODES = (None, "enabled", "disabled")

REASONING_EFFORTS = (None, "minimal", "low", "medium", "high", "xhigh", "max", "none")  # union; per-preset list in PROVIDER_PRESETS

DEFAULT_SETTINGS = {
    "system_prompt": ("You are Nexus, an execution assistant. Be exact and complete. "
                      "Never claim work was saved or executed unless a tool result confirms it."),
    "context_chars": 48000,
    "provider": {
        "mode": "custom",                       # custom = user key from browser | gateway = server env key
        "preset": "deepseek",                   # see PROVIDER_PRESETS
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-flash",
        "temperature": None,                    # None = use mode temperature (ignored by DeepSeek thinking mode)
        "thinking": None,                       # DeepSeek only: None (API default = enabled) | enabled | disabled
        "reasoning_effort": None,               # None | minimal|low|medium|high|xhigh|max|none (API-specific)
    },
    "theme": {
        "preset": "graphite",
        "accent": "#f97316",
        "bg": "#0b0d10",
        "surface": "#14171c",
        "text": "#e7e9ee",
        "radius": 12,
        "font_scale": 1.0,
    },
}

DEFAULT_ROLES = [
    {"id": "role_analyst", "name": "Analyst", "title": "Research analyst",
     "system_prompt": "Analyze sources, separate facts from inference, and cite the source file for each claim."},
    {"id": "role_engineer", "name": "Engineer", "title": "Software engineer",
     "system_prompt": "Write correct, minimal, runnable code. Verify it when tools allow."},
    {"id": "role_editor", "name": "Editor", "title": "Copy editor",
     "system_prompt": "Tighten prose, keep the meaning, remove filler, and return the full revised text."},
]
