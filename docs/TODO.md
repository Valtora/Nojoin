# Nojoin To-Do List

After the colons I will provide a list of tasks/instructions that need to be completed. Read completely (not just first 100 lines) the files in the docs directory (except the TODO.md file) to get an understanding of the project. Present a plan for approval before making any changes:

## Implement Robust Test Suites
- Develop comprehensive unit and integration tests for all major components of Nojoin (api, frontend, worker, db, redis).
- Focus on critical paths: audio ingestion, processing tasks, transcription accuracy, speaker diarization, and frontend playback.
- Use pytest for backend tests, Jest for frontend tests.
- Aim for at least 80% code coverage across the codebase.

## Audio Playback Optimization (Proxy Files)
- Implement a "Proxy File" strategy for robust audio playback across all browsers (especially Firefox) and network conditions.
- **Strategy**: Keep the original high-quality WAV file as the "Master" for AI processing (Diarization, Transcription) but generate a lightweight compressed "Proxy" file (MP3, ~128kbps) for frontend playback.
- **Database**: Update the `Recording` model to include a `proxy_path` field.
- **Ingestion**: Update `finalize_upload` and `import_audio` to trigger a background task (`convert_to_proxy_task`) that generates the MP3 using `ffmpeg`.
- **Streaming**: Update the `stream_recording` endpoint to check for and serve the `proxy_path` if available. Fallback to the WAV file (with existing chunking logic) if the proxy is missing.
- **Frontend**: No major changes required, as the browser will transparently receive the MP3 stream.
- **Note**: Ensure "Retry Processing" actions continue to use the Master WAV file to prevent generation loss.

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

