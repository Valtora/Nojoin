
# Meeting Name Inference - Low Priority - Potential TODO
Implement a first-pass attempt at inferring a title for the meeting based on the context within the transcript.

# Meeting Notes - Low Priority - Potential TODO
Transcripts and recordings are saved on to the disk but meeting notes are not. Should meeting notes also be saved separately to disk?

# Database and Config Backup - High Priority - Definite TODO
Implement a backup and restore system that allows the user to backup their configuration and database along with recordings, transcripts, and notes into a .zip file to a directory of their choosing. The system must allow for the importing of that same .zip file in an additive, non-destructive way, i.e., if there is an existing database with transcripts, recordings, etc. then importing a .zip file of a backup should be imported and added to the existing database.

# LLM User Context - Medium Priority - Definite TODO
Allow the user to provide the LLMs with some custom context. E.g., their name, title, role, company, and whatever else they want to provide as context to the LLMs. This might help with improving meeting note quality and or provide general quality of life increase for the user.
