# Git Commit Description - Use Conventional Commit Guidelines

refactor: Remove Custom Instructions feature

- Removed `custom_instructions` from LLM backend services (Gemini, OpenAI, Anthropic, Ollama) to prevent potential query malformation.
- Removed Custom Instructions UI section from GeneralSettings.
- Removed `chat_custom_instructions` from frontend TypeScript interfaces.
- Updated `transcripts.py` endpoint to stop passing custom instructions to the LLM backend.

docs(pull_request_template.md): Updated pull request template

docs(PRD, USAGE): updated docs to reflect latest state of project
