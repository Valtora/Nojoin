# ADR-0001: Gated, Signed, Reproducible Container Release Model

- **Status:** Accepted
- **Date:** 2026-06-22
- **Deciders:** @Valtora

## Context

Nojoin ships as three container images (api, worker, frontend) that operators pull by rolling tag. Before the Phase 5 supply-chain hardening, a pushed tag built and published images directly, including the mutable `latest` tag, with no vulnerability gate, no provenance, and no signature. That left three gaps in the deployment trust boundary: operators could not verify an image's origin, a known-vulnerable image could reach `latest`, and a mutable action or base-image tag could be repointed at unreviewed code after review.

This decision was made during Phase 5 and is recorded here retrospectively to seed the ADR process and demonstrate the format.

## Decision

We will publish releases through a single gated, signed, reproducible pipeline driven by a strict `vX.Y.Z` tag, implemented in [.github/workflows/release.yml](../../.github/workflows/release.yml):

- **Pin everything mutable.** Every third-party GitHub Action is pinned to a commit SHA and every container base image to a `@sha256:` digest, with the human-readable version kept as a comment. [.github/dependabot.yml](../../.github/dependabot.yml) keeps the pins current.
- **Build immutable tags first.** The build publishes only the immutable `version` and commit-`sha` tags, each carrying `provenance: mode=max` and an SBOM attestation.
- **Gate on a vulnerability scan.** Trivy scans each image and fails the release on fixable CRITICAL/HIGH findings; accepted exceptions live in `.trivyignore`. The severity policy is documented in [docs/SECURITY.md](../SECURITY.md).
- **Sign by digest, then smoke-test.** Each image is cosign keyless-signed by digest, then a health-and-non-root smoke job boots the api and frontend and asserts non-root runtime.
- **Promote rolling tags only after every gate passes.** A separate job advances `major.minor` and `latest` only after scanning, the smoke test, and signing succeed, so a failing build can never advance the tag operators pull by default.

## Consequences

- A pushed tag no longer guarantees published rolling tags: a fixable CRITICAL/HIGH finding blocks promotion until the underlying dependency or base image is updated.
- Operators can verify provenance and signatures before deploying; the commands live in [docs/DEPLOYMENT.md](../DEPLOYMENT.md).
- Contributors editing CI, the release workflow, or the Dockerfiles must preserve the pinning, gate ordering, and signing identity. This obligation is recorded in the merge-requirements section of [CONTRIBUTING.md](../../CONTRIBUTING.md).
- A briefly-exposed immutable `vX.Y.Z` tag can exist for a failed run, but it is visibly attached to a failed pipeline and never reachable through `latest`.

## Alternatives Considered

- **Keep direct publication and add scanning as a non-blocking report.** Rejected: a non-blocking scan does not protect operators who pull `latest`, which was the core gap.
- **Sign with a long-lived cosign key stored as a secret.** Rejected in favour of keyless OIDC signing, which binds the signature to the release workflow identity and removes a long-lived private key from the threat model.
