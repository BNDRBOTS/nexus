# Nexus

Lightweight AI workspace. One Python process (stdlib only) serves the API and the UI. No build step, no runtime dependencies.

- **Modes**: Work, Research, Code. Each sets the system prompt, temperature, and allowed tool groups.
- **Projects**: instructions, Source Scope (files the model may read), status (state, verified note, next step), chats, workflows.
- **Workspace**: per-project files. Save any reply as a file. Code mode can run Python against scope files.
- **Record**: append-only action log (saved messages, tool results, file writes, status changes, runs, deletions).
- **Context snapshot**: exact message list sent for a chat, with block sizes, dropped history, scope, retrieval, hash.
- **Workflows**: ordered steps run server-side. Trigger from the UI or over HTTP.
- **MCP**: Streamable HTTP servers; tools appear as `mcp_<server>_<tool>`.
- **Model route**: presets for DeepSeek, Z.ai, OpenRouter, or any OpenAI-compatible endpoint. Your own key (per request, browser-held) or an operator gateway (server key). Native reasoning is stored and shown separately from the answer.

## Run

```bash
python3 -m server.app          # http://0.0.0.0:8000
```

Tests (end-to-end against a mock model and mock MCP server, plus unit tests for the stream decoder):

```bash
python3 -m unittest tests.test_api tests.test_sse
```

## Configuration (env)

| Var | Default | Meaning |
|---|---|---|
| `NX_HOST` | `0.0.0.0` | bind address |
| `NX_PORT` | `8000` | port |
| `NX_DATA_DIR` | `./data` | SQLite store (`nexus.db`) |
| `NX_TOKEN` | empty | if set, every `/api/*` call needs `Authorization: Bearer <token>` or `X-NX-Token` |
| `NX_GATEWAY_URL` / `NX_GATEWAY_KEY` / `NX_GATEWAY_MODEL` | empty | operator gateway route; key stays on server |
| `NX_ENABLE_PYTHON` | off | `1` enables `python_run`. It runs host Python in a temp dir with `-I` and a 20 s timeout. It is not a sandbox. |

Your own key: set in Settings → Model. It is kept in browser `localStorage` and sent as `X-NX-Provider-Key` per request. The server never stores or logs it.

## Layout

```
server/
  config.py    constants, MODES, TOOL_GROUPS, DEFAULT_SETTINGS, DEFAULT_ROLES, env
  store.py     SQLite: doc, file, action (append-only triggers), backup/restore
  provider.py  presets, route resolve, byte-safe SSE decoder, streaming chat, status probe
  mcp.py       Streamable HTTP MCP client
  tools.py     tool specs per mode, execute(), retrieval over Source Scope, python_run
  context.py   context builder + snapshot (single source of the sent message list)
  engine.py    turn loop, regenerate, save/pin/delete, workflow runs, per-chat lock
  app.py       HTTP routes, auth, SSE, static files
js/
  00-core.js   global NX: state, api, stream (SSE), md, theme, modal/ask/confirm/toast
  10-chat.js   sidebar, topbar, messages, composer, slash commands, send/regenerate
  20-panels.js Canvas, Project, Context, Record, Status panels; NX.watchRun
  30-settings.js settings modal: model, theme, roles, skills, mcp, workflows, data
  90-boot.js   status pill, routing (#chat=<id>), shortcuts, startup
css/app.css    all styles; tokens are CSS variables (see theme)
index.html     shell; loads js/* in order
tests/         mock_servers.py, test_api.py (end-to-end), test_sse.py (unit)
docs/API.md    HTTP contract
```

Extension points:
- **Tool**: add a function to `tools.py` (`_fn` spec + handler in the dispatch table) and a name in `TOOL_GROUPS`.
- **Mode**: add an entry to `config.MODES`; UI picks it up from `/api/state`.
- **Frontend module**: new file in `js/`, register in `index.html` after `00-core.js`, expose on `NX.<name>`.
- **Panel**: add a tab in `20-panels.js` (`TABS` + render function).
- **Media (video, sound)**: not implemented. Plan: new `server/media.py` provider + workspace files as outputs + a panel tab. No schema change needed (files and record are generic).

Not implemented from the reference app: image generation, web search, URL scrape, chat branching, archive, continue, wallpapers, lorebook import, SillyTavern character import.

Theme: presets (graphite, ocean, ember, paper) plus custom accent, background, surface, text, radius, font scale. Stored in `settings.theme`, applied as CSS variables on `:root`.
