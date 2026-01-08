# Git Commit Description - Use Conventional Commit Guidelines

fix(frontend): handle relative API_BASE_URL for WebSocket connections

- Modified `SystemTab.tsx` to safely construct WebSocket URLs.
- Added check to prepend `window.location.origin` if `API_BASE_URL` is a relative path (e.g., `/api/v1`).
- Prevents `TypeError: Failed to construct 'URL'` in production environments where relative paths are used.
