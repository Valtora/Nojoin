# Nojoin User Guide

## Core Features

### Audio Recording (Companion App)
The Companion App is a lightweight system tray application that handles audio capture.

*   **Headless Operation:** Runs silently in the background.
*   **System Tray:**
    *   **Status:** Visual indication of state (Idle, Recording, Paused, Error).
    *   **Menu:** "Open Nojoin", "Check for Updates", "Help", "About", "Restart", "Exit".
*   **Dual-Channel Capture:** Simultaneously records system output (what you hear) and microphone input (what you say).
*   **Pause & Resume:** Supports pausing via Web Client commands.
*   **Smart Uploads:** Audio is sent to the server automatically. If pauses occur, the audio is sent as multiple segments and concatenated on the server.
*   **Visual Feedback:** Tray icon changes color/shape based on status. Native system notifications for status changes.

### Portable Version
A portable version of the Companion App is available for users who cannot or prefer not to install software.
- **Windows:** `Nojoin-Companion-Portable.exe`
- **Linux:** `Nojoin-Companion-Portable.AppImage`

### Import Recordings
You can import existing audio files directly via the Web Client.

*   **Supported Formats:** WAV, MP3, M4A, AAC, WebM, OGG, FLAC, MP4, WMA, Opus.
*   **Metadata:** Specify a custom meeting name and the original recording date/time during import.
*   **Processing:** Imported files enter the same processing pipeline (Transcription -> Diarization -> Alignment) as live recordings.

### Transcription & Diarization
Audio processing happens asynchronously on the server.

*   **Process:**
    1.  **Validation:** Checks audio file integrity.
    2.  **Preprocessing:** Converts to mono 16kHz WAV, filters silence.
    3.  **Transcription:** Uses OpenAI Whisper (Local).
    4.  **Diarization:** Uses Pyannote (Local) for speaker separation.
    5.  **Alignment:** Merges transcript segments with speaker timestamps.
*   **Progress Tracking:** Real-time status updates in the Web Client (e.g., "Transcribing...", "Determining speakers...").
*   **Export:** Export to `.txt` format via the Web Client (Transcript Only, Notes Only, or Both).

### Speaker Management
*   **Global Speaker Library:** Centralized database of known speakers.
*   **Intelligent Linking:**
    *   Link "Unknown Speakers" to existing Global Speakers.
    *   Renaming a speaker offers to update the Global Speaker or create a new one.
*   **Voiceprint Management:**
    *   **Auto-Extraction:** Can be enabled in Settings.
    *   **On-Demand Creation:** Create voiceprints via the "Create Voiceprint" context menu.
    *   **Visual Indicator:** Speakers with voiceprints display a fingerprint icon.

### Meeting Intelligence
*   **LLM-Powered Notes:**
    *   Generates summaries, action items, and key takeaways.
    *   **Comprehensive Notes:** Includes Topics, Summary, Detailed Notes, Action Items.
*   **Chat Q&A:**
    *   "Chat with your meeting" feature to ask questions about specific recordings.
    *   Chat history is saved per recording.

### Search & Organization
*   **Tagging:** Apply tags to recordings (e.g., "Daily Standup").
*   **Advanced Search:** Full-text search across titles, notes, and transcripts. Filter by Date, Tags, and Speakers.
*   **Fuzzy Search:** Tolerates typos in search queries.

### Web Playback & Transcript Interface
*   **Dashboard:**
    *   **Left Sidebar:** List of recordings.
    *   **Center Panel:** Transcript and Meeting Notes.
    *   **Speaker Panel:** List of identified speakers.
    *   **Chat Sidebar:** "Chat with Meeting" functionality.
*   **Player:** HTML5 audio player with waveform visualization.
*   **Synced Transcript:** Clicking text seeks audio. Text highlights during playback.
*   **Edit Mode:** Correct transcript text and speaker assignments in the browser.

### Settings & Configuration
*   **Server Settings:** Manage API keys, model selection, and storage paths.
*   **User Preferences:** Theme selection (Dark/Light), default playback speed.
