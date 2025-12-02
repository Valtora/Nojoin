# Nojoin To-Do List

Let's continue the development of Nojoin. Read the AGENTS.md file in the project root and the PRD.md in the docs directory to get an understanding of the project. After the colons I will provide a list of tasks/instructions that need to be completed. Your goal is now to present a plan for me to approve to complete these tasks/instructions set out below. Do not move on to implementation without my explicit approval:

### Meeting Chat UI/UX Improvements
- Improve the UI/UX of the meeting chat feature in the frontend.
- The send message button in the chat interface is not centred in the chat input box. Adjust the styling to ensure it is properly centred vertically within the input box.
- While waiting for a response from the AI assistant after sending a message, there is no visual indication that a response is being generated. Implement a loading spinner or similar visual cue to inform the user that their message is being processed. I thought we had implemented token streaming via the LLM_Services.py file, confirm this is the case. Ideally what would happen is that the text streams in real-time as the AI generates the response, rather than waiting for the full response to be ready. That being said we don't need to see the 'thoughts' of the AI model, just the final response streaming in would be sufficient.

### Frontend Upgrades
- Consider using shadcn-ui, see here: https://github.com/shadcn-ui/ui and here https://ui.shadcn.com/docs.
- Explore the feasibility and benefits of integrating shadcn-ui into the current React-based frontend. Evaluate how this change could enhance the user interface and user experience.
- Consider using the Gatsby Framework for the Frontend
- See here: https://www.gatsbyjs.com/docs/porting-from-create-react-app-to-gatsby/
- Explore the feasibility and benefits of migrating the current React-based frontend to use the Gatsby framework. Evaluate how this change could improve performance.

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