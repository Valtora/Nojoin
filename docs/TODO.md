# Nojoin To-Do List
Let's continue the development of Nojoin. Read the PRD.md and the Nojoin-Development-Instructions.md in the docs directory to get an understanding of the project and my development workflow. Your goal is now to present a plan for me to approve in order to achieve the goals and/or tasks and/or TODO items set out below after the colons:

## Multi-Tenant User System
- Remove the 'Email' field from the first-run setup wizard and user creation forms. It is not required for now. Consider the downstream and upstream impacts so we don't break anything.

## Security Audits & Improvements
- Conduct a thorough security audit of the entire application, including frontend, backend, and companion app
- Audit the authentication and authorization mechanisms to ensure that users can only access their own data.
- Implement password hashing and secure storage of user credentials.
- Ensure that all API endpoints are protected and require proper authentication.
- Ensure website security best practices are followed to prevent common vulnerabilities such as SQL injection, XSS, CSRF, etc.
- Ensure SSL is supported and properly configured for secure communication between the frontend, backend, and companion app. Use a library like Let's Encrypt (or better) for managing SSL certificates.

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

## Configuration Cleanup
- Review `config.json` and `config_manager.py` to remove unused keys and defaults (e.g., "llm_user_context", "llm_qa_context", "ui_scale", "min_meeting_length_seconds", "update_preferences").