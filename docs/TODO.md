# Nojoin To-Do List

## Prompt For Unsupervised Agents
Let's continue the development of Nojoin. Read Nojoin-Development-Instructions.md in the docs directory to get an understanding of the project. Your goal is now to achieve the goals and/or tasks and/or TODO items set out below after the colons. Since you are a cloud agent running independently you may make decisions on my behalf, this new instruction overrides the prior instructions about always waiting for my approval on your plans. As long as you make a plan. This is also true for unit tests. Since I am delegating this task to you I will not be here to manually test. You must therefore test everything yourself:

## Prompt For Supervised Agents
Let's continue the development of Nojoin. Read Nojoin-Development-Instructions.md in the docs directory to get an understanding of the project. Your goal is now to present a plan for me to approve in order to achieve the goals and/or tasks and/or TODO items set out below after the colons:

## Meeting Chat Feature
- Implement the MeetingChat panel powered by LLM services which is currently a placeholder. Utilise the same chat bubbles like in the transcript window. The objective of this feature is to allow the user to 'chat' with the transcript via an LLM. This means they will be able to make enquiries about the transcript and receive a response from an LLM provider of their choice as set in the settings modal. Let's first brainstorm how best to implement this feature.

# Settings
- Implement the ability for the user to change the base URL and ports for the web app in the companion app settings modal. This is useful for users who want to self-host Nojoin on a different domain or port. They may instead need to change this using the docker-compose.yml file but having the option in the companion app settings modal is more user friendly. Explore how we could implement this feature.
- Confirm if the default setting for auto creation of voiceprints and meeting notes are both enabled by default.
- Implement a notification to let the user know when meeting notes have been generated successfully.
- In the Settings > AI Services tab there should be a toggle for 'Auto-generate Meeting Notes' as some users may not want to have automatic notes generated. Add this under the 'Processing' section in the AI Services tab. The 'Processing' section should be renamed to 'Meeting Processing' too.
- In the Settings > AI Services tab only the API key field for the selected LLM Provider should be visible. I.e., dynamically show and hide the API key fields based on which provider is selected.

# Security
- The companion app shortcut to 'Open Nojoin' launches the web app's HTTP address. It should instead launch the HTTPS address which is ending in :14443 and not :14141. Ideally the companion app should should query the api for the correct address to launch rather than hardcoding it because the user might change the ports or base URL in the settings.

## Backup/Restore Feature
- Implement a backup/restore feature.

## Companion App Deployment
- Create an installer (e.g., MSI/NSIS for Windows, DMG for macOS, Deb/RPM for Linux).

- Implement "Run on System Startup" functionality.

- Add logic to create Desktop and Start Menu shortcuts on Windows and equivalents on MacOS and Linux that start the companion app if its not already running and launches the web app.

- Consider packaging the Companion App it as a Windows service and equivalent on other platforms so the user doesn't have to worry about managing the application in the system tray.
