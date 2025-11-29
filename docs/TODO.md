# Nojoin To-Do List

## Prompt For Supervised Agents
Let's continue the development of Nojoin. Read the PRD.md in the docs directory to get an understanding of the project. After the colons I will provide a list of tasks/instructions that need to be completed. Your goal is now to present a plan for me to approve to complete these tasks/instructions set out below:

### Meeting Transcription Feature - Translation
- Use OpenAI's Whisper API to implement a translation feature for meeting transcriptions. This feature should allow users to select a target language for translation after the transcription is complete. The translated text should be displayed alongside the original transcript in the transcript window, with clear labeling to differentiate between the two. Explore how to best integrate this feature into the existing transcription workflow.

### LICENCE File
- Help me to create an appropriate LICENCE file for the Nojoin project. I may decide to monetise Nojoin in the future by having a 'community' version that is open source and self-hosted as well as a 'pro' version that is closed source and has additional features. Therefore I want to choose a licence that allows me to do this. Research and suggest a suitable open source licence for the Nojoin project that meets these criteria and also considers the dependencies used in the project.

### UI/UX Improvements
- Make the notification toasts opaque rather than semi-transparent. Remove the 'fade in and fade out' feature that was being used in an attempt to draw the user's eyes. The toasts should be clearly visible and easy to read at all times.
- Increase the 'x' button size on the notification toasts to make it easier for users to click and dismiss notifications.
- If multiple notifications come in at once, stack them vertically rather than displaying them one at a time. This will help users see all relevant information at a glance.

### PRD Review
- Perform a comprehensive review of the PRD to ensure it is up to date with the current state of the Nojoin project. Identify any discrepancies or areas that require clarification or expansion. Summarize your findings and propose any necessary updates to the PRD to align it with the project's goals and features.

### Meeting Notes Feature
- Confirm the auto-save functionality for changes made to the meeting notes in the Notes panel. Ensure that any edits made by the user are automatically saved to the backend without requiring a manual save action. Test this feature thoroughly to ensure reliability and data integrity.

### API Polling Improvements
- Change the health check poll to only occur every 10 seconds or when an action is taken that requires the backend to be running (e.g., start recording, stop recording, fetch recordings, etc). This will reduce unnecessary network traffic and resource usage when the app is idle.
- Implement exponential backoff for the API polling mechanism. If a poll fails, the next poll should be delayed by an increasing amount of time (e.g., 1s, 2s, 4s, 8s, etc.) up to a maximum delay of 1 minute. Once a poll succeeds, the delay should reset to the initial interval.

### Meeting Chat Feature
- Implement the MeetingChat panel powered by LLM services which is currently a placeholder. Utilise the same chat bubbles like in the transcript window. The objective of this feature is to allow the user to 'chat' with the transcript via an LLM. This means they will be able to make enquiries about the transcript and receive a response from an LLM provider of their choice as set in the settings modal. Let's first brainstorm how best to implement this feature.

### Settings
- Implement the ability for the user to change the base URL and ports for the web app in the companion app settings modal. This is useful for users who want to self-host Nojoin on a different domain or port. They may instead need to change this using the docker-compose.yml file but having the option in the companion app settings modal is more user friendly. Explore how we could implement this feature.

### Security
- The companion app shortcut to 'Open Nojoin' launches the web app's HTTP address. Though it does automatically redirect to the HTTPS address, it should instead launch the HTTPS address by default which is ending in :14443 and not :14141. Ideally the companion app should should query the api for the correct address to launch rather than hardcoding it because the user might change the ports or base URL in the settings or docker compose.

### Backup/Restore Feature
- Implement a backup/restore feature.
- The backup feature should create a zip file containing all relevant data including the database, recordings, transcripts, notes, tags, and settings. The user should be able to download this zip file to their local machine via the frontend.
- The restore feature should allow the user to upload a previously created backup zip file via the frontend. The backend should then extract the zip file and replace the existing data with the data from the backup in an additive way unless the user checks a 'Clear Existing Data' option. Proper validation and error handling should be implemented to ensure data integrity during the restore process.

### Companion App Deployment
- The companion app should by dynamically compiled by the backend and be served via the frontend via a 'Download Companion' button if practical. This is to ensure that the companion app always has the correct backend URL and ports configured as well as be correct based on the user's platform. They can select their OS (Windows, MacOS, Linux) and the backend should provide the correct companion app binary for them to download and install. Explore how we could implement this feature.
- There should be a simple installation wizard for the companion app once the binary is compiled and downloaded to guide them through the installation process. The wizard just needs to be able to create the necessary shortcuts to launch the companion app and toggle an option to launch it on system startup.
- The companion app should have an auto-update feature to check for new versions of Nojoin and let the user know when an update is available. The user should then be instructed to redownload the companion app from the frontend which will always dynamically compile the latest version.
