# Nojoin To-Do List

Let's continue the development of Nojoin. Read AGENTS.md and the PRD.md files in the docs directory to get an understanding of the project. After the colons I will provide a list of tasks/instructions that need to be completed:

### First-Run Wizard - UX Improvements
- Separate the model download spinners so that the Whisper model, pyannote model, and the voice embedding models have their own spinners. Currently the spinner resets from 100% back to 60% after downloading the whisper model.
- Use the NojoinLogo.png in the frontend/public/assets folder instead of a generic microphone icon.
- Suppress the 'Worker Offline' warning notification for 10 seconds after the first-run setup to allow the worker to setup.
- The Settings > AI Services page incorrectly shows the pyannote diarization and voice embedding models as 'missing'. These should be downloaded on first run assuming a HuggingFace token has been provided. They show up as 'ready' after I manually press the 'Download / Update All Models' button.
- The similar to how the LLM Provider API key is not a strict requirement, we should inform the user that they also don't have to provide a HF token but this will disable the diarization and voice embedding features leaving Only the whisper transcription capability.

### First-Run Wizard and LLM Services - LLM Model Management
- I can see in the code that there are hardcoded 'fallback' models in the LLM_Services.py that will be listed in the API call fails. This is NOT the intention of the API key validation during the first-run setup. If the API call fails due to an invalid key then warn the user that the key is invalid and do not list any hardcoded models.
- There should be no 'default' models. The admin user HAS to select a model on first-run setup.
- Allow the admin user to not provide the a valid API key on the first-run wizard (setup page) but let them know that the AI features such as note generation and meeting chat will not work until they provide a valid API key and select a model in the AI Services page in the settings.
- Double check the the processing pipeline to ensure the graceful skipping of LLM powered features such as note generation and speaker inference. Also gracefully handle cases where the user attempts to sent a chat message without an a valid LLM model activated. We should ideally have in place a mechanism to 'disable' the meeting chat to prevent the user from attempting to send chat messages (not to hide it, but perhaps an overlay?). Make it clear the feature is will be enabled if a valid API has been provided for an LLM provider and a model is selected. Keep the message simple for admin users such as 'Chat feature is disabled... No valid API key provided.'. If a non-admin user is viewing the disabled chat panel then the message should let them know that the admin has to deal with this issue, something like 'Chat feature is disabled... Contact your admin.'
- Review the model selection logic and implement a more robust method to select the latest models from the providers. The documentation on how to list models via an API call can be found at the following links for the respective providers: OpenAI - https://platform.openai.com/docs/api-reference/models/list, Google - https://ai.google.dev/api/models, Anthropic - https://platform.claude.com/docs/en/api/python/beta/models/list
- Update the AI Services page to also include a dropdown for model selection that is populated via the API calls above.
- A reminder that Only the admin user should be able to provide/change API keys and select LLM models. A normal user should just be informed that only an Admin user can change these settings and they should be locked out of changing these settings.

### Celery Worker - Startup Routine
- Confirm the current startup routine of the celery worker. What does the worker check for, what does it attempt to download?
- During the first-run setup in the background I think the worker attempts to download the huggingface models without a HF token. It should ideally wait until a HF token is provided but let's first confirm the current setup.

### Security
- Explore feasibility of removing HTTP support entirely and only allowing HTTPS connections to the Nojoin backend server. This may involve generating self-signed certificates for local development and self-hosting via trafeik, caddy, etc.
- Research the implications of this change on the companion app's ability to connect to the backend server and any other parts of the system that may be affected. Provide a summary of findings and recommendations on whether this change should be implemented and how to go about it if so.

### Consider Upgrading to Cuda 13
- See this link: https://www.google.com/search?q=cuda+12.6+vs+12.8+vs+13.0&num=10&client=firefox-b-d&sca_esv=30d2ca42fc4644ef&sxsrf=AE3TifPQ_jdRbNECXpjGm2Ar-AilIFMQyw%3A1764454298783&ei=mm8raYvJL-2jhbIPzv6w-Ag&oq=cuda+12.6+vs&gs_lp=Egxnd3Mtd2l6LXNlcnAiDGN1ZGEgMTIuNiB2cyoCCAIyBRAAGIAEMgUQABiABDIFEAAYgAQyBRAAGIAEMgUQABiABDIIEAAYFhgKGB4yBhAAGBYYHjIGEAAYFhgeMgYQABgWGB4yCBAAGBYYChgeSKwOUIcBWOMCcAF4AJABAJgBRKABwAGqAQEzuAEByAEA-AEBmAIEoALNAcICDRAAGIAEGLADGEMYigXCAgoQABiABBhDGIoFwgIKEAAYgAQYFBiHApgDAIgGAZAGCpIHATSgB_QWsgcBM7gHyQHCBwUwLjIuMsgHCg&sclient=gws-wiz-serp
- See this link too: https://pytorch.org/get-started/locally/

### Meeting Transcription Feature - Translation
- Use OpenAI's Whisper API to implement a translation feature for meeting transcriptions. This feature should allow users to select a target language for translation after the transcription is complete. The translated text should be displayed alongside the original transcript in the transcript window, with clear labeling to differentiate between the two. Explore how to best integrate this feature into the existing transcription workflow.
- See here: https://github.com/openai/whisper/blob/main/model-card.md

### Backup/Restore Feature
- Implement a backup/restore feature.
- The backup feature should create a zip file containing all relevant data including the database, recordings, transcripts, notes, tags, and settings. The user should be able to download this zip file to their local machine via the frontend.
- The restore feature should allow the user to upload a previously created backup zip file via the frontend. The backend should then extract the zip file and replace the existing data with the data from the backup in an additive way unless the user checks a 'Clear Existing Data' option. Proper validation and error handling should be implemented to ensure data integrity during the restore process.

### LICENCE File
- Help me to create an appropriate LICENCE file for the Nojoin project. I may decide to monetise Nojoin in the future by having a 'community' version that is open source and self-hosted as well as a 'pro' version that is closed source and has additional features. Therefore I want to choose a licence that allows me to do this. Research and suggest a suitable open source licence for the Nojoin project that meets these criteria and also considers the dependencies used in the project.