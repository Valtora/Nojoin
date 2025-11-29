# Nojoin To-Do List

## Prompt For Unsupervised Agents
Let's continue the development of Nojoin. Read Nojoin-Development-Instructions.md in the docs directory to get an understanding of the project. Your goal is now to achieve the goals and/or tasks and/or TODO items set out below after the colons. Since you are a cloud agent running independently you may make decisions on my behalf, this new instruction overrides the prior instructions about always waiting for my approval on your plans. As long as you make a plan. This is also true for unit tests. Since I am delegating this task to you I will not be here to manually test. You must therefore test everything yourself:

## Prompt For Supervised Agents
Let's continue the development of Nojoin. Read Nojoin-Development-Instructions.md in the docs directory to get an understanding of the project. Your goal is now to present a plan for me to approve in order to achieve the goals and/or tasks and/or TODO items set out below after the colons:

## UI/UX Improvements
- [x] Allow the user to resize the main panels in the web app (e.g., Transcript, Notes, Participants) by dragging the dividers between them. Explore how best to implement this feature. There may need to be some constraints on minimum and maximum sizes for each panel to ensure usability.

## API / Notification Improvements
- Change the health check poll to only occur every 10 seconds or when an action is taken that requires the backend to be running (e.g., start recording, stop recording, fetch recordings, etc). This will reduce unnecessary network traffic and resource usage when the app is idle.

## Meeting Chat Feature
- Implement the MeetingChat panel powered by LLM services which is currently a placeholder. Utilise the same chat bubbles like in the transcript window. The objective of this feature is to allow the user to 'chat' with the transcript via an LLM. This means they will be able to make enquiries about the transcript and receive a response from an LLM provider of their choice as set in the settings modal. Let's first brainstorm how best to implement this feature.

# Settings
- Implement the ability for the user to change the base URL and ports for the web app in the companion app settings modal. This is useful for users who want to self-host Nojoin on a different domain or port. They may instead need to change this using the docker-compose.yml file but having the option in the companion app settings modal is more user friendly. Explore how we could implement this feature.

# Security
- The companion app shortcut to 'Open Nojoin' launches the web app's HTTP address. Though it does automatically redirect to the HTTPS address, it should instead launch the HTTPS address by default which is ending in :14443 and not :14141. Ideally the companion app should should query the api for the correct address to launch rather than hardcoding it because the user might change the ports or base URL in the settings or docker compose.

## Backup/Restore Feature
- Implement a backup/restore feature.

## Companion App Deployment
- Create an installer (e.g., MSI/NSIS for Windows, DMG for macOS, Deb/RPM for Linux).

- Implement "Run on System Startup" functionality.

- Add logic to create Desktop and Start Menu shortcuts on Windows and equivalents on MacOS and Linux that start the companion app if its not already running and launches the web app.

- Consider packaging the Companion App it as a Windows service and equivalent on other platforms so the user doesn't have to worry about managing the application in the system tray.
