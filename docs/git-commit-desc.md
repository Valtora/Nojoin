# Git Commit Description

## fix: enforce strict API Key access controls and secure LLM inheritance for standard users

- **api/settings:** Refactored `_merge_settings` to explicitly extract and propagate the Owner's LLM provider and model configurations into the non-admin user's payload. `SENSITIVE_KEYS` are now dynamically securely masked natively on backend transmission so standard users can utilize global AI models without exposing the plaintext API keys via the network or UI.
- **api/transcripts:** Secured `chat_with_meeting` and `generate_notes` by converting the monolithic key retrieval sequence into a secure backend `async_get_system_api_keys` db call, ensuring standard users cannot bypass or manually spoof the owner system's API credentials while permitting them to generate chat notes using the system-enrolled token.
- **worker/tasks:** Replicated secure global LLM pipeline configs mapped to `process_recording_task`, `generate_notes_task`, and `infer_speakers_task`. Ensures that decoupled Celery background processing securely fetches and enforces the master system API key architecture without retrieving missing keys from the ordinary user's plaintext settings map.
- **api/transcripts:** Mapped 503 (Overloaded) and 429 (Rate Limited) errors from upstream LLM models to emit user-friendly SSE messages down the WebSocket instead of raw `Internal Server Error` fail-safe traces.
- **api/transcripts:** Addressed an `UnboundLocalError` by eliminating inline aliasing of SQLAlchemy's `select` statement.
