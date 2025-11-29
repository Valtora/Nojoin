# Nojoin To-Do List

## Prompt For Unsupervised Agents
Let's continue the development of Nojoin. Read Nojoin-Development-Instructions.md in the docs directory to get an understanding of the project. Your goal is now to achieve the goals and/or tasks and/or TODO items set out below after the colons. Since you are a cloud agent running independently you may make decisions on my behalf, this new instruction overrides the prior instructions about always waiting for my approval on your plans. As long as you make a plan. This is also true for unit tests. Since I am delegating this task to you I will not be here to manually test. You must therefore test everything yourself:

## Prompt For Supervised Agents
Let's continue the development of Nojoin. Read Nojoin-Development-Instructions.md in the docs directory to get an understanding of the project. Your goal is now to present a plan for me to approve in order to achieve the goals and/or tasks and/or TODO items set out below after the colons:

## Voiceprints Feature
- Confirm the 'Create All Voiceprints' button functionality in the Voiceprints panel. Ensure that when clicked, it generates voiceprints for all recordings that do not already have a voiceprint associated with them. Verify that the voiceprints are correctly stored and can be used for speaker identification in future recordings.

## Meeting Notes Feature
- The 

## API / Notification Improvements
- Change the health check poll to only occur every 10 seconds or when an action is taken that requires the backend to be running (e.g., start recording, stop recording, fetch recordings, etc). This will reduce unnecessary network traffic and resource usage when the app is idle.

# First-Run Improvements
- Add the Nojoin logo to the first run wizard.
- Change the wording of the Setting Up AI Models page to 'Please wait while we download the necessary dependencies. This may take a few minutes. For the Whisper Model download I can see a progress bar in the terminal, can we show that in the UI too?' to make it clearer to the user what is happening.
- After the first run completes the user is redirected to the login page, instead the newly created admin account should be logged in.

## Meeting Chat Feature
- Implement the MeetingChat panel powered by LLM services which is currently a placeholder. Utilise the same chat bubbles like in the transcript window. The objective of this feature is to allow the user to 'chat' with the transcript via an LLM. This means they will be able to make enquiries about the transcript and receive a response from an LLM provider of their choice as set in the settings modal. Let's first brainstorm how best to implement this feature.

# Settings
- Implement the ability for the user to change the base URL and ports for the web app in the companion app settings modal. This is useful for users who want to self-host Nojoin on a different domain or port. They may instead need to change this using the docker-compose.yml file but having the option in the companion app settings modal is more user friendly. Explore how we could implement this feature.

# Security
- The companion app shortcut to 'Open Nojoin' launches the web app's HTTP address. Though it does automatically redirect to the HTTPS address, it should instead launch the HTTPS address by default which is ending in :14443 and not :14141. Ideally the companion app should should query the api for the correct address to launch rather than hardcoding it because the user might change the ports or base URL in the settings or docker compose.

## Backup/Restore Feature
- Implement a backup/restore feature.
- The backup feature should create a zip file containing all relevant data including the database, recordings, transcripts, notes, tags, and settings. The user should be able to download this zip file to their local machine via the frontend.
- The restore feature should allow the user to upload a previously created backup zip file via the frontend. The backend should then extract the zip file and replace the existing data with the data from the backup in an additive way unless the user checks a 'Clear Existing Data' option. Proper validation and error handling should be implemented to ensure data integrity during the restore process.

## Companion App Deployment
- The companion app should by dynamically compiled by the backend and be served via the frontend via a 'Download Companion' button if practical. This is to ensure that the companion app always has the correct backend URL and ports configured as well as be correct based on the user's platform. They can select their OS (Windows, MacOS, Linux) and the backend should provide the correct companion app binary for them to download and install. Explore how we could implement this feature.
- There should be a simple installation wizard for the companion app once the binary is compiled and downloaded to guide them through the installation process. The wizard just needs to be able to create the necessary shortcuts to launch the companion app and toggle an option to launch it on system startup.
- The companion app should have an auto-update feature to check for new versions of Nojoin and let the user know when an update is available. The user should then be instructed to redownload the companion app from the frontend which will always dynamically compile the latest version.
