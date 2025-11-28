# Nojoin To-Do List

## Prompt Engineering - Unsupervised
Let's continue the development of Nojoin. Read Nojoin-Development-Instructions.md in the docs directory to get an understanding of the project. Your goal is now to achieve the goals and/or tasks and/or TODO items set out below after the colons. Since you are a cloud agent running independently you may make decisions on my behalf, this new instruction overrides the prior instructions about always waiting for my approval on your plans. As long as you make a plan. This is also true for unit tests. Since I am delegating this task to you I will not be here to manually test. You must therefore test everything yourself.

## Prompt Engineering - Supervised
Let's continue the development of Nojoin. Read Nojoin-Development-Instructions.md in the docs directory to get an understanding of the project. Your goal is now to present a plan for me to approve in order to achieve the goals and/or tasks and/or TODO items set out below after the colons:

## Tag System
- Investigate the error that occurs when the user attempts to delete a tag that is currently assigned to one or more meetings.

## Meeting Chat Feature
- Implement the MeetingChat panel powered by LLM services which is currently a placeholder. Utilise the same chat bubbles like in the transcript window. The objective of this feature is to allow the user to 'chat' with the transcript via an LLM. This means they will be able to make enquiries about the transcript and receive a response from an LLM provider of their choice as set in the settings modal. Let's first brainstorm how best to implement this feature.

## Meeting Notes Feature
I'm going to share some high level requirements below and I want to discuss and plan this new feature in order to plan out the implementation in detail, considering user stories, edge cases, and future extensibility.

- Implement a robust and reusable pipeline to engage with LLM providers' services, look up their API documentations to ensure best practices are being followed.
- Implement the 'Meeting Notes' feature and component powered by LLM services.
- The Meeting Notes component will appear in the place of the transcript and be a switchable panel such that the user can switch between the Transcript and Notes panel.
- The Transcript Toolbar component may need to be repurposed and act as a navigation bar allowing the user to switch between the Transcript and Notes.
- The existing 'search' and 'search and replace' features as well as the undo/redo and export buttons all need to be context aware and work for both the Transcript and Notes.
- The user should be prompted when exporting via a new modal asking the user if they want the transcript, the notes, or both.
- The changes made by the search and replace function should apply to both the diarized transcript and the meeting notes.
- A very high quality and detailed prompt needs to be generated for the purposes of creating high quality meeting notes. The meeting notes need to contain the following sections: Topics Discussed, Summary, [Notes on each topic, including decisions, rationales, debates, etc. that are logically displayed], Tasks, Misc. It is critical that the prompt produces consistently formatted notes so the prompt should specify strict formatting guidelines. The notes can be as lengthy as required.

## Backup/Restore Feature
- Implement a backup/restore feature.

## Companion App Deployment
- Create an installer (e.g., MSI/NSIS for Windows, DMG for macOS, Deb/RPM for Linux).

- Implement "Run on System Startup" functionality.

- Add logic to create Desktop and Start Menu shortcuts on Windows and equivalents on MacOS and Linux that start the companion app if its not already running and launches the web app.

- Consider packaging the Companion App it as a Windows service and equivalent on other platforms so the user doesn't have to worry about managing the application in the system tray.
