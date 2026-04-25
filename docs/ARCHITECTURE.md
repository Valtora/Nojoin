# Nojoin Architecture Overview

This document provides a human-readable overview of how Nojoin fits together.

For product scope and longer-term feature intent, see [PRD.md](PRD.md).

## System at a Glance

Nojoin has three major parts:

1. A Dockerised backend that stores data and runs processing workloads.
2. A Next.js web client for capture control, review, and administration.
3. A Windows Companion app that captures system and microphone audio locally.

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

### Companion App

The Companion app is responsible for:

- Capturing loopback system audio and microphone audio on Windows.
- Exposing local status and live metering to the web client.
- Uploading recording segments to the backend.

## Recording Flow

1. The browser authenticates through a Secure HttpOnly session cookie.
2. The user manually pairs the Companion app to one Nojoin backend at a time using a short-lived pairing code. During that flow, the Companion captures and pins the backend TLS certificate it first sees, then stores a bootstrap token for recording initialisation.
3. When a recording starts, `/recordings/init` returns a short-lived per-recording upload token.
4. The Companion uploads audio segments using that recording-scoped token.
5. Finalisation queues the recording for backend processing.
6. The web client shows a live capture or processing status workspace while the job runs.

Disconnecting the current backend from Companion Settings clears the stored backend trust state and returns the Companion to a clean first-pair state.

## Processing Pipeline

The normal backend processing path is:

1. Validation.
2. VAD and audio preprocessing.
3. Proxy creation for web playback.
4. Whisper transcription.
5. Pyannote diarisation.
6. Phantom speaker filtering.
7. Merge and speaker resolution.
8. Voiceprint extraction.
9. Title inference.
10. Meeting note generation.

Manual user notes can be captured during recording or processing and are fed into later inference and note-generation stages.

## Calendar Flow

1. An admin configures Google and/or Microsoft OAuth credentials for the installation.
2. End users connect their own accounts from the Account settings page.
3. Nojoin syncs selected calendars into stored dashboard-facing event data.
4. The dashboard renders month markers, agenda items, next-event summaries, and colour-coded sources.

## Authentication Model

Nojoin uses different auth shapes for different clients:

- **Browser traffic**: Secure HttpOnly session cookies.
- **Non-browser API clients**: Explicit bearer tokens.
- **Companion pairing**: A short-lived pairing code submitted manually, establishing a one-backend association and returning a bootstrap token.
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

- [GETTING_STARTED.md](GETTING_STARTED.md)
- [DEPLOYMENT.md](DEPLOYMENT.md)
- [USAGE.md](USAGE.md)
- [CALENDAR.md](CALENDAR.md)
- [PRD.md](PRD.md)
- [AGENTS.md](AGENTS.md)
