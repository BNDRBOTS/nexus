# Nexus build status

Machine-readable ledger. Status values: `done` (built and tested), `partial` (built, gaps listed), `open` (not built), `n/a`.
Version 2.0.0. Branch `arena/fceba36e-nexus`.

## Scope items

| # | Item | Status | Where | Gap |
|---|------|--------|-------|-----|
| 1 | Work / Research / Code modes on thread configs | done | `server/config.py` MODES | none |
| 2 | Local Project record (instructions, chat IDs, doc refs, status) | done | `store.py` doc kind `project`; Project panel (`js/20-panels.js`); `project_set_status` tool; doc refs = Source Scope | none (scope verified in UI via `tests/browser/project_scope.js`, API test_04/test_08, retrieval) |
| 3 | Save Response → Workspace File | done | `engine.save_message_to_workspace`, `tools.save_file` | none |
| 4 | Source Scope (selected files + permitted retrieval tools only) | done | `tools.retrieve`, `context.build` scope | keyword scoring only, no embeddings |
| 5 | Action Record (saved messages, executed tool results) | done | `store.action` (append-only triggers), `tool.executed` | no UI filter |
| 6 | Context Snapshot, Backup, Model Status | done | `engine.context_preview`, `store.export_all/restore`, `provider.status` | none |
| 7 | Loop: open project → context → work → save → preserve → resume | partial | project status and next step injected into every chat's context (test_22); Continue button; `start_run`; `tests/test_resume.py` restarts the server on the same data dir and resumes from stored progress (mutation-checked) | no UI test of the loop; resume tested on SQLite only
| 8 | MCP usage | partial | `server/mcp.py` Streamable HTTP (JSON + SSE) | stdio out of scope |
| 9 | Python tooling | partial | `tools.run_python` (`python -I`, temp dir, 20 s, minimal env, rlimits); owner-only (project tokens denied, test_30) | not a filesystem sandbox; off unless `NX_ENABLE_PYTHON=1`; owner python can read `data/token` |
| 10 | Canvas connect | partial | `js/20-panels.js` canvas panel | connection to project files not verified |
| 11 | Plugins / skills / skill maker | partial | plugins: `server/plugins.py` (HTTP tools, doc kind `plugin`, group `plugins`, test_28, browser flow `tests/browser/plugins.js`); skill IDs on chats; skill maker `server/skillmaker.py` + `POST /api/skills/draft` (draft only, saves nothing; test_26, browser flow `tests/browser/skills.js`) | no plugin install/marketplace; plugins are HTTP endpoints only (no in-process code); no CLI command for plugins |
| 12 | System prompt + chat prompt | done | global `system_prompt`, roles; per-chat `chat_prompt` (PATCH chat, `/chatprompt` command, injected after project) | none (test_31: sent to model, ordered before history, cleared on empty) |
| 13 | Chat session connector | done | `client/nexus.py` (stdlib CLI, JSON out, exit codes), `docs/CONNECTOR.md`, routes in `docs/API.md` | no streaming in CLI (use SSE over HTTP); no non-Python client |
| 14 | Optional SQL DB | done | `store.py` `_Db` shim; `NX_DB_URL=postgresql://…` selects Postgres (psycopg, lazy import); SQLite default | no other SQL engines; no schema migrations tool (schema is `CREATE IF NOT EXISTS`); move data via backup/restore |
| 15 | Custom API key OR operator (paid) key | done | `provider.resolve`; key sent per request, never stored server-side | none |
| 16 | Workflow sections / external call trigger | done | workflow editors (`js/30-settings.js`), `start_run`, `POST /api/workflows/{id}/run`; auth rules in `docs/API.md` | no rate limit or run queue; no completion callback (poll `GET /api/runs/{id}`) |
| 17 | Custom colors | done | theme CSS variables, `theme` settings | none |
| 18 | Per-message prompt-stick menu and knacks | partial | pin = stick (`set_pin`); Knacks popover on assistant replies: 6 built-ins (`js/05-knacks.js`) + user knacks stored in `settings.knacks`, validated by `app.normalize_knacks`, edited in Settings → Knacks | no user-knack reordering or import/export; built-ins not editable; browser run is jsdom only (not a real browser) |
| 19 | Expansion to video / sound | open | — | architecture only |
| 20 | Polished UI (Perplexity / Kimi / Grok level) | partial | `css/app.css`, `index.html` | not visually reviewed in this session |
| 21 | Provider protocol (this session) | done | see below | none known |

## Provider protocol (this session)

Done and tested (`tests/test_sse.py`, `tests/test_api.py` test_17, test_18):

- Presets: `deepseek` (default; `https://api.deepseek.com`, model `deepseek-flash`), `zai` (`https://api.z.ai/api/paas/v4/`, `glm-5.3`), `openrouter` (`https://openrouter.ai/api/v1`), `custom`. Coding Plan endpoint not used.
- Byte-safe SSE decoder: CRLF / CR / LF, split UTF-8, multi-line `data`, comments, trailing-CR wait, `flush()`.
- Reasoning kept separate from content. Stream events: `reasoning`, `delta`, `final`. Stored as `meta.reasoning`.
- Tool calls assembled by index; `usage` and `finish_reason` kept.
- DeepSeek `reasoning_content` passed back when the request has tools; stripped otherwise.
- `finish_reason=length` with no visible text → 502, no assistant row saved.
- Provider errors and malformed frames raise `ProviderError`.
- Settings validation: preset, thinking, reasoning_effort, context_chars ≥ 1000.
- Stale default migration: only the exact pair `https://api.deepseek.com/v1` + `deepseek-chat`.
- Retry: 429 / 500 / 502 / 503 / 504 and connection errors are retried up to 3 attempts, with `Retry-After` honored up to 8 s. Retries happen only before the stream opens, so output is never duplicated. Other 4xx are not retried.
- OpenRouter: docs (fetched 2026-10) define `reasoning: {effort}` in requests and `reasoning` on each message in responses. Nexus matches both. `reasoning_details` is not referenced in those docs, so no passback is implemented. Length-cut replies with empty content raise, per docs (reasoning counts against `max_tokens`).

Not built: per-model capability discovery (OpenRouter `/api/v1/models` `supported_efforts`), cost or pricing display, model-specific moderation, image/TTS providers.

## Known risks

- Postgres backend: tested against one local PostgreSQL 16 instance only. No TLS settings, pooling or failover. Not load-tested.
- Plugins: the server POSTs to any plugin URL it can reach, including internal hosts. Plugin output is untrusted model input. Same trust boundary as MCP servers.
- Auth: token required by default (`NX_TOKEN` or generated `data/token`). `NX_ALLOW_OPEN=1` removes it; do not set on a reachable host.
- Workflow triggers: no rate limit or concurrency cap. Any valid owner or project token can start unlimited runs (each runs in a background thread and calls the model). Revoke tokens that leak. Auth rules and test_32 in `docs/API.md`.
- Project isolation: project tokens (`nxp_…`) are scoped to one project and cannot reach owner-only routes. Tested: test_29 (403 on other projects and owner routes, 401 after revoke, mutation-checked) and test_30 (python, mcp and plugin tools denied to project tokens in turns, context preview and workflow runs; mutation-checked). The owner token still reads every project. Tools and model context are bound server-side to the chat's project (test_08, test_20).
- `NX_ENABLE_PYTHON=1` runs model-written code on the host as the server user. It can read host files, including `data/nexus.db` and `data/token`. Keep disabled unless the host is single-owner. Project tokens cannot reach python (test_29, test_30). Owner chats still can, so python gives owner-level file access to anyone the owner lets run it.
- Branch overwrite request is pending user confirmation. No force-push or reset has been performed.

## Verification

- `python3 -m unittest tests.test_api tests.test_sse tests.test_auth tests.test_resume tests.test_browser`: 52 tests OK on SQLite (`test_api` 32, `test_sse` 16, `test_auth` 2, `test_resume` 1, `test_browser` 1 covering 4 jsdom flows) and `tests/test_knacks.js` OK. test_31 (chat prompt) was checked on SQLite only; the PostgreSQL run below predates it. test_27 runs `client/nexus.py` against the live test server (round trip, exit codes 1 and 2).
- `NX_TEST_DB_URL=<admin url> python3 -m unittest tests.test_api`: 30 OK on PostgreSQL 16.2 (before test_31 was added) (pgserver 0.1.4 bundle, psycopg 3.3.6). Fresh DB per run. test_15 checks append-only through psycopg and that writes after restore do not collide with restored action ids (mutation check: removing the `setval` line makes test_15 fail).
- Browser (jsdom, `tests/browser`, run via `tests/test_browser.py` after `npm install`): skill draft, plugin editor, project token panel, project scope panel. 4 flows OK against a live server and mock model. Mutation check: dropping scope from the Save payload fails the scope flow.
- Migration check (manual): SQLite server backup → Postgres server restore: 6 docs restored, writes after restore 200, `/api/status` shows password masked. Earlier jsdom checks for the knack editor and menu (8/8) were run from a scratch script that is not kept in the repo; `tests/test_knacks.js` covers the knack validation. The skill draft and plugin editor flows are now in `tests/browser/` (see Verification above).
- `pyflakes server tests client`: clean.
- `node --check js/*.js`: clean.
- Live provider calls: not run (no credentials in sandbox). Behaviour checked against mock OpenAI-compatible server only.
