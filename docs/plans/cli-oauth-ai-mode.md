# Implementation Plan: CLI OAuth AI Mode

Status: **M1–M2 implemented; M3–M6 pending.** Branch:
`feat/cli-oauth-ai-mode-plan`. See §9 for the milestone tracker and the M1/M2
delivery notes.

## 1. Goal and scope

Add a third per-user AI "usage model" alongside the existing Ollama and BYOK/API-key
providers: **CLI OAuth**, which routes inference through a user's Claude Pro/Max
subscription via the Claude Code CLI, driven by the Claude Agent SDK, running as
locked-down subprocesses inside the **`worker-io`** container — the GPU-free
network/LLM Celery lane (see §2a).

> **Rebased onto the split-worker `main` (`62ff6d7`, #84).** The worker is now three
> containers (`worker-gpu`, `worker-cpu`, `worker-io`) sharing one image. This plan
> targets `worker-io` specifically; §2a records the baseline and the decisions the split
> forces.

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
Settings > AI (per-user)         worker-io container (GPU-free LLM lane, -Q io)
  usage_model = cli_oauth          ├─ Celery task (generate_notes / refresh_meeting_edge / infer_speakers)
        │                          │     └─ resolve_llm_config(purpose) ─┐
        ▼                          │                                      ▼
  device-code OAuth  ──►  encrypted DB row  ──►  CliConversationManager (new)
  (Nojoin UI)            (CliOAuthCredential)        ├─ per-user CLAUDE_CONFIG_DIR (materialised from DB)
                                                     ├─ locked-down subprocess (low-priv user, tools off)
                                                     ├─ Claude Agent SDK session(s)
                                                     │     • persistent per-meeting session via resumable session_id (Edge)
                                                     │     • fresh session per async task
                                                     └─ async lane capped/queued; Edge lane always slotted
```

`CliLLMBackend(LLMBackend)` is a thin adapter over `CliConversationManager`, slotted
into the existing `get_llm_backend()` factory so the rest of the pipeline is untouched.

## 2a. Split-worker architecture baseline (as of the rebase)

`main` (`62ff6d7`, PR #84) splits the worker into three containers that **share one
image** (`ghcr.io/valtora/nojoin-worker:latest` via the `x-worker-base` anchor in
`docker-compose.example.yml`) and differ only by the Celery queue they consume:

| Container | Queue (`-Q`) | Pool / concurrency | Role |
| --- | --- | --- | --- |
| `worker-gpu` | `gpu` | `solo` / 1 | Heavy ML: `process_recording_task`, embeddings, model downloads. **Only** GPU access. Leave untouched. |
| `worker-cpu` | `cpu` | `prefork` / 3 | ffmpeg transcode, proxy generation, backups. |
| `worker-io` | `io` | `prefork` / 4 | **LLM lane** + Celery Beat (`-B`): `refresh_meeting_edge_task`, `generate_notes_task`, `infer_speakers_task`, embeddings, calendar sync, cleanup. |

Routing lives in `TASK_ROUTES` in [celery_app.py](../../backend/celery_app.py). This
baseline changes three things versus the pre-split plan:

1. **CLI OAuth belongs entirely in `worker-io`.** Every LLM task already routes there;
   CLI inference is GPU-free and network-bound. Nothing CLI-related should reach
   `worker-gpu` or `worker-cpu`.
2. **`task_default_queue = GPU_QUEUE`.** Any *new* CLI Celery task (device-poll,
   credential health-check) **must** be added to `TASK_ROUTES` → `IO_QUEUE`, or it will
   silently run on the serialised single-GPU lane. This is a real footgun.
3. **The warm per-meeting Edge session must outlive a Celery task.** `worker-io` is
   `prefork`, so each `refresh_meeting_edge_task` runs in a forked child — a warm
   in-process session cannot be shared across them. Use the Agent SDK's **resumable
   `session_id`**: persist it (keyed by `recording_id`) and resume per invocation, rather
   than holding a live subprocess in memory. Fresh async tasks need no persistence.

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
  - **Concurrency (within `worker-io`):** the lane is `prefork`/4, so up to four Celery
    children can each be driving a CLI conversation. Process-per-conversation on top of
    that; async spawns are capped per user with a backpressure queue; the live Edge lane
    always gets a slot. A soft cap prevents spawning processes that only 429 against the
    one account. The cap is enforced in the manager (e.g. a Redis-backed per-user
    semaphore), since the four prefork children don't share memory.
  - **Conversation strategy:** hybrid — Edge uses one **resumable Agent SDK session per
    meeting**: persist `session_id` keyed by `recording_id` and resume it each
    `refresh_meeting_edge_task` invocation (a forked child can't hold a live session in
    memory), carrying rolling context alongside the existing rolling-summary. Async tasks
    use a fresh session each call — no persistence needed.
  - **Prompt adaptation:** the bulk of the effort. Each existing prompt template is
    re-tuned for the conversation format and re-validated for strict JSON per feature.
- **Model routing:** pass the per-task model (`cli_model` vs `cli_live_model`) to the SDK
  session. Note in the UI copy that under a subscription this conserves *quota*, not cost.

### D. Auth flow (device-code OAuth)

> **Superseded — see the connect-flow redesign note (§9).** There is no
> device-code flow in Claude Code and server-initiated browser OAuth is
> unsupported, so the connect flow is a **Nojoin-driven PKCE OAuth** exchange (an
> intermediate paste-token cut was replaced after it proved error-prone). The
> `POST /start` / `POST /poll` shapes sketched below are not built; the built
> endpoints are `POST /cli-oauth/start`, `POST /cli-oauth/complete`,
> `GET /cli-oauth/status`, `DELETE /cli-oauth/token`.

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

### G. Worker image packaging (all three lanes share one image)

- **Key constraint:** `worker-gpu`, `worker-cpu`, and `worker-io` all run the **same**
  image ([docker/Dockerfile.worker](../../docker/Dockerfile.worker), a Node-free
  PyTorch/CUDA image). CLI OAuth needs Node.js + Claude Code CLI + the `claude-agent-sdk`
  Python package — but only `worker-io` uses them. Adding them to the shared image bloats
  the already-large GPU/CPU images for no benefit.
- **Decision to make (flag for the next agent):**
  - **(a) Shared image** — add Node LTS + `@anthropic-ai/claude-code` +
    `claude-agent-sdk` to `Dockerfile.worker`. Simplest; one image; ~150–250 MB of Node
    tooling rides along on `worker-gpu`/`worker-cpu` that never use it.
  - **(b) io-specific layer (recommended)** — a small `Dockerfile.worker-io` (or a final
    build stage) that `FROM`s the base worker image and adds the Node/CLI layer; publish
    a second tag and point only `worker-io` at it in compose. Keeps GPU/CPU lean; adds a
    build target and a compose image override.
- Either way: add `claude-agent-sdk` to `requirements/worker.txt` (unpinned per repo
  LLM-SDK policy — [[nojoin-llm-sdk-no-pin]]); create the low-privilege sandbox OS user in
  whichever image carries the CLI; keep all CLI tooling out of the API image.
- Compose: only `worker-io` needs the CLI. If (b), override its `image:`; the
  `x-worker-base` anchor stays the base for GPU/CPU.

## 5. Dependency propagation

- `requirements/worker.txt`: add `claude-agent-sdk` (+ Node via the worker image, §G).
- Any new CLI Celery task **must** be routed to `IO_QUEUE` in `TASK_ROUTES`
  ([celery_app.py](../../backend/celery_app.py)) — `task_default_queue` is `GPU_QUEUE`,
  so unrouted tasks land on the serialised GPU lane. Register the credential health-check
  under Celery Beat, which `worker-io` already owns (`-B`).
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

1. **M1 — plumbing, no CLI: DELIVERED.** `usage_model` field + Settings surface +
   resolver short-circuit returning a *stub* CLI config; migration +
   `CliOAuthCredential`; encrypt/decrypt helpers. Ships behind the selector, does
   nothing yet. See the M1 delivery note below.
2. **M2 — auth: DELIVERED (paste-token, not device-code).** Connect/disconnect
   UI + status. The spike found no device-code flow, so this is a pasted
   long-lived `setup-token`. See the M2 delivery note below.
3. **M3 — backend + manager:** Worker image (Node + SDK), `CliLLMBackend` +
   `CliConversationManager` for async tasks only (notes/title/speaker/chat).
4. **M4 — Meeting Edge:** persistent-conversation lane + concurrency caps.
5. **M5 — limit handling:** foreground modal + background pause/notify + health check.
6. **M6 — docs + hardening:** sandbox rlimits, revoke cleanup, ADR, doc sweep.

### M1 delivery note (as built)

Resolves open-decision §3.1 in favour of the **narrow** approach and the two
build-time choices from the handover:

- **`usage_model` is a standalone per-user field**, deliberately kept **out** of
  `SYSTEM_LLM_FIELDS` / `INSTALL_WIDE_AI_SETTING_KEYS`, so it survives the user
  settings merge untouched and never leaks from owner to user. `cli_model` /
  `cli_live_model` are likewise plain per-user keys (CLI OAuth is inherently
  per-user; there is no install-wide CLI). The install-wide `llm_provider` enum
  is unchanged.
- **Resolver** (`backend/utils/llm_config.py`): `_maybe_cli_config` /
  `_to_cli_config` short-circuit both `resolve_llm_config` entry points when
  `usage_model == cli_oauth`, returning `provider="cli"` with the per-task model
  (`cli_live_model` for Meeting Edge, falling back to `cli_model`).
- **Degrade cleanly (decision):** the CLI config carries the resolved secondary
  through, so `SecondaryLLMBackend` falls back to the user's configured provider
  when the stub raises; only users with no fallback see the error.
- **Stub backend** (`backend/processing/cli_backend.py`): `CliLLMBackend` raises
  `CliOAuthUnavailableError` from every method; wired via the `cli` branch in
  `get_llm_backend`.
- **Storage:** `CliOAuthCredential` (`backend/models/cli_oauth.py`) +
  encrypted persistence (`backend/services/cli_oauth/persistence.py`,
  `CliTokenBundle`). Migration `b30103edc480` chains from the real head
  `d9e2f4a6c8b1` (the split-worker calendar-push migration) — **not**
  `354c15ea791e`, which already has a child; a single-head guard test now
  enforces this.
- **Frontend (decision):** the "AI usage model" selector is visible in
  Settings > AI with **CLI OAuth disabled** ("coming soon"); type fields added,
  no auth panel yet.
- **Deferred as planned:** live `alembic upgrade`/`downgrade` on Postgres (DDL
  verified Postgres-valid via dialect render: `id BIGSERIAL`, FK CASCADE,
  UNIQUE); the user-facing doc sweep (§8).

### M2 delivery note (as built)

Resolves open-decision §6.4 (device-code capture) — via a **spike** that changed
the premise. Key spike findings (Claude Code 2.1.x, Agent SDK docs):

- **No device-code flow exists.** OAuth is browser-only (`claude auth login` /
  `claude setup-token`); server-initiated login is unsupported and the headless
  code-paste fallback is fragile (undocumented stdout/stdin) with a documented
  headless-refresh lockout on Linux servers
  ([claude-code#47754](https://github.com/anthropics/claude-code/issues/47754)).
- **Two token types:** `setup-token` prints a **~1-year, refresh-free** token
  (CI-oriented); `auth login` stores a **~60-min access + refresh** pair (subject
  to the lockout). The long-lived token is the safer one for a server.
- **Credential store:** `~/.claude/.credentials.json` (0600), relocatable via
  `CLAUDE_CONFIG_DIR`; the subscription block is
  `claudeAiOauth.{accessToken, refreshToken, expiresAt(ms), subscriptionType}`
  — maps 1:1 onto the M1 `CliOAuthCredential` columns.
- **Precedence trap (for M3):** the SDK ranks `ANTHROPIC_API_KEY` (and cloud
  vars) **above** the subscription credential. Nojoin's worker env may hold an
  install-wide `ANTHROPIC_API_KEY`; the CLI subprocess **must** scrub
  `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` / `CLAUDE_CODE_USE_*` or the
  user's inference silently bills the install key, not their subscription.
- **Usage limits:** generic 401/429, **no reset-time metadata** (M5 stays
  best-effort, as hedged).

Decision (owner): **paste a `setup-token`** — simplest and most robust; no
server browser, no stateful auth subprocess, no refresh-lockout risk. As built:

- **Endpoints** (`backend/api/v1/endpoints/cli_oauth.py`, registered under
  `/cli-oauth`, all `Depends(get_current_user)`): `GET /status`,
  `PUT /token` (validate → `upsert_credential`, `status=active`),
  `DELETE /token`. Token is **write-only** — never logged, never returned.
- **Frontend:** `lib/api/cliOauth.ts` + `CliOAuthStatus` type + a
  `CliOAuthPanel` (status / paste-token connect / disconnect) in Settings > AI.
  The usage-model **selector's CLI option stays disabled** — M2 is connect-only;
  M3 turns on routing.
- **Deferred to M3:** real token validation (needs the SDK to make a call);
  materialising the token as `CLAUDE_CODE_OAUTH_TOKEN` into the scrubbed
  subprocess env; enabling CLI as an active usage model.

### Connect-flow redesign — Nojoin-driven PKCE OAuth (supersedes the paste-token)

The paste-token cut proved error-prone: `claude setup-token` is an interactive
PKCE flow, and users naturally paste the short-lived *authorization code* (which
401s) rather than the final token. The connect flow was redesigned to a
**Nojoin-driven PKCE OAuth** exchange (owner's choice over piping to a held CLI
subprocess). Spike-confirmed mechanics:

- **Authorize:** `https://claude.com/cai/oauth/authorize`, public client_id
  `9d1c250a-…` (baked into the CLI, not a secret), `redirect_uri`
  `https://platform.claude.com/oauth/code/callback`, `scope=user:inference`,
  `code_challenge_method=S256`, `code=true` (headless — Anthropic's page shows
  the code to copy).
- **Token exchange:** `POST https://platform.claude.com/v1/oauth/token`
  (**not** `console.anthropic.com` → 404), **form-encoded** (JSON → 400), body
  `grant_type=authorization_code, code` (strip any `#…`)`, redirect_uri,
  client_id, code_verifier, state`. Public client, no secret. Slow → 120s
  timeout.
- **Lifecycle:** yields an **~8h `access_token` + rotating `refresh_token`** (not
  the ~1y setup-token). Refresh: `grant_type=refresh_token` at the same endpoint;
  each refresh rotates the refresh token (persist the new one). Headless refresh
  has a documented 429/lockout mode → refresh on-demand, degrade to
  `needs_reauth`, never block.

As built (replaces the paste-token endpoints/panel):

- `backend/services/cli_oauth/oauth.py`: PKCE gen, authorize-URL builder,
  `exchange_code` / `refresh_tokens`, tolerant `parse_pasted_code` (bare /
  `code#state` / full URL), and Redis pending-state helpers (verifier + state,
  10-min TTL — **no schema change**; M1's `access/refresh/expires` columns fit).
- Endpoints: `POST /cli-oauth/start` (PKCE + authorize URL, stash pending),
  `POST /cli-oauth/complete` (validate state, exchange, store tokens encrypted);
  `GET /status` and `DELETE /token` retained; the paste `PUT /token` removed.
- Frontend `CliOAuthPanel`: Connect → skinned modal with a real "Grant access"
  link (a plain `<a>` — avoids the popup-blocker that eats `window.open` after an
  await) → paste code → complete.

**Proven end-to-end (live smoke):** the io image + this flow authenticated a real
`sk-ant-oat01-` token against the subscription and returned the expected
completion, with the env-scrub (`ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN`/
`CLAUDE_CODE_USE_*`) confirmed stripped from the subprocess env. The default
model observed was `claude-sonnet-5`.

## 10. Open risks carried forward (not blockers, but track)

- **Unsanctioned auth path** can break or be enforced against without notice — keep CLI
  OAuth swappable and non-load-bearing.
- **Quota economics:** heavy-meeting weeks exhaust the plan limit and pause AI; "cheaper
  model" conserves quota, not money — set user expectations in the UI copy.
- **Prompt adaptation** per feature is the largest single effort; budget for it.
- **Worker image bloat** — the three lanes share one image, so Node/CLI tooling added
  naively rides along on `worker-gpu`/`worker-cpu`; prefer the io-specific layer (§G).
- **GPU-lane footgun** — `task_default_queue = GPU_QUEUE`; forget to route a new CLI task
  to `IO_QUEUE` and it serialises behind the ML pipeline on the one 8 GB card.

## 11. Not in this plan (explicitly deferred)

- Codex/OpenAI CLI support.
- CLI as a *secondary* provider.
- Cross-mode per-feature mixing beyond the primary usage-model + opt-in fallback.
