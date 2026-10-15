# Chat session connector

Machine contract for driving Nexus chats from another process. Two surfaces, same server:

1. HTTP (authoritative): routes in `docs/API.md`.
2. CLI (`client/nexus.py`): stdlib-only wrapper, JSON on stdout, no dependencies.

## Auth and env

Use a project token as `NX_TOKEN` for a connector scoped to one project. It works for the chat, file, action and workflow-run routes above (workflow runs need `project_id` set to that project). Owner-only routes (state, settings, docs, backup, token management, `POST /api/python`) return 403 with it. Workflow triggers from outside the server: see "External trigger" in `docs/API.md`.

| Var | Use |
|---|---|
| `NX_URL` | base URL, default `http://127.0.0.1:8000` |
| `NX_TOKEN` | bearer token. Fallback file: `NX_TOKEN_FILE` (default `data/token`) |

Every `/api/*` call needs `Authorization: Bearer <token>`. Missing or wrong token → 401.

## Session lifecycle

```
project create ──► chat create ──► chat send (repeat) ──► chat save (per reply) ──► file get
                        │                  │
                        │                  └──► chat context   (inspect exact model input)
                        └──► actions       (audit: chat.created, file.saved, tool.executed, turn.error …)
```

Resume: a project keeps `status{state, note, next_step}`. Every chat in the project receives it in context, so a new chat continues from the stored next step. Set it with `project_set_status` (model tool) or `PUT /api/doc/project/{id}`.

## CLI reference

```bash
export NX_URL=http://127.0.0.1:8000 NX_TOKEN=...
PRJ=$(python3 client/nexus.py project create --name "Q3 pricing" --instructions "Literal output only" --scope notes/brief.md | jq -r .id)
CHAT=$(python3 client/nexus.py chat create --project $PRJ --mode research | jq -r .id)
python3 client/nexus.py chat send $CHAT --stdin <<< "Summarise the brief"        # → {assistant, context}
MSG=$(python3 client/nexus.py chat send $CHAT --text "Draft the memo" | jq -r .assistant.id)
python3 client/nexus.py chat save $CHAT $MSG out/memo.md
python3 client/nexus.py file get $PRJ out/memo.md | jq -r .content
python3 client/nexus.py actions --project $PRJ --limit 20
```

| Command | Maps to |
|---|---|
| `project create --name N [--instructions T] [--scope P…]` | `POST /api/doc/project` |
| `project get ID` | `GET /api/projects/{id}` |
| `chat create --project ID [--mode work\|research\|code] [--title T]` | `POST /api/chats` |
| `chat get ID` | `GET /api/chats/{id}` |
| `chat send ID (--text T \| --stdin)` | `POST /api/chats/{id}/messages` (JSON, not streamed) |
| `chat context ID` | `GET /api/chats/{id}/context` |
| `chat save ID MSG PATH` | `POST /api/chats/{id}/messages/{mid}/save` |
| `file list PROJ` / `file get PROJ PATH` / `file put PROJ PATH (--text T \| --stdin)` | `/api/files` |
| `actions [--project] [--chat] [--limit]` | `GET /api/actions` |
| `status [--probe]` | `GET /api/status` |
| `skill draft --description T` | `POST /api/skills/draft` (returns a draft; saves nothing) |

## Exit codes and errors

| Code | Meaning | Output |
|---|---|---|
| 0 | success | JSON result on stdout |
| 1 | server returned 4xx/5xx | `{"status": <code>, "error": <detail>}` on stderr |
| 2 | usage error or connection failure | `{"error": "..."}` on stderr |

## Streaming

The CLI sends turns as JSON and waits for the full reply. For token streaming, call `POST /api/chats/{id}/messages` with `"stream": true` and read SSE (`context`, `delta`, `reasoning`, `tool`, `done` | `error`); see `docs/API.md`.

## Limits

- Chat history is budget-trimmed to `context_chars` (default 48,000). Older turns drop from the model input; they stay in storage.
- One turn per chat at a time. A second concurrent turn on the same chat returns 409 (`processing another turn`).
- Project isolation: use a project token (created with `POST /api/projects/{id}/tokens`, or from the Project panel) for any connector that should only see one project. Owner token = all projects. Tokens are shown once; revoke them the same way.
- Tool execution follows the chat's mode and `tool_groups`. `python_run` exists only when `NX_ENABLE_PYTHON=1`, and never for a project token. Project tokens also never get MCP or plugin tools.
