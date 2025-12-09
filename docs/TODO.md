# Nojoin To-Do List

After the colons I will provide a list of tasks/instructions that need to be completed. Now read completely (not just first 100 lines) the following files in the /docs directory to get an understanding of the project: AGENTS.md, DEPLOYMENT.md, PRD.md, and USAGE.md. Present a plan for approval before making any changes:

## Meeting Recording Improvements
- If a meeting is recorded and no speech is detected, the whisper model can sometimes hallucinate words, let's make the processing pipeline more robust so as to simply return an empty transcript in such cases. We should also warn the user that in instances of no speech detected, the transcript will be empty but the whisper model may still produce hallucinated text.

## Backup/Restore System
- Review the UI/UX and implementation of the backup/restore system. Ensure it is intuitive and robust.
- Ensure the UI/UX is consisent with the rest of the frontend, including the 'Browse...' button styling and placement.
