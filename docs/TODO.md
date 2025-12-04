# Nojoin To-Do List

Let's continue the development of Nojoin. Read completely (not just first 100 lines) the AGENTS.md and the PRD.md files in the docs directory to get an understanding of the project. After the colons I will provide a list of tasks/instructions that need to be completed. Now present a plan for my approval to:

## Implement Support for Local AI Models via Ollama and LocalAI
- Research how to integrate local AI models using Ollama (https://ollama.com/) and LocalAI (https://localai.io/). This may involve exploring their APIs, installation procedures, and any specific requirements for running these models locally.
- They should be an option in both the first-run setup and in the settings page. The dropdown for model selection will need to query these local services to get a list of available models.
- Warn the user that local models may have different performance characteristics compared to cloud-based LLM providers.

## Meeting Transcription Feature - Translation
- Use OpenAI's Whisper API to implement a translation feature for meeting transcriptions. This feature should allow users to select a target language for translation after the transcription is complete. The translated text should be displayed alongside the original transcript in the transcript window, with clear labeling to differentiate between the two. Explore how to best integrate this feature into the existing transcription workflow.
- See here: https://github.com/openai/whisper/blob/main/model-card.md

## Backup/Restore Feature
- Implement a backup/restore feature.
- The backup feature should create a zip file containing all relevant data including the database, recordings, transcripts, notes, tags, and settings. The user should be able to download this zip file to their local machine via the frontend.
- The restore feature should allow the user to upload a previously created backup zip file via the frontend. The backend should then extract the zip file and replace the existing data with the data from the backup in an additive way unless the user checks a 'Clear Existing Data' option. Proper validation and error handling should be implemented to ensure data integrity during the restore process.
