# HTTPS Security Enforcement and Companion App Update

## Summary
Enforce HTTPS-only access to Nojoin by redirecting HTTP (port 14141) to HTTPS (port 14443), and update the companion app's system tray menu for consistent branding.

## Changes Made

### Security (HTTP to HTTPS Redirect)
- Modified nginx.conf to redirect HTTP requests (port 80) to HTTPS on port 14443
- Updated docker-compose.yml to:
  - Route port 14141 through nginx for HTTP redirect instead of exposing frontend directly
  - Removed direct frontend port exposure to prevent HTTP access bypass
  - Added nginx dependency on frontend and api services for proper startup order

### Companion App
- Changed tray menu item from "Open Web App" to "Open Nojoin" for consistent branding
- Updated config.example.json to use HTTPS URL (https://localhost:14443/api/v1) instead of HTTP

### Architecture
- All web traffic now flows through nginx reverse proxy
- Port 14141 (HTTP) automatically redirects to port 14443 (HTTPS)
- Frontend container no longer exposes ports directly, only accessible via nginx
