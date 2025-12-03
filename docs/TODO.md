# Nojoin To-Do List

Let's continue the development of Nojoin. Read completely (not just first 100 lines) the AGENTS.md and the PRD.md files in the docs directory to get an understanding of the project. After the colons I will provide a list of tasks/instructions that need to be completed:

## Nojoin Shortcut Logo
- The logo for Nojoin shortcuts (e.g. desktop shortcut created by the installer) is lower resolution than the asset used. What could be the cause of this? It should be a high resolution icon. Investigate and report back.

## Companion App - System Tray
- When hovering over the system tray icon for the companion app, it currently shows a small blank empty bubble. Investigate how to either remove this or populate it with useful information such as the current status text from the context menu.

## Companion App - Reauthorisation
- Consider the case where the user moves from a local docker deployment to a remote server deployment.
- Will they need to reauthorise the companion app to connect to the new server?
- If so, implement a way for the user to trigger reauthorisation via the web app front end similar to how it is done for the initial authorisation.

## Companion App - Auto-Updater
- Investigate how we can implement an auto-update function that periodically checks for updates and notifies the user if there is an update to download. Using the notification system in the companion app, the user should be able to click 'Update Now' or 'Not Now' that appears in the notification toast, with the auto-update check occuring again on next start-up if they click 'Update Now'.
- If they click Update Now then the companion app should silently download and update itself. If successfully updated and restarted there should be another notification along the lines of 'Nojoin Companion App Updated vX.X.X'.
- Add another button in the main sidebar of the web app that shows up when the companion app is connected but not running the latest version. This button should say 'Update Companion App' and clicking it should trigger the same update process as above.

## Meeting Transcription Feature - Translation
- Use OpenAI's Whisper API to implement a translation feature for meeting transcriptions. This feature should allow users to select a target language for translation after the transcription is complete. The translated text should be displayed alongside the original transcript in the transcript window, with clear labeling to differentiate between the two. Explore how to best integrate this feature into the existing transcription workflow.
- See here: https://github.com/openai/whisper/blob/main/model-card.md

## Backup/Restore Feature
- Implement a backup/restore feature.
- The backup feature should create a zip file containing all relevant data including the database, recordings, transcripts, notes, tags, and settings. The user should be able to download this zip file to their local machine via the frontend.
- The restore feature should allow the user to upload a previously created backup zip file via the frontend. The backend should then extract the zip file and replace the existing data with the data from the backup in an additive way unless the user checks a 'Clear Existing Data' option. Proper validation and error handling should be implemented to ensure data integrity during the restore process.
