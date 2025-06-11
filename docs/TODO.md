
## Meeting Name Inference - Low Priority - Potential TODO
Implement a first-pass attempt at inferring a title for the meeting based on the context within the transcript.

## Meeting Notes - Low Priority - Potential TODO
Transcripts and recordings are saved on to the disk but meeting notes are not. Should meeting notes also be saved separately to disk? We need to consider the saving and loading of the notes from disk. Currently its saved in the database so we need to decide on a source of truth.

## Wider Database Architecture - Potential TODO
Review how transcripts and notes are stored in the database. Should we stop storing them on disk? What would the implications be? It is cleaner to have just one database file but the ability to perform operations on transcript files are core to the app's functionality at the moment.

## Database and Config Backup - High Priority - Definite TODO
Implement a backup and restore system that allows the user to backup their configuration and database along with recordings, transcripts, and notes into a .zip file to a directory of their choosing. The system must allow for the importing of that same .zip file in an additive, non-destructive way, i.e., if there is an existing database with transcripts, recordings, etc. then importing a .zip file of a backup should be imported and added to the existing database.

## LLM User Context - Medium Priority - Definite TODO
Allow the user to provide the LLMs with some custom context. E.g., their name, title, role, company, and whatever else they want to provide as context to the LLMs. This might help with improving meeting note quality and or provide general quality of life increase for the user.

## Updater - High Priority - Potential TODO
Build an version management system that checks for updates to Nojoin. If an update is found the user should be prompted if they want to update. They should have the option to be reminded never, on next-run, in one week, or one month. The user should be able to manually check for an update via the settings dialog using a 'Check for Updates' button. If the user decides to update,the system should proceed with the update.

This might require the creation of a standalone update script with its own process, terminal, GUI, etc. It will need to use a temp directory to copy the necessary update scripts into because the original directory will need to be deleted/overwritten. Then it will need to backup the user config and database (as above) to the same temp directory. Then the update system will need to run the update script from the temp directory, close the main Nojoin app, and update the Nojoin directory. It then needs to restore the user's database and config, merging them if necessary.

The database will need to have operations to import/merge to allow for backwards compatability in for new updates. This will only be needed if we make changes to the database architecture later. Once done the Nojoin application should be restarted. Possibly a button to view commit changes/releases, etc. can be added here.

## Advanced Meeting Analysis - Potential TODO
Implement advanced analyses for both the audio and transcript to gauge things like speaker sentiments, engagement, bias, etc. with a view to extract as much information as possible from the meetings' interactions. Look at things like time spent on topics.

## Codebase Polish - Definite TODO
Prune the codebase of unnecessary or overly verbose comments. Especially where code has been commented out and is now obsolete.