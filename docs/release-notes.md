## Unreleased

### Recording And Processing Workspace

- Added a dedicated in-progress recording / processing workspace with a live dual-channel waveform, centred progress badge, recording length card, and inline manual notes panel.
- Added a non-destructive Companion endpoint at `GET /levels/live` so the Web Client can poll live audio levels without consuming the existing peak meters.
- Improved notes-panel responsiveness by keeping input local in the browser and autosaving asynchronously.

### Processing ETA

- Added persisted `processing_started_at` and `processing_completed_at` fields on recordings.
- Added ETA estimation based on prior completed processing runs with explicit timing data.
- Added a learning-state message for installations that do not yet have enough timing history.

### User-Authored Notes

- Added `user_notes` persistence and API endpoints for meeting-specific manual notes.
- Preserved user-authored notes across Retry Processing while clearing generated artefacts.
- Fed user-authored notes into speaker inference and meeting-note generation, and appended a deterministic `User Notes` section to final notes.
