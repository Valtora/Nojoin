# Nojoin User Guide

## Getting Started

This guide outlines the steps to initialize and utilize the Nojoin platform.

### 1. First Run

1. **Install/Run Companion App:** Ensure the Nojoin Companion App is running in the system tray. This is required for audio capture.
2. **Verify Connection:** Open the Web Client. The status indicator in the bottom left should display "Connected".
3. **Make a Test Recording:**
   - Click the **Record** button (microphone icon) in the sidebar.
   - Speak a few words.
   - Click **Stop**.
4. **View Results:** The recording will appear in the list. Wait for processing to complete (transcription, diarization) to view the transcript and notes.

### 2. Interactive Tour

On the first visit to the dashboard, an interactive tour guides the user through the main features:

1. **Navigation:** Overview of the main sidebar.
2. **Recordings:** Location of processed meetings.
3. **Import:** Procedure for uploading existing audio files.
4. **Companion App:** Guidance on downloading and connecting the system tray app.
5. **Settings:** Configuration of models and preferences.
   - _Note: If environment variables are detected, the setup wizard will be pre-filled with your provided keys._

**Transcript Tour:**
When a recording is viewed for the first time (such as the included "Demo Recording"), a second tour guides the user through the detailed view:

1. **Transcript View:** Reading and navigating the transcript.
2. **Audio Controls:** Playback and speed controls.
3. **Speakers:** Managing and identifying speakers.
4. **AI Notes:** Generating summaries and action items.
5. **Meeting Chat:** Asking questions about the meeting.

These tours can be restarted at any time by resetting preferences in the browser.

## Core Features

### Audio Recording (Companion App)

The Companion App is a lightweight system tray application that handles audio capture on Windows.

- **Headless Operation:** Runs silently in the background.
- **System Tray:**
  - **Status:** Visual indication of state (Idle, Recording, Paused, Error).
  - **Menu:** "Open Nojoin", "Check for Updates", "Help", "About", "Restart", "Exit".
- **Dual-Channel Capture:** Simultaneously records system output (audio heard) and microphone input (audio spoken).
- **Pause & Resume:** Supports pausing via Web Client commands.
- **Smart Uploads:** Audio is sent to the server automatically. If pauses occur, the audio is sent as multiple segments and concatenated on the server.
- **Visual Feedback:** The tray icon changes color/shape based on status. Native system notifications indicate status changes.

**Platform Support:** The companion app currently supports Windows only. Contributors are welcome to help build macOS and Linux versions. Please see the [Contributing Guide](../CONTRIBUTING.md) for details.

### Import Recordings

Existing audio files can be imported directly via the Web Client.

- **Supported Formats:** WAV, MP3, M4A, AAC, WebM, OGG, FLAC, MP4, WMA, Opus.
- **Metadata:** A custom meeting name and the original recording date/time can be specified during import.
- **Processing:** Imported files enter the same processing pipeline (Transcription -> Diarization -> Alignment) as live recordings.

### Transcription & Diarization

Audio processing occurs asynchronously on the server.

- **Process:**
  1. **Validation:** Checks audio file integrity.
  2. **Preprocessing:** Converts audio to mono 16kHz WAV and filters silence (VAD).
  3. **Proxy Creation:** Generates an aligned MP3 for web playback.
  4. **Transcription:** Uses OpenAI Whisper (Local) to generate text.
  5. **Diarization:** Uses Pyannote (Local) for speaker separation.
  6. **Merge & Inference:** Combines transcript and diarization, then uses LLM to infer speaker names (e.g., "John Doe" instead of "Speaker 1").
  7. **Intelligence:** Extracts voiceprints (sampling up to 10 high-quality segments for accuracy), infers a meeting title, and generates comprehensive notes.
- **Progress Tracking:** Real-time status updates are displayed in the Web Client (e.g., "Transcribing...", "Determining speakers...").
- **Export:** Transcripts can be exported to `.txt` format via the Web Client (Transcript Only, Notes Only, or Both).

#### Reprocessing

If a recording fails or if you wish to re-run the pipeline (e.g., after updating models), you can trigger **Retry Processing** from the context menu.

- **Preservation:** The system intelligently preserves:
  - **Manual Speaker Names:** If you have renamed "Speaker 01" to "Alice", reprocessing will keep this assignment.
  - **Speaker Merges:** If you have merged two speakers, they will remain merged after reprocessing.
  - **Notes:** LLM-generated notes are regenerated based on the new transcript.

### Speaker Management

- **Global Speaker Library:** Centralized database of known speakers.
- **Intelligent Linking:**
  - Link "Unknown Speakers" to existing Global Speakers.
  - **Add to People:** Right-click a speaker to add them to the Global Library. This option is hidden if the speaker is already in the library (matched by name or ID).
  - Renaming a speaker offers to update the Global Speaker or create a new one.
- **Voiceprint Management:**
  - **Auto-Extraction:** Can be enabled in Settings.
  - **On-Demand Creation:** Create voiceprints via the "Create Voiceprint" context menu.
  - **Visual Indicator:** Speakers with voiceprints display a fingerprint icon.

### Meeting Intelligence

- **LLM-Powered Notes:**
  - Generates summaries, action items, and key takeaways.
  - **Comprehensive Notes:** Includes Topics, Summary, Detailed Notes, Action Items.
- **Chat Q&A:**
  - "Chat with your meeting" feature allows users to ask questions about specific recordings.
  - Chat history is saved per recording.

### Search & Organization

- **Tagging:** Apply tags to recordings (e.g., "Daily Standup").
- **Advanced Search:** Full-text search across titles, notes, and transcripts. Filter by Date, Tags, and Speakers.
- **Fuzzy Search:** Tolerates typos in search queries.

### Web Playback & Transcript Interface

- **Dashboard:**
  - **Left Sidebar:** List of recordings.
  - **Center Panel:** Transcript and Meeting Notes.
  - **Speaker Panel:** List of identified speakers.
  - **Chat Sidebar:** "Chat with Meeting" functionality.
- **Player:** HTML5 audio player with waveform visualization.
- **Synced Transcript:** Clicking text seeks audio. Text highlights during playback.
- **Edit Mode:** Correct transcript text and speaker assignments in the browser.

### Backup & Restore

Nojoin includes a comprehensive backup system located in **Settings > Backup & Restore**.

- **Create Backup:**
  - Generates a ZIP archive containing the database, configuration, and audio files.
  - **Options:**
    - **Include Audio:** Toggle to include or exclude audio files. Audio is automatically compressed to Opus format to reduce backup size.
- **Restore Backup:**
  - Upload a previously created backup file.
  - **Conflict Resolution:** Choose how to handle data that already exists (Skip, Overwrite).
  - **Selective Restore:** The system intelligently merges data, ensuring no data loss for existing users unless overwrite is explicitly selected.
- **Redaction:** Sensitive keys (API keys, etc.) are redacted from backups for security. You may need to re-enter them after restoration if migrating to a new server.

### Settings & Configuration

- **Server Settings:** Manage API keys, model selection, and storage paths.
- **AI Settings:**
  - **Whisper Model Management:** View installed Whisper models. Download new models (e.g. `turbo`, `large-v3`) or delete unused ones to free up disk space.
  - **LLM Provider:** Configure OpenAI, Anthropic, Gemini, or Ollama connections.
    - **Privacy Note:** Using a remote LLM provider (OpenAI, Anthropic, Gemini) will send your meeting transcripts to external services. For a pure private mode where data never leaves your server, you must configure a local Ollama instance.
- **Help:**
  - **Tours & Demos:** Restart the Welcome/Transcript tours or re-create the demo meeting.
  - **Report a Bug:** Direct link to report issues on the GitHub repository.
- **User Preferences:** Theme selection (Dark/Light), default playback speed.
- **System Version:** The current version is displayed in the Settings header. If an update is available on GitHub, a notification will appear.

### System Management (Admin Only)

Accessible via **Settings > System**, this panel provides tools for server maintenance.

- **System Restart:** safely restarts all Nojoin services (API, Worker, Database, etc.) directly from the UI.
  - Handles backend startup delays and automatically reconnects when the system is back online.
- **Log Streaming:** Real-time diagnostics for the entire stack.
  - **Unified Log View:** Default view shows a merged stream of all container logs (`all`), with each line prefixed by the service name.
  - **Single Service View:** Drill down into specific containers (e.g. `nojoin-worker`).
  - **Filters:**
    - **Text/Regex:** Filter logs by keyword or regex pattern.
    - **Log Level:** Filter by severity (`DEBUG`, `INFO`, `WARN`, `ERROR`).
  - **Download:** Export the current log stream to a text file for sharing or analysis.
