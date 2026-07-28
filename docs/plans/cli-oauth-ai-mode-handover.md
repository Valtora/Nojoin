# Handover: CLI OAuth AI Mode

Audience: the next agent (or engineer) picking up this feature. Read this first, then
[cli-oauth-ai-mode.md](cli-oauth-ai-mode.md) (the implementation plan). Nothing is built
yet — both docs are design-only.

## 1. TL;DR

Nojoin is adding a third per-user AI **usage model** — **CLI OAuth** — alongside Ollama
and BYOK. It routes inference through a user's Claude Pro/Max subscription via the Claude
Code CLI, driven by the Claude Agent SDK, as locked-down subprocesses in the **`worker-io`**
container. The design decision tree is fully resolved; the plan is written and has just
been **rebased onto the split-worker `main`**. Your job is to execute the plan, starting at
milestone **M1**, following the nojoin-dev workflow (plan → approval → targeted build →
tests → docs).

> **Accepted risk, stated plainly:** using a consumer Claude subscription this way is
> contrary to Anthropic's consumer terms and can break or be enforced against without
> notice. The project owner has chosen to build it anyway. Non-negotiable design
> constraint: **CLI OAuth must stay a swappable mode that degrades cleanly to BYOK/Ollama
> and is never a load-bearing dependency.**

## 2. Current state

- **Branch:** `feat/cli-oauth-ai-mode-plan`, rebased onto `main` (`62ff6d7`, PR #84 — the
  split-worker architecture). One docs-only commit; no code, migrations, deps, or image
  changes. Not pushed.
- **Deliverables on the branch:** [cli-oauth-ai-mode.md](cli-oauth-ai-mode.md) (plan) and
  this handover.
- **Background context** also lives in the maintainer's agent memory
  (`nojoin-cli-oauth-ai-mode.md`) if you have access to it; this handover reproduces what
  you need.

## 3. How the design was reached (so you don't re-litigate)

The plan came out of a full grill-me interrogation. These decisions are **settled** — do
not reopen them without the owner:

| Topic | Decision |
| --- | --- |
| Direction | Build the CLI-container path; ToS risk accepted |
| Provider (first cut) | Claude Code only (Codex deferred) |
| Feature scope | All AI features incl. live Meeting Edge, with per-task model routing configurable in Settings > AI |
| Topology | Per-user sessions as **subprocesses inside `worker-io`** (not separate containers, no Docker socket) |
| Driver | Claude Agent SDK (authenticates via the logged-in Claude Code credential) |
| Concurrency | Process-per-conversation; async lane capped/queued; live Edge always slotted |
| Conversation strategy | Hybrid — resumable per-meeting session for Edge; fresh session per async task |
| Auth | Device-code OAuth surfaced in the Nojoin UI |
| Credential storage | Encrypted at rest in a dedicated table; per-user `CLAUDE_CONFIG_DIR` |
| Sandboxing | Locked-down subprocess: low-priv OS user, Agent SDK tools disabled, resource caps |
| Limit handling | Foreground (chat) = fall-back-or-pause modal; background = pause + notify |

## 4. What the rebase changed (the split-worker re-analysis)

`main` now runs three worker containers sharing one image, differing only by Celery queue
(`TASK_ROUTES` in [celery_app.py](../../backend/celery_app.py)):

- `worker-gpu` (`-Q gpu`, solo/1, only GPU) — heavy ML. **Do not touch.**
- `worker-cpu` (`-Q cpu`, prefork/3) — ffmpeg/proxy/backups.
- `worker-io` (`-Q io`, prefork/4, owns Celery Beat) — **all LLM tasks already run here**
  (`refresh_meeting_edge_task`, `generate_notes_task`, `infer_speakers_task`).

Three consequences fold into the plan (details in plan §2a, §4.C, §4.G, §5):

1. **CLI OAuth lives entirely in `worker-io`.** Nothing CLI reaches gpu/cpu.
2. **`task_default_queue = GPU_QUEUE` is a footgun.** Any new CLI Celery task not
   explicitly routed to `IO_QUEUE` runs on the serialised GPU lane. Always add routes.
3. **The warm Edge session can't be in-process** (prefork forks a child per task). Use the
   Agent SDK's resumable `session_id`, persisted per `recording_id`, resumed each refresh.
4. **One shared worker image** → adding Node/Claude Code naively bloats gpu/cpu. Prefer an
   io-specific image layer (plan §4.G option (b)).

## 5. Integration surface map (verified file:line)

- LLM contract to implement: `LLMBackend` — [llm_services.py:92](../../backend/processing/llm_services.py)
  (methods: `infer_speaker_suggestions`, `generate_meeting_notes`,
  `generate_meeting_intelligence`, `generate_meeting_edge`, `infer_meeting_title`,
  `ask_question_about_meeting`, `ask_question_streaming`, `list_models`, `validate_api_key`).
- Factory to extend: `get_llm_backend()` — [llm_services.py:2433](../../backend/processing/llm_services.py).
- Fallback wrapper: `SecondaryLLMBackend` / `get_llm_backend_with_secondary` —
  [llm_services.py:2450](../../backend/processing/llm_services.py).
- Config merge (per-user vs install-wide): `resolve_llm_config` / `_merge_llm_config` /
  `INSTALL_WIDE_ONLY_USER_LLM_FIELDS` — [llm_config.py:62](../../backend/utils/llm_config.py),
  [:123](../../backend/utils/llm_config.py), [:339](../../backend/utils/llm_config.py).
- Celery routing: `TASK_ROUTES`, `task_default_queue` — [celery_app.py](../../backend/celery_app.py).
- LLM task call sites: `generate_notes_task`, `infer_speakers_task`
  ([worker/tasks/intelligence.py](../../backend/worker/tasks/intelligence.py)),
  `refresh_meeting_edge_task`, `process_recording_task`
  ([worker/tasks/pipeline.py](../../backend/worker/tasks/pipeline.py)).
- Encryption to mirror: `encrypt_secret`/`decrypt_secret` —
  [core/encryption.py](../../backend/core/encryption.py); storage pattern to copy:
  `CalendarConnection.{access,refresh}_token_encrypted`
  ([models/calendar.py:74](../../backend/models/calendar.py)),
  writes in `services/calendar_service/persistence.py` / `.../oauth.py`.
- User settings JSONB (unencrypted — do NOT put the token here):
  [models/user.py:32](../../backend/models/user.py).
- Settings API + provider validator: [api/v1/endpoints/settings.py:136](../../backend/api/v1/endpoints/settings.py).
- Alembic versions dir + naming: `backend/alembic/versions/<12-hex>_desc.py`.
- Worker image: single [docker/Dockerfile.worker](../../docker/Dockerfile.worker),
  shared via the `x-worker-base` anchor in `docker-compose.example.yml`.
- Frontend: [AiRoutingSection.tsx](../../frontend/src/components/settings/AiRoutingSection.tsx),
  accessors [aiSettingsModels.ts](../../frontend/src/components/settings/aiSettingsModels.ts),
  types [types/index.ts:320](../../frontend/src/types/index.ts), api
  [lib/api/settings.ts](../../frontend/src/lib/api/settings.ts).

## 6. Decisions still open (resolve before/at build)

1. **Worker image strategy** — shared image vs io-specific layer (plan §4.G). Recommend
   the io-specific layer.
2. **Per-user provider scoping** — confirm the narrow `usage_model` field vs relaxing
   `INSTALL_WIDE_ONLY_USER_LLM_FIELDS` (plan §3.1). Plan assumes the narrow field.
3. **Cross-child concurrency cap mechanism** — prefork children don't share memory, so the
   per-user "async cap + Edge slot" needs a shared store (recommend Redis, already
   present). Confirm before M4.
4. **Device-code OAuth capture** — verify how the Claude Code CLI/Agent SDK device flow is
   driven headlessly and what the token file layout under `CLAUDE_CONFIG_DIR` is; this
   underpins M2 and needs a spike before committing the endpoint shapes.

## 7. Guardrails (nojoin-dev + repo policy)

- **Workflow:** plan → explicit approval before mutating code → targeted changes matching
  existing patterns → run checks → update docs. Work only on a task branch, never `main`.
- **Do not modify `worker-gpu`** behaviour or the GPU pipeline. Route all new tasks to
  `IO_QUEUE`.
- **Frontend:** route API calls through `frontend/src/lib/api.ts`; keep shared state in the
  existing Zustand patterns; avoid `any`; run `cd frontend && npm run build` after any
  `frontend/src/**` change; update shared TS types before wiring fields.
- **Backend/worker:** keep heavy work in Celery tasks; import heavy libs inside task
  functions; use the existing config system (no parallel config store); credentials only
  via the encrypted table + `DATA_ENCRYPTION_KEY`.
- **Deps:** don't hard-pin `claude-agent-sdk` (repo LLM-SDK policy).
- **Commits (owner preference):** Conventional Commits, single-line message, **omit** the
  `Co-Authored-By: Claude` trailer. Don't push/pull automatically.
- **Release validation:** before any release tag, build + Trivy-scan images locally.

## 8. Recommended first step (M1 — safe, no behaviour change)

M1 ships the plumbing behind the selector doing nothing; existing users are unaffected.

1. `CliOAuthCredential` model (`backend/models/cli_oauth.py`) + Alembic migration chaining
   from the current head; encrypt/decrypt helpers mirroring the calendar pattern.
2. `usage_model` field: add to `SettingsUpdate` and the merged settings, **excluded** from
   `INSTALL_WIDE_ONLY_USER_LLM_FIELDS`; `cli_model`/`cli_live_model` keys.
3. `resolve_llm_config` short-circuit: when `usage_model == cli_oauth`, return a
   `ResolvedLLMConfig` with `provider="cli"` (a stub `CliLLMBackend` raising a clear
   "not yet available" is fine at M1) and the user's BYOK/Ollama as opt-in secondary.
4. Frontend: add the `Usage model` selector + `usage_model`/`cli_model`/`cli_live_model`
   to the `Settings` type; no auth panel yet.
5. Tests: config-merge unit tests (CLI path vs fallthrough), credential encrypt/decrypt
   round-trip, migration up/down. `npm run build`.

Then M2 (auth), M3 (worker-io image + real `CliLLMBackend`/manager for async), M4 (Edge),
M5 (limit handling), M6 (docs + hardening) — see plan §9.

## 9. Verification expectations

- Backend: `pytest` for the touched areas; migration up/down against a seeded DB.
- Frontend: `npm run build` green; type-check clean.
- Manual (from M2 on): device-code connect in Settings; end-to-end recording on CLI OAuth;
  force a limit error → confirm foreground modal and background pause+notify; disconnect
  wipes the per-user `CLAUDE_CONFIG_DIR`.
- Do not rely on automated tests alone where the live/Meeting-Edge path changes — provide
  manual smoke steps.
