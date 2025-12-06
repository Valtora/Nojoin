# Nojoin To-Do List

After the colons I will provide a list of tasks/instructions that need to be completed. Read completely (not just first 100 lines) the files in the docs directory (except the TODO.md file) to get an understanding of the project. Present a plan for approval before making any changes:

## Audio Playback Optimization (Transcoding)
- Implement on-the-fly transcoding for audio playback to improve robustness on slow networks and reduce bandwidth usage.
- Create a new endpoint parameter or separate endpoint (e.g., `/api/v1/recordings/{id}/stream?format=mp3`) that accepts a target format.
- Use `ffmpeg` on the backend to transcode the original WAV file to a lower bitrate format (e.g., MP3 128kbps) in real-time or cache a transcoded version.
- This will significantly reduce the chunk size needed for the same duration of audio (e.g., 1MB = ~1 minute of MP3 vs ~6 seconds of WAV), making seeking and scrubbing much smoother.
- Update the frontend `AudioPlayer` to request the compressed format by default, potentially with a quality toggle.

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

