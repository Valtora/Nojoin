# Nojoin To-Do List

After the colons I will provide a list of tasks/instructions that need to be completed. Now read completely (not just first 100 lines) the following files in the /docs directory to get an understanding of the project: AGENTS.md, DEPLOYMENT.md, PRD.md, and USAGE.md. Present a plan for approval before making any changes:

## Security and Sanitisation
- Review how input sanitisation is handled for all user inputs to prevent injection attacks.
- Implement input validation for all settings to ensure they conform to expected formats and ranges. Provide a clear error notification toast if invalid inputs are detected. Use tooltips to inform users of valid input formats.

## macOS Companion App
- The companion app is not able to send notifications on macOS due to missing permissions.
  - Research and implement a method to request the necessary permissions from the user on first launch.
  - Update the macOS build configuration to include any required entitlements for sending notifications.