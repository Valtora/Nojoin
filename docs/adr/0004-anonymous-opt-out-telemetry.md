# ADR-0004: Anonymous opt-out telemetry

- **Status:** Accepted
- **Date:** 2026-07-25
- **Deciders:** Valtora

## Context

Nojoin is self-hosted and invite-gated, so there is no signal at all that a deployment exists. Download counts measure curiosity rather than use, GitHub stars measure neither, and issues are only opened by the small fraction of users who hit a problem. Every product decision — which providers to prioritise, whether the ONNX ASR engines are adopted, whether a feature is worth maintaining — is currently made without evidence.

This touches two core contracts. It adds an **outbound data flow** from an application whose entire premise is that your meeting content stays on your own server, which is a trust-boundary change. It also adds **operator-facing configuration** that must be discoverable and honestly documented.

The constraint that shapes everything below is reputational rather than technical. A privacy-first self-hosted tool that phones home badly does more damage to its own credibility than the data is worth. The design therefore has to be defensible line by line, not merely compliant.

## Decision

We will collect anonymous, aggregate telemetry, on by default, with several independent ways to opt out.

**Anonymity is structural, not promised.** The only identifier is a UUID4 generated on the user's own server, stored in `data/.install_id`, derived from nothing. The payload carries counts and configuration shape only — never meeting content, names, hostnames, URLs, keys, or model names. Provider fields carry the *family* (`anthropic`) and never the key, endpoint, or model. The ingest stores nothing derived from the connection: no IP, no User-Agent, no geolocation.

**Consent is gated on the notice actually being seen.** A new install consents through a ticked-by-default checkbox in the first-run wizard and starts sending immediately. An install upgraded into the feature sends **nothing at all** until an admin banner reports itself rendered; from that moment, an explicit "keep it on" starts sending immediately, "turn it off" is permanent, and silence for 7 days is treated as consent. An install nobody signs into never pings.

**Opting out is possible at three independent layers:** the Settings toggle, the `NOJOIN_TELEMETRY_ENABLED` environment variable (which outranks the UI and locks the toggle), and DNS or firewall blocking of `telemetry.nojoin.co.uk`.

**The collector is public.** The Cloudflare Worker and its D1 schema live in [telemetry/](../../telemetry/) in this repository, so the disclosure in [TELEMETRY.md](../TELEMETRY.md) is independently verifiable rather than a claim users must trust.

**State ownership is split so an opt-out cannot be clobbered.** `config.json` is written only by the API; the last-sent marker lives in Redis and is written only by the worker; the install id file is write-once. The worker reloads config before every consent check, so an opt-out takes effect on the next cycle rather than on the next container restart.

## Consequences

Product decisions gain an evidence base: deployment count, version spread and therefore update cadence, real usage volume, provider and engine popularity, and feature adoption.

The obligations this creates on contributors are real and ongoing:

- Adding a field to the ping **fails a test** (`test_payload_contains_exactly_the_documented_fields`) until [TELEMETRY.md](../TELEMETRY.md) is updated in the same change. This is deliberate: the disclosure cannot silently drift.
- The payload is assembled in exactly one function, `build_payload`. Assembling telemetry anywhere else defeats the lock above.
- The worker must never write `config.json`. It holds a `ConfigManager` populated at process start, so saving its map could revert an opt-out made moments earlier.

Accepted trade-offs:

- **Headless deployments are undercounted.** No notice can be shown, so nothing is sent, however long the install runs. Counting them would mean sending data before any human had been informed, which is the wrong trade for this project.
- **The counts can be forged.** The endpoint is public and unauthenticated; rate limiting, validation, and per-install-per-day deduplication stop casual noise, but a determined actor with many addresses could inflate the numbers. A shared secret was rejected as false comfort, since Nojoin is open source and any token would be extractable from an image in seconds.
- **Enabling on upgrade will attract criticism** from some users regardless of the consent gate. The mitigations are the notice, the grace period, the env kill switch, and this document.
- **Cloudflare becomes a processor** for the ingest. No personal data reaches it, but the dependency is real and disclosed.

## Alternatives Considered

**Opt-in.** The most defensible position, and rejected because opt-in telemetry on self-hosted software typically reaches single-digit-percentage adoption, which is worse than no data — it produces a confidently wrong picture skewed towards enthusiasts.

**Enabling silently on upgrade with no notice.** Maximises coverage and is what most projects do. Rejected: on a privacy-first tool, a data flow the operator was never told about is precisely the thing that destroys trust, and no amount of anonymity repairs it after the fact.

**A third-party analytics product (PostHog, Plausible).** Fastest to stand up and gives dashboards free. Rejected because introducing a third-party processor into a privacy-first project is awkward to disclose and impossible to verify — users would have to trust both us and them.

**Cloudflare Analytics Engine instead of D1.** Purpose-built for high-cardinality time series. Rejected because it samples, and the headline number this exists to answer — how many distinct deployments — needs to be exact.

**A minimal ping (install id and version only).** Considered first and widened during design. It answers "how many" but not "of what shape", which leaves every roadmap question unanswered and would have meant a second, wider disclosure later.
