# Architecture Decision Records

An Architecture Decision Record (ADR) captures a significant architectural decision, the context that forced it, and its consequences. ADRs are intentionally lightweight: a single Markdown file per decision, committed alongside the code that implements it.

## When an ADR Is Required

Open an ADR when a change alters one of Nojoin's core contracts:

- **Trust boundaries** — authentication, session or token handling, encryption, or what a given actor is permitted to do.
- **Persistence** — the database schema's shape or guarantees, backup/restore semantics, or migration strategy.
- **Capture** — the browser-capture contract: supported browsers, audio sources, segment upload, or live-processing dispatch.
- **Processing** — the transcription/diarisation pipeline contract, including stage boundaries, engine selection, or canonical-pipeline reconciliation.
- **Deployment** — the release model, image build/sign/scan pipeline, runtime topology, or operator-facing configuration contracts.

A routine bug fix, refactor, dependency bump, or documentation edit does **not** need an ADR. If you are unsure, the test is whether a future maintainer would be surprised by the decision and need to know *why* it was made.

## How to Add an ADR

1. Copy [0000-adr-template.md](0000-adr-template.md) to `NNNN-short-kebab-title.md`, where `NNNN` is the next free zero-padded number.
2. Fill in the sections. Keep it short; link to code, workflows, or other docs rather than restating them.
3. Set the status to `Accepted` when the decision is in effect. Superseded decisions stay in the repository for history and link forward to the ADR that replaced them.
4. Reference the ADR from the pull request that implements the decision.

## Index

- [0001-gated-signed-release-model.md](0001-gated-signed-release-model.md) — Gated, signed, reproducible container release pipeline (Accepted).
