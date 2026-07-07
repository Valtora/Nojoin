# ADR-0002: CLI OAuth subscription AI mode

- **Status:** Accepted
- **Date:** 2026-07-07
- **Deciders:** Valtora

## Context

Nojoin resolves AI inference through an install-wide provider (Ollama or a
BYOK API key). A third, **per-user** "usage model" was requested: **CLI OAuth**,
which routes a user's inference through their own Claude Pro/Max subscription via
the Claude Code CLI (driven by `claude-agent-sdk`) running as subprocesses in the
`worker-io` lane.

Using a consumer Claude subscription for programmatic/application inference is
**contrary to Anthropic's consumer terms** — subscription quota is not licensed
for this, and no partner path unlocks it. The owner has chosen to build it anyway
for personal use, explicitly accepting that risk. This ADR records that decision
and the security posture, so the trust boundary and the accepted risk are
documented rather than implicit.

## Decision

We will ship **CLI OAuth as a per-user, opt-in usage model**, off by default,
selectable in Settings > AI only after the user connects their subscription. It
is a **swappable** mode that degrades cleanly to the user's BYOK/Ollama secondary
via `SecondaryLLMBackend` and is **never load-bearing**.

Security posture (the trust boundary):

- **Credential at rest:** the subscription OAuth token is stored encrypted
  (`CliOAuthCredential`, `encrypt_secret`), one row per user, never in
  `User.settings` (unencrypted JSONB), never logged, never returned by the API.
- **Env scrub (non-negotiable, test-locked):** the Agent SDK merges the worker's
  `os.environ` *under* `options.env`, so the CLI subprocess env is built by
  **removing** `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` / `CLAUDE_CODE_USE_*`
  from `os.environ` for the query's duration and injecting only
  `CLAUDE_CODE_OAUTH_TOKEN`. Without this, an install-wide key would out-rank the
  user's subscription token and silently bill the install.
- **Minimal execution surface:** the subprocess runs **tools off**
  (`allowed_tools=[]`), **single-turn** (`max_turns=1`), non-agentic (a tight
  system prompt), under a wall-clock timeout, as the image's **non-root
  `appuser`**. Transcripts are treated as untrusted input.
- **No heavy OS sandbox.** We will *not* add a dedicated low-privilege OS user,
  `resource.setrlimit` caps, or network isolation. Tools-off + single-turn +
  timeout + non-root already neutralise the threat: with no tools, a
  prompt-injected transcript can only produce *text* (contained by the tolerant
  parsers and merely read by the user), not write files, run shell, or make
  network calls. The SDK's native sandbox targets *tool* use (which is off) and
  its network lockdown would break the very connection the CLI needs for
  inference; its OS-level enforcement also does not engage in a non-root
  container.
- **Supply chain:** the io image pins Node.js by digest and drops npm after
  install (the CLI runs directly on `node`), so the image carries **zero CVE
  delta** over the shared worker base; it goes through the same Trivy + cosign +
  SBOM release gates as the other images. The Anthropic CLI/SDK stay unpinned per
  the repo LLM-SDK policy.

## Consequences

- The path is **unsanctioned** and can be broken or enforced against without
  notice. CLI OAuth must remain swappable and non-load-bearing — it is: any
  failure degrades to the user's secondary or surfaces a clear error.
- **Quota economics:** heavy weeks exhaust the plan and pause AI. Usage limits are
  detected from the SDK's `RateLimitEvent` (accurate reset time), recorded on the
  credential, and skipped-when-limited to avoid doomed spawns. A "cheaper model"
  conserves *quota*, not money.
- The io image (Node + Claude Code CLI) is **published** to the org's public
  registry via the release pipeline (owner's decision), which advertises the
  capability under the org namespace.
- **New obligations:** keep the env-scrub unit test green; keep the io image at
  zero CVE delta over the base; keep the mode provider-neutral (OpenAI Codex is a
  future provider).

## Alternatives Considered

- **BYOK / Ollama only (status quo).** No ToS risk, but does not use the
  subscription the owner already pays for. Rejected per the explicit request.
- **Device-code OAuth in-app.** No such flow exists in Claude Code (spike
  finding); replaced by a Nojoin-driven PKCE exchange.
- **Resumable SDK session for live Meeting Edge.** Proven to work, but replays
  unbounded history each call — worse for quota than the existing bounded rolling
  summary. Rejected (see the M4 note in the plan).
- **Heavy OS sandbox (low-priv user, rlimits, network isolation).** Rejected —
  low value given tools-off + non-root, and partly infeasible in a non-root
  container (see Decision).
