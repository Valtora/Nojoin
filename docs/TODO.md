# Nojoin To-Do List

Let's continue the development of Nojoin. Read AGENTS.md and the PRD.md files in the docs directory to get an understanding of the project. After the colons I will provide a list of tasks/instructions that need to be completed:

### First-Run Wizard and LLM Services - Dynamically Update Default Models
- Implement a mechanism to dynamically update the models being used for each LLM provider in the LLM_Services.py module. After the user selects their preferred LLM provider and enters their API key during the first-run wizard, the application should query the respective LLM provider's API to retrieve the latest list of available models. The default model for each provider should then be set by the user. Most LLM Providers have a model.list or similar api endpoint that can be used for this purpose.
- While doing this we can improve the first-run wizard logic to force the user to validate their API keys before proceeding.
- Only the admin user should be able to provide API keys and set default models for LLM providers with the normal users just piggybacking off these settings from the admin.

### Speaker Library Management
- Give the user the ability to delete a voice print (embedding) associated from a speaker in the Speaker Library which just removes their voice embedding from the database but keeps the speaker entry itself and any associated transcripts/notes/tags intact. I think the current logic only allows a speaker to be in the Speaker Library if they have an associated voice embedding, so this will require some changes to the database schema and the Speaker Library management logic. There should be no requirement to have a voice embedding for a speaker to exist in the Speaker Library.

### Meeting Chat Feature - UI/UX Improvements
- Remove the button that shows/hides the chat panel, the chat panel should just always be visible.

### Bulk Delete Recordings - Error
- Investigate this error from the log when I tried to delete two recordings at once (they did have the same name): INFO:     172.18.0.6:54256 - "DELETE /api/v1/recordings/batch/permanent HTTP/1.1" 422 Unprocessable Entity

### Security
- Explore feasibility of removing HTTP support entirely and only allowing HTTPS connections to the Nojoin backend server. This may involve generating self-signed certificates for local development and self-hosting via trafeik, caddy, etc. Research the implications of this change on the companion app's ability to connect to the backend server and any other parts of the system that may be affected. Provide a summary of findings and recommendations on whether this change should be implemented and how to go about it if so.

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