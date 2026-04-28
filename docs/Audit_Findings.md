# Audit Findings

This document captures the security audit items that remain open, plus findings that are only partially closed and still need follow-up work.

## Partially Closed To-Dos

- [ ] Revisit throttling for the newer Companion auth endpoints.
  - Current state: the old unlimited long-lived companion token flow has been replaced, but the current Companion pairing, local-control token issuance, and credential exchange endpoints do not appear to be rate-limited.
  - Files to review: [backend/api/v1/endpoints/login.py](../backend/api/v1/endpoints/login.py)
  - Target outcome: protect the new Companion auth endpoints with targeted rate limits appropriate to their risk and expected usage.

- [ ] Review Companion logging for residual token or sensitive transport leakage and tighten log file handling.
  - Current state: the current logging paths do not obviously print bearer headers directly, but raw reqwest-style errors are still logged and the log file setup does not appear to add extra filesystem permission hardening.
  - Files to review: [companion/src-tauri/src/uploader.rs](../companion/src-tauri/src/uploader.rs), [companion/src-tauri/src/main.rs](../companion/src-tauri/src/main.rs)
  - Target outcome: confirm sensitive headers cannot leak through error formatting and ensure the log file permissions are acceptable for the deployment model.
