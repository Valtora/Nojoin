# Nojoin To-Do List

After the colons I will provide a list of tasks/instructions that need to be completed. Read completely (not just first 100 lines) the files in the docs directory (except the TODO.md file) to get an understanding of the project. Present a plan for approval before making any changes:

## Design and Implement a Robust, Modern Invitation System for Admin Users
- Allow admin users to invite others by creating special invite links they can distribute.
- The link management system should be powerful, the admin should (via a dedicated invite configuration modal) be able to configure a link to expire (in days), toggle whether it is reusable or just valid on a one-link-one-user basis, etc.
- Rather than implementing this under the 'Admin Panel', implement a new 'User Management' page under Settings, visible and editable only to admins.
- Keep the Admin Panel for future use.
- The 'User Management' page should also allow admins to view and manage all users, including their roles and permissions.
- We should also implement a better permission hierarchy system. There should be one 'Owner' that was the user that completes the setup and provided the API keys, this is the absolute owner of the system. Then there should be 'admins' that can be designated by the 'Owner'. This is a simple three tier system. Users cannot acccess admin settings and cannot manage other users. Admins can access admin settings and manage other users, except other admins, and except the Owner.

## Implement Robust Test Suites
- Develop comprehensive unit and integration tests for all major components of Nojoin (api, frontend, worker, db, redis).
- Focus on critical paths: audio ingestion, processing tasks, transcription accuracy, speaker diarization, and frontend playback.
- Use pytest for backend tests, Jest for frontend tests.
- Aim for at least 80% code coverage across the codebase.

## Realtime Transcription Feature
- I want to implement realtime transcription as the default in Nojoin. I will list a few libraries and frameworks below for investigation. I want you to look at each library and assess suitability for Nojoin's architecture.
- Investigate 'speaches' library for realtime transcription capabilities.
- Investigate replacing openai-whisper with 'Faster-Whisper' as it is more performant.
- Investigate using 'RealtimeSTT' for real-time transcription also.
- https://github.com/KoljaB/RealtimeSTT
- https://github.com/SYSTRAN/faster-whisper
- https://speaches.ai/usage/realtime-api/
- https://platform.openai.com/docs/guides/realtime
- https://platform.openai.com/docs/api-reference/realtime-client-events
- https://speaches.ai/usage/open-webui-integration/
- What changes would need to be made to Nojoin?
- Once we discuss and collaboratively produce a plan, AND you receive my explicit approval:
- Implement a realtime transcription feature that streams audio data from the Nojoin Companion app to the backend as it is being recorded. The backend should process this audio data in real-time and return transcription segments to the frontend, which will display them in the transcript window as they are received.
- Let's discuss a way to enable speaker recognition in real-time also, this might mean cleverly generating embeddings that are continually built-on to recognise speakers, perhaps even retroactively going back and updating speakers with low confidence matches before, etc.
- Ensure that the realtime transcription feature is efficient and does not introduce significant latency. Latency of more than 5-10s is unnacceptable. Consider using WebSockets or a similar technology to facilitate low-latency communication between the companion app, backend, and frontend.
- Update the companion app to support streaming audio data to the backend in small chunks as it is being recorded, rather than waiting for the entire recording to finish before uploading.
- Update the backend to handle incoming audio streams, process them using the chosen transcription library, and send transcription segments back to the frontend in real-time.
- Update the frontend to display incoming transcription segments in the transcript window as they are received from the backend.
- Ensure proper error handling and fallback mechanisms are in place in case of network issues or transcription errors.

## Meeting Transcription Feature - Translation
- Use OpenAI's Whisper API to implement a translation feature for meeting transcriptions. This feature should allow users to select a target language for translation after the transcription is complete. The translated text should be displayed alongside the original transcript in the transcript window, with clear labeling to differentiate between the two. Explore how to best integrate this feature into the existing transcription workflow.
- See here: https://github.com/openai/whisper/blob/main/model-card.md

