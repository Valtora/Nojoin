# Git Commit Description - Use Conventional Commit Guidelines

feat(system): implement system restart and enhanced log streaming

- **System Restart**: Added `/system/restart` endpoint with backend delay to ensure clean response. Mounted Docker socket for container management.
- **Logs**:
  - Implemented non-blocking **Threaded Queue** architecture for Docker logs to prevent API freezes.
  - Added **Unified Log View** (`container="all"`) to stream all containers simultaneously.
  - Added **Log Level Filter** (DEBUG, INFO, WARN, ERROR) and Regex filtering in frontend.
  - Fixed WebSocket auth using dedicated `get_current_active_superuser_ws` dependency.
  - Added log download functionality.
- **Docs**: Updated docs to include new restart and log streaming features.
