# Implementation Plan: CLI OAuth AI Mode

Status: **Draft for approval — no code written.** Branch: `feat/cli-oauth-ai-mode-plan`.

## 1. Goal and scope

Add a third per-user AI "usage model" alongside the existing Ollama and BYOK/API-key
providers: **CLI OAuth**, which routes inference through a user's Claude Pro/Max
subscription via the Claude Code CLI, driven by the Claude Agent SDK, running as
locked-down subprocesses inside the Worker container.

First cut:

- **Provider:** Claude Code only. (Codex/OpenAI deferred to a later phase.)
- **Features:** all AI features, including live Meeting Edge, with per-task model
  routing configurable in Settings > AI (reusing the existing per-provider model
  surface). Meeting Edge uses a persistent conversation per meeting; async tasks
  (notes, title, speaker inference, non-streaming chat) use fresh conversations.
- **Isolation:** per-user Claude Code sessions as subprocesses inside the Worker
  container, each with its own `CLAUDE_CONFIG_DIR`.
- **Auth:** device-code OAuth surfaced in the Nojoin UI; credential encrypted at rest.
- **Limit handling:** foreground (chat) shows a fall-back-or-pause modal; background
  tasks obey a per-user default (pause + notify, per the resolved design).

> **Accepted risk (see §12):** subscription-quota use through the CLI is contrary to
> Anthropic's consumer terms. This plan builds the capability the user has chosen to
> pursue; it does not endorse the ToS position. CLI OAuth must remain a *swappable*
> mode that degrades cleanly to BYOK/Ollama and is never a load-bearing dependency.

## 2. Architecture at a glance

```
Settings > AI (per-user)         Worker container
  usage_model = cli_oauth          ├─ Celery task (generate_notes / meeting_edge / …)
        │                          │     └─ resolve_llm_config(purpose) ─┐
        ▼                          │                                      ▼
  device-code OAuth  ──►  encrypted DB row  ──►  CliConversationManager (new)
  (Nojoin UI)            (CliOAuthCredential)        ├─ per-user CLAUDE_CONFIG_DIR (materialised from DB)
                                                     ├─ locked-down subprocess (low-priv user, tools off)
                                                     ├─ Claude Agent SDK session(s)
                                                     │     • persistent conversation per live meeting (Edge)
                                                     │     • fresh conversation per async task
                                                     └─ async lane capped/queued; Edge lane always slotted
```

`CliLLMBackend(LLMBackend)` is a thin adapter over `CliConversationManager`, slotted
into the existing `get_llm_backend()` factory so the rest of the pipeline is untouched.

## 3. Compatibility tensions to resolve first

Two facts about the current code shape the whole plan and must be decided before build:

### 3.1 Provider selection is install-wide, not per-user

`INSTALL_WIDE_ONLY_USER_LLM_FIELDS` ([llm_config.py:62](../../backend/utils/llm_config.py))
strips `llm_provider`, API keys, and `ollama_api_url` from user settings during merge —
today only *live-model* selection is user-scoped, and `SettingsUpdate`
([settings.py:136](../../backend/api/v1/endpoints/settings.py)) further gates
install-wide fields to admins. Our design needs the *usage model* to be genuinely
per-user.

**Decision for the plan:** introduce a distinct per-user field `usage_model`
(`ollama | byok | cli_oauth`) that is **not** in `INSTALL_WIDE_ONLY_USER_LLM_FIELDS`,
rather than making the existing install-wide `llm_provider` user-settable. When
`usage_model == cli_oauth`, the resolver short-circuits to a CLI-backed
`ResolvedLLMConfig` for that user, ignoring the install-wide provider. This keeps the
existing owner/admin install-wide provider semantics intact for Ollama/BYOK and adds
CLI OAuth as an explicit per-user opt-in without loosening the current security model.

### 3.2 The credential fits neither existing store

Install-wide settings persist to `config_manager` (filesystem); user settings persist
to `User.settings` JSONB **unencrypted** ([user.py:32](../../backend/models/user.py)).
A subscription bearer token must be encrypted at rest. Mirror `CalendarConnection`
([calendar.py:74](../../backend/models/calendar.py)): a dedicated table with
`{access,refresh}_token_encrypted` columns via `encrypt_secret`/`decrypt_secret`
([encryption.py](../../backend/core/encryption.py)). Do **not** store the token in
`User.settings`.

## 4. Workstreams

### A. Data model and credential storage

- New model `CliOAuthCredential` (new file `backend/models/cli_oauth.py`), one row per
  `(user_id, provider)`:
  - `user_id` (FK, indexed), `provider` (`"claude_code"`), `status`
    (`active | needs_reauth | revoked`), `access_token_encrypted`,
    `refresh_token_encrypted`, `token_expires_at`, `oauth_client_id` (if the flow needs
    it), `last_refreshed_at`, `usage_limited_until` (nullable, best-effort from CLI
    error text), `created_at`, `updated_at`.
- Alembic migration under `backend/alembic/versions/` chaining from the current head,
  following the existing naming (`<12-hex>_add_cli_oauth_credential.py`).
- Read/write helpers that encrypt on write, decrypt on read, exactly as
  `services/calendar_service/persistence.py` and `.../oauth.py` do.

### B. Config and provider plumbing

- Add `usage_model` to the per-user Settings surface (default unset → falls back to the
  existing install-wide provider, i.e. no behaviour change for current users).
- Add `cli_model` / `cli_live_model` to `SYSTEM_LLM_FIELDS`, and register
  `cli` in `LIVE_MODEL_FIELDS_BY_PROVIDER` (`cli_live_model`). Secondary variants only
  if CLI is ever allowed as a *secondary* provider — recommend **not** in the first cut.
- Extend `resolve_llm_config` / `_merge_llm_config`: when the resolved per-user
  `usage_model == cli_oauth`, return a `ResolvedLLMConfig` with `provider="cli"` and the
  per-task model from `cli_model` / `cli_live_model` (honouring `purpose` for Meeting
  Edge), plus the user's chosen fallback (`byok`/`ollama` config) surfaced as the
  secondary. The CLI credential is fetched from `CliOAuthCredential`, not `merged`.
- Extend `get_llm_backend()` ([llm_services.py:2433](../../backend/processing/llm_services.py))
  with a `cli` branch returning `CliLLMBackend`, and the `SettingsUpdate` provider
  validator ([settings.py:139](../../backend/api/v1/endpoints/settings.py)) if `cli`
  ever appears as a provider string. (Prefer routing via `usage_model`, keeping the
  install-wide `llm_provider` enum unchanged.)

### C. CliLLMBackend + CliConversationManager (the core new component)

- `CliLLMBackend(LLMBackend)` implements the full contract
  ([llm_services.py:92](../../backend/processing/llm_services.py)):
  `infer_speaker_suggestions`, `generate_meeting_notes`, `generate_meeting_intelligence`,
  `generate_meeting_edge`, `infer_meeting_title`, `ask_question_about_meeting`,
  `ask_question_streaming`, `list_models`, `validate_api_key`. Each method builds the
  adapted prompt and delegates to the conversation manager; JSON responses go through the
  existing tolerant parsers (`parse_*` statics already on `LLMBackend`).
- `CliConversationManager` (new module `backend/processing/cli/`):
  - **Driver:** Claude Agent SDK, single-turn, tools disabled, tight non-agentic system
    prompt (so it behaves as an inference endpoint, not a coding agent). Auth is the
    user's Claude Code credential — no `ANTHROPIC_API_KEY` in the subprocess env.
  - **Per-user isolation:** materialise the decrypted credential into a per-user
    `CLAUDE_CONFIG_DIR` (`{data}/cli-oauth/<user_id>/`), 0700, cleaned on revoke.
  - **Sandboxing:** run as a dedicated low-privilege OS user; no network/file tools;
    per-process CPU/memory caps (e.g. `resource.setrlimit` / a wrapper); transcripts
    treated as untrusted input.
  - **Concurrency:** process-per-conversation. Async lane is capped per user with a
    backpressure queue; the live Edge lane always gets a slot. A soft cap prevents
    spawning processes that only 429 against the one account.
  - **Conversation strategy:** hybrid — one persistent conversation per live meeting for
    Edge (carrying rolling context alongside the existing rolling-summary), fresh
    conversation per async task.
  - **Prompt adaptation:** the bulk of the effort. Each existing prompt template is
    re-tuned for the conversation format and re-validated for strict JSON per feature.
- **Model routing:** pass the per-task model (`cli_model` vs `cli_live_model`) to the SDK
  session. Note in the UI copy that under a subscription this conserves *quota*, not cost.

### D. Auth flow (device-code OAuth)

- Backend endpoints under `backend/api/v1/endpoints/` (new `cli_oauth.py`):
  - `POST /cli-oauth/start` → initiates the Claude Code device-code flow, returns the
    verification URL + user code, persists a pending record.
  - `POST /cli-oauth/poll` (or server-side polling) → on success, encrypts and stores the
    tokens in `CliOAuthCredential`, sets `status=active`.
  - `DELETE /cli-oauth` → revoke: delete the row and wipe the per-user `CLAUDE_CONFIG_DIR`.
  - `GET /cli-oauth/status` → `active | needs_reauth | revoked | usage_limited` + best-effort
    reset time.
- Refresh: rely on the CLI/SDK auto-refresh where the refresh token is present; a
  scheduled health check (Celery Beat, alongside calendar sync) detects
  `needs_reauth` and surfaces it in Settings.

### E. Frontend Settings > AI

- Add a **Usage model** selector (`Ollama | BYOK | CLI OAuth`) above the existing provider
  UI in [AISettings.tsx](../../frontend/src/components/settings/AISettings.tsx); when
  `cli_oauth` is chosen, show the CLI auth panel (connect / status / disconnect) and the
  per-task model pickers (`cli_model`, `cli_live_model`), reusing
  [aiSettingsModels.ts](../../frontend/src/components/settings/aiSettingsModels.ts)
  accessor patterns.
- Add fields to the `Settings` type
  ([types/index.ts:320](../../frontend/src/types/index.ts)): `usage_model`,
  `cli_model`, `cli_live_model`, plus a background-limit preference.
- New `api.ts` functions for the `/cli-oauth/*` endpoints (mirroring
  [settings.ts](../../frontend/src/lib/api/settings.ts) / models.ts patterns). CLI model
  list can be a static curated list first, or a `list_models` proxy later.
- **Limit modal** (foreground chat): on a `usage_limited` error, present "fall back to
  <BYOK/Ollama>" or "pause AI until reset (<reset time if known>)".
- Run `npm run build` after any `frontend/src/**` change (repo requirement).

### F. Limit handling and fallback

- Detect subscription limit / auth failure from the Agent SDK error surface; parse the
  reset time best-effort from the CLI error text (advisory only).
- **Foreground (chat):** raise a typed error the frontend renders as the modal above.
- **Background (notes/title/speaker):** obey the per-user default — first cut: **pause +
  notify** (record the task as skipped-pending, surface a notification, allow manual
  re-trigger after reset). Do **not** auto-answer a modal for unattended work.
- Reuse `SecondaryLLMBackend`
  ([llm_services.py:2450](../../backend/processing/llm_services.py)) only where the user
  has opted into automatic fallback; otherwise the pause/notify path takes over.

### G. Worker image packaging

- The Worker image is pure Python PyTorch, **no Node.js**
  ([docker/Dockerfile.worker](../../docker/Dockerfile.worker)). CLI OAuth needs
  Node.js + Claude Code CLI + the Claude Agent SDK Python package.
- Add Node.js (pinned LTS) + `@anthropic-ai/claude-code` to the runtime stage; add
  `claude-agent-sdk` to `requirements/worker.txt` (unpinned per repo LLM-SDK policy).
  Create the low-privilege sandbox OS user in the image.
- Guard the image-size/GPU-base impact; keep CLI tooling out of the API image.

## 5. Dependency propagation

- `requirements/worker.txt`: add `claude-agent-sdk` (+ transitively Node via Dockerfile).
- New Alembic head; `backend.startup_migrations` runs it on boot — verify the cutover
  path ([entrypoint / startup_canonical_cutover]) is unaffected.
- Shared TS types updated before wiring UI fields (repo rule: no `any`).
- `config_manager` schema/defaults for the new keys.

## 6. Compatibility impact

- **No behaviour change for existing users:** `usage_model` unset → existing install-wide
  provider path is untouched; `resolve_llm_config` only diverges when a user explicitly
  opts into `cli_oauth`.
- `SecondaryLLMBackend` and `resolve_llm_config` remain the resolution spine; CLI slots in
  as a primary with the user's BYOK/Ollama as the (opt-in) secondary.
- Security model preserved: install-wide provider/keys stay admin-only; per-user CLI is a
  new, explicitly-scoped surface with its own encrypted store.

## 7. Testing plan

- **Unit:** `_merge_llm_config`/`resolve_llm_config` returns a CLI config when
  `usage_model=cli_oauth` and falls through otherwise; credential encrypt/decrypt
  round-trip; `CliLLMBackend` maps each `LLMBackend` method and parses JSON via existing
  parsers; limit-error classification.
- **Integration (mocked SDK):** conversation manager concurrency (Edge slot preserved
  under async backpressure); per-user `CLAUDE_CONFIG_DIR` isolation (user A never reads
  B's credential); fallback/pause paths.
- **Migration:** upgrade/downgrade against a seeded DB.
- **Frontend:** `npm run build`; type checks; the usage-model selector + limit modal.
- **Manual smoke:** device-code connect in Settings; run a recording end-to-end on
  CLI OAuth; force a limit error and confirm the foreground modal and background
  pause+notify; disconnect wipes the config dir.

## 8. Docs to update

- `docs/ARCHITECTURE.md` (new usage-model concept + conversation manager),
  `docs/SECURITY.md` (per-user credential store, sandbox posture, accepted-risk note),
  `docs/DEPLOYMENT.md` (Worker image now carries Node + Claude Code; new `.env`/limits),
  `docs/USAGE.md` (Settings > AI usage-model + connect flow). Consider an ADR
  (`docs/adr/0002-*`) recording the accepted-ToS decision explicitly.

## 9. Suggested sequencing (milestones)

1. **M1 — plumbing, no CLI:** `usage_model` field + Settings surface + resolver
   short-circuit returning a *stub* CLI config; migration + `CliOAuthCredential`;
   encrypt/decrypt helpers. Ships behind the selector, does nothing yet.
2. **M2 — auth:** device-code endpoints + connect/disconnect UI + status.
3. **M3 — backend + manager:** Worker image (Node + SDK), `CliLLMBackend` +
   `CliConversationManager` for async tasks only (notes/title/speaker/chat).
4. **M4 — Meeting Edge:** persistent-conversation lane + concurrency caps.
5. **M5 — limit handling:** foreground modal + background pause/notify + health check.
6. **M6 — docs + hardening:** sandbox rlimits, revoke cleanup, ADR, doc sweep.

## 10. Open risks carried forward (not blockers, but track)

- **Unsanctioned auth path** can break or be enforced against without notice — keep CLI
  OAuth swappable and non-load-bearing.
- **Quota economics:** heavy-meeting weeks exhaust the plan limit and pause AI; "cheaper
  model" conserves quota, not money — set user expectations in the UI copy.
- **Prompt adaptation** per feature is the largest single effort; budget for it.
- **Worker image bloat** and a second runtime (Node) in a GPU Python image.

## 11. Not in this plan (explicitly deferred)

- Codex/OpenAI CLI support.
- CLI as a *secondary* provider.
- Cross-mode per-feature mixing beyond the primary usage-model + opt-in fallback.
