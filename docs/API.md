# Nexus HTTP API

Base: `http(s)://<host>:<port>`. JSON bodies. All `/api/*` routes need `Authorization: Bearer <token>` (or `X-NX-Token`). Token = `NX_TOKEN`, else the value in `DATA_DIR/token` (created 0600 on first start). `NX_ALLOW_OPEN=1` disables auth (local dev only). Static UI files are served without a token.
Custom-route model key: header `X-NX-Provider-Key` (per request, never stored).

Errors: `{"error": "<message>"}` with status
400 invalid input / engine or tool error · 401 token · 404 not found · 405 method · 409 chat busy (one turn at a time per chat) · 413 body over 5 MB · 500 unexpected · 502 model provider error.

## State and status

| Method | Path | Returns |
|---|---|---|
| GET | `/api/state` | `version, modes, tool_groups, inbox, settings, projects, chats[], roles, skills, workflows, mcp, python_enabled, gateway_configured, token_required` |
| GET | `/api/status?probe=1` | `provider{route,model,reachable,latency_ms,error}, mcp[], python_enabled, version, db{path,docs,files,actions}` |
| PUT | `/api/settings` | merged settings. Keys: `system_prompt, context_chars (>=1000), knacks, provider{mode, preset, base_url, model, temperature, thinking, reasoning_effort}, theme{...}`. `knacks` is a list of `{id, label, prompt}` (max 20; id `^[a-z][a-z0-9_-]{1,31}$`, not a built-in id, unique; label 1-24 chars; prompt 1-2000 chars; unknown fields rejected). Invalid values return 400 and nothing is saved. `preset` is one of deepseek, zai, openrouter, custom. `thinking` is null, enabled or disabled (DeepSeek). `reasoning_effort` is a per-preset list (see `provider_presets` in `/api/state`). Invalid values return 400. |
| GET | `/api/actions?project=&chat=&limit=` | `{actions[]}` newest first. Each: `id, ts, project_id, chat_id, type, ref, payload` |
| GET | `/api/backup` | full JSON export |
| POST | `/api/restore` | body = backup export; replaces all data; records `backup.restored` |

## Documents (project, role, skill, workflow, mcp)

| Method | Path | Notes |
|---|---|---|
| POST | `/api/doc/{kind}` | 201, returns doc with generated `id` (or supply `id`) |
| PUT | `/api/doc/{kind}/{id}` | full replace of the doc body |
| DELETE | `/api/doc/{kind}/{id}` | inbox project cannot be deleted |
| GET | `/api/projects/{id}` | `{project, chats[], files[], actions[]}` |
| POST | `/api/skills/draft` | body `{description}` (1-4000 chars). One model call, no tools, temperature 0.3. Returns `{name, instructions, model, route}`. Saves nothing; the UI fills the new-skill form for review. Records `skill.drafted`. Errors: 400 `Invalid` (bad description, model output not a JSON object, missing or oversized name (max 60) or instructions (max 4000)). |

Project fields: `name, instructions, scope[] (workspace paths), status{state: active|paused|done, note, next_step, updated}`.
Role: `name, title, system_prompt`. Skill: `name, instructions`. MCP: `name, url, enabled, headers{}`. Workflow: `name, steps[{id, prompt, mode, save_path}]`.

## Chats

| Method | Path | Notes |
|---|---|---|
| POST | `/api/chats` | `{project_id, mode, role_id, tool_groups, skill_ids, title?, chat_prompt?, reminder?}` → chat |
| GET | `/api/chats/{id}` | full chat incl. `messages[]` |
| PATCH | `/api/chats/{id}` | any of `title, mode, role_id, chat_prompt, reminder, tool_groups, project_id, skill_ids` |
| DELETE | `/api/chats/{id}` | record keeps the deletion |
| GET | `/api/chats/{id}/context` | `{messages[], snapshot{hash, blocks[{tag,chars}], history{included,dropped}, reminder, chars, tokens_est, budget_chars, scope[], retrieved[], tools[]}}`. Exact payload that would be sent. |

Message object: `id, role (user|assistant), content, pinned, meta{tools[{name,ok,summary}], saved[], model, route, preset, ms, ctx_hash, reasoning (native, separate from content), finish_reason, usage (null when provider sent none)}`.

Truncation: if the provider stops on `finish_reason=length` with no visible text, the turn fails (502, `turn.error` record) and no empty assistant row is saved.

### Send a turn

`POST /api/chats/{id}/messages` with `{"content": "..."}`.

- JSON (default): `200 {assistant, context}`.
- SSE: add `"stream": true` or `?stream=1`. Events in order:
  - `context` `{hash, tokens_est, dropped, tools}`
  - `delta` `{text}` (repeated). Visible answer only.
  - `reasoning` `{text}` (repeated). Native provider reasoning (DeepSeek `reasoning_content`, OpenRouter `reasoning`). Never mixed into `delta`.
  - `tool` `{name, ok, summary}` (per tool call)
  - `done` `{assistant, context}` or `error` `{status, error}`

Chat title: set by the UI on first turn (`PATCH title`). Not automatic in the API.

### Regenerate

`POST /api/chats/{id}/regenerate` with `{}` (or `stream`). Replaces the last assistant message only after success. On failure the old reply stays and a `turn.error` record is written.

### Message actions

| Method | Path | Body / effect |
|---|---|---|
| POST | `/api/chats/{id}/messages/{mid}/save` | `{"path"}` writes the message content to the workspace; record `file.saved` |
| POST | `/api/chats/{id}/messages/{mid}/pin` | `{"pinned": true|false}`; the last 5 pinned messages are injected into context (`PINS_MAX`) |
| DELETE | `/api/chats/{id}/messages/{mid}` | record `message.deleted` keeps the content in payload |

Editing a user message = delete it and later messages, then send new content.

## Workspace files

| Method | Path | Notes |
|---|---|---|
| GET | `/api/files?project=<id>` | `{files[{path, bytes, sha, updated}]}` |
| GET | `/api/files?project=<id>&path=<p>` | `{path, content, sha, bytes, updated}` |
| PUT | `/api/files` | `{project, path, content}`; writes are recorded as `file.saved` |
| DELETE | `/api/files?project=<id>&path=<p>` | records `file.deleted` |

Paths are normalized relative paths. Traversal is rejected. Max 2,000,000 chars per file.

## Workflows, runs, python, MCP

| Method | Path | Notes |
|---|---|---|
| POST | `/api/workflows/{id}/run` | `{"input", "project_id"}` → 202 run record (`status: queued`) |
| GET | `/api/runs/{id}` | `{id, workflow_id, project_id, status: queued|running|done|failed, steps[], error, ended}` |
| POST | `/api/python` | `{"project_id", "code"}` → `{ok, output, summary}`. Requires `NX_ENABLE_PYTHON=1` (off by default). Child process gets a minimal environment and rlimits; no server secrets in env. Not a filesystem sandbox. |
| POST | `/api/mcp/{id}/probe` | `{ok, tools[]}` or `{ok:false, error}` |

Provider contract (checked against provider docs, 2026-10): DeepSeek sends `thinking` and `reasoning_effort` only when set. When a request carries `tools`, prior `reasoning_content` is sent back; otherwise it is stripped. OpenRouter sends `reasoning: {effort}` only when set. Z.ai and custom endpoints get no provider-specific fields.

Workflow step: `prompt` may contain `{{input}}` (run input) and `{{prev}}` (previous step output). `save_path` writes the step output to the project workspace. Each step appends a `workflow.step` record. On finish: `run.finished` record, and project `status` is updated with a summary.

External trigger example:

```bash
curl -X POST $NX/api/workflows/$WF/run -H "Authorization: Bearer $NX_TOKEN" \
  -H "Content-Type: application/json" -d '{"input":"Q3 pricing review","project_id":"'$PRJ'"}'
curl $NX/api/runs/$RUN -H "Authorization: Bearer $NX_TOKEN"
```

## Tools available to the model

| Name | Group | Effect |
|---|---|---|
| `workspace_list` / `workspace_read` | workspace | list / read project files |
| `workspace_write` | workspace | write a project file (record `file.saved`) |
| `search_sources` | sources | keyword search over Source Scope only; returns chunks with paths |
| `project_set_status` | status | set state, verified note, next step (record `status.updated`) |
| `python_run` | python | run code in temp dir with scope files; saves new/changed text files |
| `mcp_<server>_<tool>` | mcp | MCP tool from an enabled server |

Mode default groups: Work = workspace, status, sources, mcp · Research = sources, workspace, mcp · Code = workspace, sources, python, mcp. A chat's `tool_groups` overrides the mode default (`null` = use default).

## Context order (fixed)

system → mode → role → skills → project (instructions, status, scope list) → chat prompt → pins → retrieved scope chunks → history (newest kept, budget-trimmed) → reminder.

Budget: `context_chars` (default 48,000). Snapshot hash is stored on each assistant message as `meta.ctx_hash`.

## Action record types

`backup.restored` · `chat.created` · `chat.deleted` · `chat.moved` · `file.deleted` · `file.saved` · `message.deleted` · `message.pinned` · `message.saved` · `message.superseded` · `message.unpinned` · `run.finished` · `skill.drafted` · `status.updated` · `tool.executed` · `turn.error` · `workflow.step`.

Append-only: UPDATE and DELETE on this table are blocked by triggers.
