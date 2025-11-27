# Implement Multi-Tenant User System & First-Run Wizard

## Features
- **Multi-Tenancy**: 
  - Updated `Recording`, `GlobalSpeaker`, and `Tag` models with `user_id` ownership.
  - Configured cascading deletes for user data cleanup.
  - Refactored API endpoints to enforce strict user data isolation.
- **First-Run Experience**:
  - Added `POST /api/v1/system/setup` for initial admin creation.
  - Added `GET /api/v1/system/status` for initialization checks.
  - Created Frontend Setup Wizard (`/setup`) to guide new installations.
  - Disabled automatic default user creation in backend startup.
- **User Management**:
  - Added Admin Panel in Settings for managing users (Create/Edit/Delete).
  - Added Account Panel for self-service profile and password updates.
  - Implemented `AuthGuard` to protect routes and handle redirects.
  - Added `Log Out` button to main navigation for secure session termination.
- **Database**:
  - Added Alembic migration `da065320c05b` for multi-tenant schema changes.
  - Configured backend to auto-run migrations on startup.

## Fixes
- Suppressed "Server Unreachable" error during initial frontend startup (10s grace period).
- Fixed "Rules of Hooks" violation in `SettingsPage.tsx`.
- Fixed `Internal Server Error` on fresh install by ensuring migrations run automatically.
- Fixed `SettingsPage` crash by handling null settings response in frontend.
- Updated backend `GET /settings` endpoints to return empty object instead of null.
- Resolved 404 errors on API endpoints (`/tags`, `/speakers`, `/recordings`, etc.) by implementing dual route handlers for strict slash compatibility.
- Fixed `ImportError` in `speaker.py` caused by circular dependencies.
- Added missing Pydantic models (`TagCreate`, `TagUpdate`, `TagRead`) to `backend/models/tag.py`.
- Fixed authentication flow to return `401 Unauthorized` (instead of `404`) for stale tokens, triggering correct frontend logout.
- Removed deprecated `create_first_user.py` script and default 'admin' username from setup form.

## Technical Details
- **Backend**: FastAPI, SQLModel, Alembic.
- **Frontend**: Next.js, React, Tailwind CSS.
- **Auth**: JWT-based authentication with forced password change support.
