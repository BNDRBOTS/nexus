# Nexus build status

Machine-readable ledger. Status values: `done` (built and tested), `partial` (built, gaps listed), `open` (not built), `n/a`.
Version 2.0.0. Branch `arena/fceba36e-nexus`.

## Scope items

| # | Item | Status | Where | Gap |
|---|------|--------|-------|-----|
| 1 | Work / Research / Code modes on thread configs | done | `server/config.py` MODES | none |
| 2 | Local Project record (instructions, chat IDs, doc refs, status) | partial | `store.py` doc kind `project`; `project_set_status` tool | document-ref field not verified end to end |
| 3 | Save Response → Workspace File | done | `engine.save_message_to_workspace`, `tools.save_file` | none |
| 4 | Source Scope (selected files + permitted retrieval tools only) | done | `tools.retrieve`, `context.build` scope | keyword scoring only, no embeddings |
| 5 | Action Record (saved messages, executed tool results) | done | `store.action` (append-only triggers), `tool.executed` | no UI filter |
| 6 | Context Snapshot, Backup, Model Status | done | `engine.context_preview`, `store.export_all/restore`, `provider.status` | none |
| 7 | Loop: open project → context → work → save → preserve → resume | partial | chat persistence, `start_run` / `_execute_run` | resume after restart not tested |
| 8 | MCP usage | partial | `server/mcp.py` Streamable HTTP (JSON + SSE) | stdio out of scope |
| 9 | Python tooling | partial | `tools.run_python` (`python -I`, temp dir, 20 s) | not a security sandbox; gated by `NX_ENABLE_PYTHON=1` |
| 10 | Canvas connect | partial | `js/20-panels.js` canvas panel | connection to project files not verified |
| 11 | Plugins / skills / skill maker | partial | skill IDs on chats and drafts | no skill maker, no plugin loader |
| 12 | System prompt + chat prompt | partial | global `system_prompt`, roles | per-chat prompt path not verified |
| 13 | Chat session connector | open | — | — |
| 14 | Optional SQL DB | open | SQLite only (`store.py`) | external SQL adapter not built |
| 15 | Custom API key OR operator (paid) key | done | `provider.resolve`; key sent per request, never stored server-side | none |
| 16 | Workflow sections / external call trigger | partial | workflow editors (`js/30-settings.js`), `start_run` | external trigger auth not documented |
| 17 | Custom colors | done | theme CSS variables, `theme` settings | none |
| 18 | Per-message prompt-stick menu and knacks | partial | `set_pin`, pin chip | knacks not built |
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

Not built: per-model capability discovery (OpenRouter `/models`), cost or pricing display, model-specific moderation, image/TTS providers.

## Known risks

- The preview on port 8000 runs with no `NX_TOKEN`. The API is open to anyone who can reach it. Set `NX_TOKEN` before sharing the preview.
- `NX_ENABLE_PYTHON=1` enables code execution in the host process tree. Keep disabled on shared hosts.
- Branch overwrite request is pending user confirmation. No force-push or reset has been performed.

## Verification

- `python3 -m unittest tests.test_api tests.test_sse`: 32 tests OK.
- `pyflakes server tests`: clean.
- `node --check js/*.js`: clean.
- Live provider calls: not run (no credentials in sandbox). Behaviour checked against mock OpenAI-compatible server only.
