# Nojoin Architecture Overview

This document provides a human-readable overview of how Nojoin fits together.

For product scope and longer-term feature intent, see [PRD.md](PRD.md).

## System at a Glance

Nojoin has three major parts:

1. A Dockerised backend that stores data and runs processing workloads.
2. A Next.js web client for capture control, review, and administration.
3. A Windows Companion app that captures audio locally and owns native pairing approval, local browser connectivity, and tray-side operational fallback.

## Core Components

### Backend

The backend is responsible for:

- API endpoints.
- Authentication and authorisation.
- Recording lifecycle management.
- Background task dispatch.
- Calendar sync orchestration.
- Release metadata and system operations.

The processing-heavy work runs in Celery workers rather than inside API endpoints.

### Web Client

The web client is responsible for:

- Dashboard workflows.
- Recordings workspace and transcript review.
- Speaker management.
- Notes, meeting chat, and document upload.
- User, admin, and system settings.
- Browser-side Companion status, signed pairing request initiation, and support routing.

The web Companion page is a secondary support surface. It confirms state, starts signed pairing requests, and routes native-only disconnect or troubleshooting actions back to the Companion app.

### Companion App

The Companion app is responsible for:

- Presenting the native launcher, Settings window, and tray fallback surfaces.
- Capturing loopback system audio and microphone audio on Windows.
- Owning OS-native approval for browser-initiated pairing requests, update, log, and disconnect actions.
- Exposing local status and live metering to the web client.
- Uploading recording segments to the backend.

## Recording Flow

1. The browser authenticates through a Secure HttpOnly session cookie.
2. The browser starts a signed pairing request from `Settings -> Companion`, launches the local Companion through `nojoin://pair`, and the Companion shows an OS-native approval prompt on that same machine. During completion, the Companion validates the backend-signed request fields, captures and pins the backend TLS certificate it first sees, stores a revocable companion credential plus local control secret in a Windows-protected secret store, and persists only backend metadata plus backend identity metadata in `config.json`.
3. When the Companion needs backend access, it exchanges the stored companion credential for a short-lived companion access token.
4. When a recording starts, `/recordings/init` returns a short-lived per-recording upload token.
5. The Companion uploads audio segments using that recording-scoped token.
6. Finalisation queues the recording for backend processing.
7. The web client shows a live capture or processing status workspace while the job runs.

Disconnecting the current backend from Companion Settings clears the stored backend trust state and local secret bundle, then attempts a best-effort revoke against the previously paired backend before returning the Companion to a clean first-pair state.

If browser-side local control degrades, the web client reports coarse state and directs the user to relaunch Companion or inspect Companion Settings for status.

## Processing Pipeline

The normal backend processing path is:

1. Validation.
2. VAD and audio preprocessing.
3. Proxy creation for web playback.
4. Whisper transcription.
5. Pyannote diarisation.
6. Phantom speaker filtering.
7. Merge, voiceprint extraction, and deterministic speaker resolution.
8. Automatic meeting intelligence when an AI provider and model are configured.
9. Persistence of unresolved speaker suggestions, meeting title, and Markdown meeting notes.

Manual user notes can be captured during recording or processing and are fed into both the automatic meeting-intelligence stage and the manual note-generation flow.

If AI configuration is missing, the recording still completes with transcript, diarisation, and deterministic speaker resolution intact. Automatic AI enhancement is skipped rather than failing the meeting. Manual `Generate Notes` and `Retry Speaker Inference` remain available once AI is configured.

## Calendar Flow

1. An admin configures Google and/or Microsoft OAuth credentials for the installation.
2. End users connect their own accounts from the Personal settings area.
3. Nojoin syncs selected calendars into stored dashboard-facing event data.
4. The dashboard renders month markers, agenda items, next-event summaries, and colour-coded sources.

## Authentication Model

Nojoin uses different auth shapes for different clients:

- **Browser traffic**: Secure HttpOnly session cookies. State-changing browser requests authenticated by that session must originate from the trusted Nojoin web origin, using standard `Origin` or `Referer` validation rather than relying only on `SameSite` and CORS.
- **Non-browser API clients**: Explicit bearer tokens.
- **Companion pairing**: A short-lived signed pairing request created by the authenticated browser session, explicitly approved in the local Companion app, and completed against one backend target.
- **Companion backend access**: Short-lived companion access tokens exchanged on demand by the Companion.
- **Companion upload operations**: Short-lived per-recording tokens.

Forced password rotation is enforced server-side. Flagged users can only reach their self-profile, password update flow, and logout until the rotation is complete.

## Storage and Persistence

- **PostgreSQL** stores metadata, transcripts, speakers, tasks, calendar state, and user settings.
- **Redis** supports Celery and related queue or cache operations.
- **Recordings storage** holds source audio, derived proxy assets, and related files on disk.
- **Config files** store system-wide configuration, while sensitive material is encrypted or otherwise handled separately where appropriate.

## Release Model

Nojoin follows a unified release model:

- Git tags in the form `vX.Y.Z` drive published releases.
- Docker images are published to GHCR.
- Windows Companion binaries are published alongside the server release.
- The application surfaces release metadata primarily from GitHub Releases.

## Related Docs

- [COMPANION.md](COMPANION.md)
- [GETTING_STARTED.md](GETTING_STARTED.md)
- [DEPLOYMENT.md](DEPLOYMENT.md)
- [USAGE.md](USAGE.md)
- [CALENDAR.md](CALENDAR.md)
- [PRD.md](PRD.md)
- [AGENTS.md](AGENTS.md)
