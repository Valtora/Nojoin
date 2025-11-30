# Nojoin To-Do List

Let's continue the development of Nojoin. Read the PRD.md in the docs directory to get an understanding of the project. After the colons I will provide a list of tasks/instructions that need to be completed. Your goal is now to present a plan for me to approve to complete these tasks/instructions set out below:

### Settings Page > AI Services Tab - 'Local Models' Section
- Confirm that the 'Download / Update Models' button functions correctly by initiating the download of available local models from the backend when clicked.
- Confirm where and how the models are stored, look at the docker compose file to see the mounts and volumes used for model storage.
- Improve the UI/UX of the 'Local Models' section in the AI Services tab of the Settings page. This includes adding clear indicators for which models are currently downloaded and available for use, as well as providing options to download or remove models directly from this section. Implement progress bars for model downloads and ensure that the user receives feedback on the success or failure of these actions.
- There is a 'refresh' button already there to check which models are downloaded. Ensure this functions correctly and updates the UI accordingly. Use the notification system to inform the user when the refresh is complete.

### Settings - Custom Base URL and Ports
- Implement the ability for the user to change the base URL and ports for the web app in the companion app settings modal. This is useful for users who want to self-host Nojoin on a different domain or port. They may instead need to change this using the docker-compose.yml file but having the option in the companion app settings modal is more user friendly. Explore how we could implement this feature.

### Consider Upgrading to Cuda 13
- See this link: https://www.google.com/search?q=cuda+12.6+vs+12.8+vs+13.0&num=10&client=firefox-b-d&sca_esv=30d2ca42fc4644ef&sxsrf=AE3TifPQ_jdRbNECXpjGm2Ar-AilIFMQyw%3A1764454298783&ei=mm8raYvJL-2jhbIPzv6w-Ag&oq=cuda+12.6+vs&gs_lp=Egxnd3Mtd2l6LXNlcnAiDGN1ZGEgMTIuNiB2cyoCCAIyBRAAGIAEMgUQABiABDIFEAAYgAQyBRAAGIAEMgUQABiABDIIEAAYFhgKGB4yBhAAGBYYHjIGEAAYFhgeMgYQABgWGB4yCBAAGBYYChgeSKwOUIcBWOMCcAF4AJABAJgBRKABwAGqAQEzuAEByAEA-AEBmAIEoALNAcICDRAAGIAEGLADGEMYigXCAgoQABiABBhDGIoFwgIKEAAYgAQYFBiHApgDAIgGAZAGCpIHATSgB_QWsgcBM7gHyQHCBwUwLjIuMsgHCg&sclient=gws-wiz-serp
- See this link too: https://pytorch.org/get-started/locally/

### Meeting Chat Feature
- Implement the MeetingChat panel powered by LLM services which is currently a placeholder. Utilise the same chat bubbles like in the transcript window. The objective of this feature is to allow the user to 'chat' with the transcript via an LLM. This means they will be able to make enquiries about the transcript and receive a response from an LLM provider of their choice as set in the settings modal. Let's first brainstorm how best to implement this feature.

### PRD Review
- Perform a comprehensive review of the PRD to ensure it is up to date with the current state of the Nojoin project. Identify any discrepancies or areas that require clarification or expansion. Summarize your findings and propose any necessary updates to the PRD to align it with the project's goals and features.

### Meeting Transcription Feature - Translation
- Use OpenAI's Whisper API to implement a translation feature for meeting transcriptions. This feature should allow users to select a target language for translation after the transcription is complete. The translated text should be displayed alongside the original transcript in the transcript window, with clear labeling to differentiate between the two. Explore how to best integrate this feature into the existing transcription workflow.
- See here: https://github.com/openai/whisper/blob/main/model-card.md

### Backup/Restore Feature
- Implement a backup/restore feature.
- The backup feature should create a zip file containing all relevant data including the database, recordings, transcripts, notes, tags, and settings. The user should be able to download this zip file to their local machine via the frontend.
- The restore feature should allow the user to upload a previously created backup zip file via the frontend. The backend should then extract the zip file and replace the existing data with the data from the backup in an additive way unless the user checks a 'Clear Existing Data' option. Proper validation and error handling should be implemented to ensure data integrity during the restore process.

### Companion App Deployment
- The companion app should by dynamically compiled by the backend and be served via the frontend via a 'Download Companion' button if practical. This is to ensure that the companion app always has the correct backend URL and ports configured as well as be correct based on the user's platform. They can select their OS (Windows, MacOS, Linux) and the backend should provide the correct companion app binary for them to download and install. Explore how we could implement this feature.
- There should be a simple installation wizard for the companion app once the binary is compiled and downloaded to guide them through the installation process. The wizard just needs to be able to create the necessary shortcuts to launch the companion app and toggle an option to launch it on system startup.
- The companion app should have an auto-update feature to check for new versions of Nojoin and let the user know when an update is available. The user should then be instructed to redownload the companion app from the frontend which will always dynamically compile the latest version.

### LICENCE File
- Help me to create an appropriate LICENCE file for the Nojoin project. I may decide to monetise Nojoin in the future by having a 'community' version that is open source and self-hosted as well as a 'pro' version that is closed source and has additional features. Therefore I want to choose a licence that allows me to do this. Research and suggest a suitable open source licence for the Nojoin project that meets these criteria and also considers the dependencies used in the project.