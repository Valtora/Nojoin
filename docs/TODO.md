# Nojoin To-Do List

Let's continue the development of Nojoin. Read completely (not just first 100 lines) the AGENTS.md and the PRD.md files in the docs directory to get an understanding of the project. After the colons I will provide a list of tasks/instructions that need to be completed:

## Companion App Un/Installer and Wizard
- Currently if an existing instance of Nojoin's companion app is found the installer prompts the user to kill it manually and retry. This is fine however we should also add a 'Close Nojoin Companion App' button to the installer to let the user kill it themselves via that UI interacttion.
- Verify the UNinstall logic is as equally robust as the installer logic.

## Security
- Explore feasibility of removing HTTP support entirely and only allowing HTTPS connections to the Nojoin backend server. This may involve generating self-signed certificates for local development and self-hosting via traefik, caddy, etc.
- Research the implications of this change on the companion app's ability to connect to the backend server and any other parts of the system that may be affected. Provide a summary of findings and recommendations on whether this change should be implemented and how to go about it if so.

## Consider Upgrading to Cuda 13
- See this link: https://www.google.com/search?q=cuda+12.6+vs+12.8+vs+13.0&num=10&client=firefox-b-d&sca_esv=30d2ca42fc4644ef&sxsrf=AE3TifPQ_jdRbNECXpjGm2Ar-AilIFMQyw%3A1764454298783&ei=mm8raYvJL-2jhbIPzv6w-Ag&oq=cuda+12.6+vs&gs_lp=Egxnd3Mtd2l6LXNlcnAiDGN1ZGEgMTIuNiB2cyoCCAIyBRAAGIAEMgUQABiABDIFEAAYgAQyBRAAGIAEMgUQABiABDIIEAAYFhgKGB4yBhAAGBYYHjIGEAAYFhgeMgYQABgWGB4yCBAAGBYYChgeSKwOUIcBWOMCcAF4AJABAJgBRKABwAGqAQEzuAEByAEA-AEBmAIEoALNAcICDRAAGIAEGLADGEMYigXCAgoQABiABBhDGIoFwgIKEAAYgAQYFBiHApgDAIgGAZAGCpIHATSgB_QWsgcBM7gHyQHCBwUwLjIuMsgHCg&sclient=gws-wiz-serp
- See this link too: https://pytorch.org/get-started/locally/

## Meeting Transcription Feature - Translation
- Use OpenAI's Whisper API to implement a translation feature for meeting transcriptions. This feature should allow users to select a target language for translation after the transcription is complete. The translated text should be displayed alongside the original transcript in the transcript window, with clear labeling to differentiate between the two. Explore how to best integrate this feature into the existing transcription workflow.
- See here: https://github.com/openai/whisper/blob/main/model-card.md

## Backup/Restore Feature
- Implement a backup/restore feature.
- The backup feature should create a zip file containing all relevant data including the database, recordings, transcripts, notes, tags, and settings. The user should be able to download this zip file to their local machine via the frontend.
- The restore feature should allow the user to upload a previously created backup zip file via the frontend. The backend should then extract the zip file and replace the existing data with the data from the backup in an additive way unless the user checks a 'Clear Existing Data' option. Proper validation and error handling should be implemented to ensure data integrity during the restore process.

## LICENCE File
- Help me to create an appropriate LICENCE file for the Nojoin project. I may decide to monetise Nojoin in the future by having a 'community' version that is open source and self-hosted as well as a 'pro' version that is closed source and has additional features. Therefore I want to choose a licence that allows me to do this. Research and suggest a suitable open source licence for the Nojoin project that meets these criteria and also considers the dependencies used in the project.

## Tauri Migration & CI/CD
- [ ] **Update GitHub CI/CD Workflows**: Create or update `.github/workflows/companion-tauri.yml` to build the Tauri application for Windows, macOS, and Linux. Ensure artifacts are correctly uploaded to GitHub Releases.
- [ ] **Implement Installer Configuration**: Verify `tauri.conf.json` bundle settings. Ensure the installer correctly handles:
    - Installation directory.
    - Shortcuts (Desktop/Start Menu).
    - "Run on Startup" functionality (may need a specific Tauri plugin or registry key logic).
    - Uninstall logic.
- [ ] **Re-enable Auto-Updater**:
    - Generate Tauri signing keys (`tauri signer generate`).
    - Add public key to `tauri.conf.json`.
    - Add private key to GitHub Secrets.
    - Re-enable `updater` in `tauri.conf.json` and uncomment updater logic in `main.rs`.