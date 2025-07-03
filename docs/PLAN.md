# Nojoin Cloud & Remote Processing Implementation Plan

This document outlines the phased development plan to evolve Nojoin from a standalone desktop application into a cloud-connected service.

---
## Guiding Principles & Architecture

-   **Operating Modes:** Nojoin will support two distinct modes:
    1.  **Local Mode:** The application works entirely offline, using the user's own hardware and local API keys, identical to its current functionality.
    2.  **Cloud Mode:** Upon logging in, users gain access to cloud features. This mode is a prerequisite for remote processing and data synchronization.

-   **Remote Processing:** The ability to offload processing to a remote GPU will be offered exclusively as a managed cloud service. The application will not support connecting to a user's private, self-hosted server.

-   **Monorepo:** The backend API service will be developed within the existing `nojoin` repository under a `nojoin/backend` directory to streamline development and dependency management.

---

## Phase 1: Core Cloud Foundation & Data Synchronization

**Objective:** Establish a robust "Cloud Mode" for users. This phase focuses on full user authentication, cloud storage, and real-time synchronization of user settings and data between the local client and the cloud. By the end of this phase, a user should be able to log in, have their settings sync, and manage database backups in the cloud.

### Task 1.1: Backend Scaffolding (Monorepo)
-   **Status:** Pending
-   **Action:** Create the foundational structure for the FastAPI backend within the current project.
-   **Details:**
    -   Create a `nojoin/backend` directory.
    -   Add backend-specific dependencies (FastAPI, Uvicorn, Celery, etc.) to the main `requirements.txt`.
    -   Initialize a basic FastAPI app in `nojoin/backend/main.py` with a health-check endpoint (e.g., `/health`).
    -   Establish a clear directory structure within `nojoin/backend` for routes, services, and models to ensure separation of concerns.

### Task 1.2: User Authentication Service
-   **Status:** Pending
-   **Action:** Implement a complete authentication flow using Supabase.
-   **Pattern:** Singleton `CloudManager` on the client-side to manage state.
-   **Details:**
    -   **Client (UI):** Create non-blocking UI dialogs for Login, Registration, and Password Reset in `nojoin/ui/`.
    -   **Client (Service):** Develop a `nojoin/cloud/auth_service.py` module. This service will encapsulate all interactions with Supabase Auth (`supabase-py`), manage the JWT lifecycle (storage, refresh, deletion), and hold the user session state.
    -   **Backend (Security):** Implement a reusable FastAPI dependency that validates the JWT on protected endpoints, extracting the user ID for use in downstream logic.

### Task 1.3: Cloud Configuration Synchronization
-   **Status:** Pending
-   **Action:** Enable user settings to be stored in the cloud and synced with the local client.
-   **Pattern:** Repository Pattern to abstract data sources.
-   **Details:**
    -   **Backend:**
        -   In Supabase, create a `profiles` table with a one-to-one mapping to `auth.users`. This table will store user configuration as a JSONB column.
        -   Create authenticated API endpoints: `GET /api/v1/config` and `PUT /api/v1/config`.
    -   **Client:**
        -   Refactor `nojoin/utils/config_manager.py` to operate against an abstract `ConfigRepository` interface.
        -   Implement `LocalConfigRepository` (for file-based storage) and `CloudConfigRepository` (for API-based storage).
        -   On login, check for cloud config. If different from local, prompt the user to choose which version to keep.
        -   On settings change in Cloud Mode, update both the local file and the cloud via the API to ensure consistency.

### Task 1.4: Database Backup & Restore Service
-   **Status:** Pending
-   **Action:** Allow users to save and load their application database to/from Supabase Storage.
-   **Details:**
    -   **Backend:**
        -   Implement strict Row Level Security (RLS) policies on a new Supabase Storage bucket (`user-backups`) to ensure users can only access a folder matching their user ID.
        -   Create an endpoint `POST /api/v1/backups/generate-upload-url` that returns a secure, time-limited presigned URL for uploading a file to the user's private folder.
        -   Create `GET /api/v1/backups` and `POST /api/v1/backups/generate-download-url` to list backups and get a presigned URL for downloading.
    -   **Client:**
        -   Update `nojoin/utils/backup_restore.py` to use these new service endpoints. The client will no longer need powerful Supabase credentials. It will request a URL from the backend and then perform a simple HTTP `PUT` or `GET` request.

---

## Phase 2: Remote GPU Processing Pipeline

**Objective:** Implement the end-to-end pipeline for offloading transcription and diarization tasks to the cloud backend.

### Task 2.1: Abstract Processing Logic
-   **Status:** Pending
-   **Action:** Refactor the core processing pipeline to support interchangeable processing strategies.
-   **Pattern:** Strategy Pattern.
-   **Details:**
    -   Define an abstract base class, `ProcessingStrategy`, in `nojoin/processing/strategies/base.py`.
    -   Create `LocalProcessingStrategy` in `.../local_strategy.py` that encapsulates the existing, file-based processing logic.
    -   Create `RemoteProcessingStrategy` in `.../remote_strategy.py` which will orchestrate the API calls for remote processing.
    -   Modify `nojoin/processing/pipeline.py` to select and use a strategy based on the user's current mode (Local vs. Cloud).

### Task 2.2: Asynchronous Job Queue
-   **Status:** Pending
-   **Action:** Integrate Celery and a message broker (Redis) into the backend to manage processing jobs asynchronously.
-   **Details:**
    -   Configure Celery to work with the FastAPI application.
    -   Define a Celery `@task` that takes an audio file path and runs the full transcription/diarization pipeline. This task will run in a separate Celery worker process.

### Task 2.3: Job Management API
-   **Status:** Pending
-   **Action:** Create the API endpoints for submitting and monitoring jobs.
-   **Details:**
    -   `POST /api/v1/jobs`: Accepts a file upload. Validates the user (via JWT), places the job in the Celery queue, and immediately returns a `{ "job_id": "..." }`.
    -   `GET /api/v1/jobs/{job_id}`: Pollable endpoint. Returns the job status (`QUEUED`, `PROCESSING`, `COMPLETED`, `FAILED`) and, upon completion, the final results.

---

## Phase 3: Monetization & Commercialization

**Objective:** Build the infrastructure to support paid subscription tiers for accessing premium features like cloud processing.

### Task 3.1: Subscription & Usage Schema
-   **Status:** Pending
-   **Action:** Extend the Supabase database to track subscriptions and resource consumption.
-   **Details:**
    -   Create a `subscriptions` table to store tier info (`free`, `pro`), status (`active`, `canceled`), and the current billing period, linked to a user.
    -   Create a `gpu_usage_logs` table to log every completed remote job, its duration, and the associated user for analytics and potential future usage-based billing.

### Task 3.2: Payment Integration
-   **Status:** Pending
-   **Action:** Integrate Stripe for handling subscriptions.
-   **Details:**
    -   Use Stripe Checkout for a secure, hosted payment page.
    -   Implement a `POST /api/v1/billing/create-checkout-session` endpoint in the backend that redirects users to Stripe.
    -   Create a webhook handler endpoint `POST /api/v1/billing/webhook` to listen for events from Stripe (e.g., `checkout.session.completed`) and update the `subscriptions` table accordingly.

### Task 3.3: Tier-Gated Access Control
-   **Status:** Pending
-   **Action:** Enforce subscription limits in the backend.
-   **Details:**
    -   Before adding a job to the queue in the `POST /api/v1/jobs` endpoint, the logic will first query the `subscriptions` table to verify the user has an active, valid subscription for cloud processing. If not, the API will return a `403 Forbidden` error with an explanatory message.

---

## Phase 4: Deployment & Operations

**Objective:** Prepare the backend service for a scalable, production-grade cloud deployment.

### Task 4.1: Cloud Deployment Architecture
-   **Status:** Pending
-   **Action:** Define and document the target architecture for deploying the service on a major cloud provider (e.g., AWS).
-   **Details:**
    -   **Containerization:** Use Docker to containerize the FastAPI application and the Celery workers.
    -   **API:** Deploy the FastAPI container to a scalable service like AWS ECS or Fargate, behind an Application Load Balancer.
    -   **Compute Workers:** Deploy the Celery worker container to a separate auto-scaling group of EC2 GPU instances (e.g., G4dn).
    -   **Job Queue:** Use a managed service like Amazon SQS or Redis ElastiCache.
    -   **CI/CD:** Document a plan for a CI/CD pipeline (e.g., using GitHub Actions) to automate testing and deployment of new container images.

---

## Phase 5: Web Application Frontend

**Objective:** Develop a full-featured, web-based client that mirrors the functionality of the desktop application, enabling cross-platform access from any modern browser. This phase will commence after the core cloud-connected desktop application is stable.

### Task 5.1: Web App Proof of Concept (POC)
-   **Status:** Pending
-   **Action:** Build a minimal web application that can authenticate with the existing backend.
-   **Technology:** SvelteKit and TypeScript.
-   **Details:**
    -   Implement the Supabase login flow in TypeScript using `supabase-js`.
    -   Create a simple page that calls a protected backend endpoint (e.g., `GET /api/v1/config`) and displays the result.

### Task 5.2: UI Component Library
-   **Status:** Pending
-   **Action:** Create a reusable library of UI components (buttons, dialogs, etc.) that match the Nojoin brand.
-   **Details:**
    -   Use a component framework like Skeleton UI for Svelte or build them from scratch with a CSS framework like Tailwind CSS.

### Task 5.3: Core Feature Implementation
-   **Status:** Pending
-   **Action:** Build out the core web application's features, aiming for parity with the desktop client for file-based processing.
-   **Details:**
    -   Implement settings, database backup/restore, audio file upload, job polling, and results display.
    -   Integrate the Stripe billing flow to manage subscriptions from the web.

### Task 5.4: Live Recording via Companion App
-   **Status:** Pending
-   **Action:** Design, build, and integrate a downloadable native companion app for live audio recording.
-   **Architecture:**
    -   **Companion App:** A lightweight, system-tray Python application (packaged with PyInstaller) that handles the capture of both **microphone input and system audio output**.
    -   **Communication:** The Svelte web app will communicate with the companion app over a local WebSocket connection. The web app acts as a "remote control".
    -   **Workflow:** The companion app captures, pauses, and stops audio from both sources based on commands from the web app. It will mix these sources into a single audio track. Upon completion, it uploads the final mixed audio file directly to the backend API.
-   **Sub-Tasks:**
    1.  **Companion App Core:** Develop the Python app with multi-source audio recording (`soundcard` library) to capture and mix microphone/system audio, and manage a local WebSocket server.
    2.  **Web App Integration:** Implement the client-side WebSocket logic in the Svelte app to detect the companion, send commands, and display status updates.
    3.  **Authentication:** Secure the WebSocket handshake and pass the user's JWT from the web app to the companion app for authenticated API uploads.
    4.  **Packaging:** Create installers for Windows and macOS. 