# Nojoin To-Do List

After the colons I will provide a list of tasks/instructions that need to be completed. Now read completely (not just first 100 lines) the following files in the /docs directory to get an understanding of the project: AGENTS.md, DEPLOYMENT.md, PRD.md, and USAGE.md. Present a plan for approval before making any changes:

## Meeting Recording Improvements
- If a meeting is recorded and no speech is detected, the whisper model can sometimes hallucinate words, let's make the processing pipeline more robust so as to simply return an empty transcript in such cases. We should also warn the user that in instances of no speech detected, the transcript will be empty but the whisper model may still produce hallucinated text.

## Backup/Restore System
- Review the UI/UX and implementation of the backup/restore system. Ensure it is intuitive and robust.
- Ensure the UI/UX is consisent with the rest of the frontend, including the 'Browse...' button styling and placement.

### Deployment
- [ ] **Publish Pre-built Images**: Build and push Docker images to GHCR (GitHub Container Registry https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).
- [ ] **Update Compose Files**: Update `docker-compose.yml` to pull from GHCR instead of building locally.

### Companion App UX
- [ ] **Meeting Controls**: Add "Start Meeting" and "Stop Meeting" buttons directly in the Companion App system tray context menu as a fallback if the frontend becomes unreachable for whatever reason.
- [ ] **Simplified Connection**: Allow entering just the server address (e.g., `https://nojoin.mylocaldomain.lan`) without needing to specify ports/paths manually if using standard defaults. Rather than just calling it 'Settings', rename to 'Connection Settings' for clarity.

### macOS Specifics
- [ ] **Permission Handling**: Implement explicit permission requests and status checks for Microphone and System Audio.
- [ ] **Permission UI**: Add a "Permissions" tab in Settings showing the status of required permissions (Green checkmarks).
- [ ] **Stuck Meeting State**: Investigate and fix the issue where the meeting stays "In Progress" if the server fails to end it properly. Ensure the Companion App can force-stop a meeting. This was likely a macOS specific issue since the kinks on that platform are still being worked out.
