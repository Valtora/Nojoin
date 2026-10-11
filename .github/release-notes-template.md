## Nojoin {{VERSION}}

Container images for this release. All images are cosign-signed and ship build-provenance and SBOM attestations; verification steps are in the [deployment guide](https://github.com/Valtora/Nojoin/blob/main/docs/DEPLOYMENT.md#verifying-an-image-before-deploying). Pin to a digest for reproducible deployments.

{{IMAGE_DIGESTS}}

### Install heartbeat

From 2.7.0, each Nojoin installation sends a small heartbeat to `telemetry.nojoin.co.uk`, once when it first starts, once each time it starts on a new version, and once a day. It has three fields.

```json
{"schema": 1, "install_id": "3f2a5c1e-9b4d-4a7f-8e1c-2d6b0a9f4e33", "version": "2.7.0"}
```

The install ID is the random UUID Nojoin already generates for telemetry. Nothing else is sent, and the receiving service discards any other field.

I build Nojoin in my spare time, and I need to know how many installations exist to decide how much of that time it gets. At the moment I can't tell. Optional telemetry has seen almost no installations besides my own since July, while the 2.5.1 images were downloaded more than a hundred times in the 24 days after release. Optional telemetry undercounts by design, because nothing is sent until an administrator agrees. The heartbeat tells me which figure is closer.

Usage diagnostics are unchanged and stay optional, and **Settings > Privacy** still turns them off. They gain one count, recordings made in the last 24 hours.

The heartbeat is never sent if you set `NOJOIN_TELEMETRY_ENABLED=false`, or if you turned telemetry off in Settings before upgrading. If you said no before, that still stands. Blocking `telemetry.nojoin.co.uk` stops it too, and nothing else in Nojoin is affected.

Telemetry data is now kept indefinitely rather than reduced to daily totals after 13 months. `docs/TELEMETRY.md` has the full details (#389).

### Highlights

<!-- Maintainer: one bullet per item, one or two sentences each. What an operator would notice, not how it works. Detail belongs in the docs. Remove the section if a release has nothing to lead with. -->

- **Administrators can cap AI analysis runs.** Set a **Daily AI analysis budget** under **Settings > AI providers**, with a per-user override under Users. Each user can then turn on **Analyse meetings automatically** under **Settings > Recording**, off by default and within the same budget (#385).
- **Delivery figures catch up after a transcript edit.** Opening the Analytics tab re-measures a meeting whose transcript changed since it was measured. **Re-measure delivery after edits** under **Settings > Recording** turns this off (#384).
- **Document batches upload two at a time** and are listed in the order they were queued, not the order they finished (#383).
- **The GPU transcription window is an install-wide setting.** An administrator can shorten the 120 second window for Parakeet and Canary under **Settings > Transcription**, for a card shared with live capture (#382).
- **Short Anthropic calls return text.** Current Claude models think first, and a title or other short call could spend its whole limit thinking and return nothing. These calls now ask for low effort, have more room, and fail with a clear message when there is still no text (#379). Models that still accept a temperature get 0.2, matching OpenAI and Gemini (#380).
- **The worker-io build verifies the CLIs it installs.** It checks the Codex and Claude Code npm packages against the registry's signatures and provenance, and records their versions in the image (#381).
- **Dependency and code-scanning fixes.** postcss-selector-parser moves to 7.1.6 for a quadratic parsing advisory (#378). The chat usage-limit message is built from the reset time alone, so it cannot carry exception text (#377). The Trivy ignore list is empty, since none of its entries still applied (#376).

### Upgrade

Pull the new images and recreate the stack.

```bash
docker compose pull
docker compose up -d
```

- `worker-io` sends the first heartbeat within about fifteen minutes of starting, unless one of the exemptions above applies. Set `NOJOIN_TELEMETRY_ENABLED=false` in `.env` before upgrading if this deployment must not send it.

### Migration

Database migrations run automatically on the first API start after upgrading. Back up your instance before upgrading.

<!-- Maintainer: note any blocking first-boot migration, longer startup, or manual step. Keep it to bullets. -->

- One Alembic revision adds the `ai_analysis_runs` table and a nullable per-user limit on `users`, for the analysis budget. It runs on the first API start and takes moments.
- No change to the example compose file or the nginx configuration. `NOJOIN_TELEMETRY_ENABLED` keeps its name and values, and `false` now also stops the heartbeat.
- The backup format is unchanged.

### Rollback

<!-- Maintainer: state whether rollback is code-only or requires data steps. Default below. -->

- Roll the schema back before the images, because the 2.6.0 API refuses to start against a revision it does not know. With 2.7.0 still running, run `docker compose exec api alembic downgrade b182c2378829`, then redeploy the 2.6.0 image tags. The downgrade drops the AI analysis run log, so each user's count for the day resets.
- 2.6.0 ignores the settings 2.7.0 adds to `config.json`, so the GPU window and the analysis budget return to their defaults, and it sends no heartbeat.

### Known Issues

<!-- Maintainer: list known issues affecting this release, or leave the default. -->

- Carried over from 2.6.0. With Ollama, a meeting whose transcript does not fit the context window fails note generation. Raise the Ollama context window under **Settings > AI providers** if the GPU has room, or use a hosted provider for long meetings. Splitting long meetings into parts is planned.
- Carried over from 2.5.1. Current Anthropic models do not accept a temperature, so their notes and titles can vary more between runs than other providers'.
- Carried over. Overlapping speech is a floor rather than a total (#387 records why it stays that way).
- Carried over. Trivy cannot inspect the Codex binary in the worker-io image. Its package is now verified at build time, and its version should be checked against upstream advisories before each release.

### Browser-Capture Compatibility

<!-- Maintainer: note any change to supported browsers/OSes or capture behaviour. Default below. -->

- No capture code changed in this release. Supported browsers, operating systems and audio sources are unchanged, and shared-audio capture still resolves on Chromium desktop only.

### Changes

{{CHANGELOG}}
