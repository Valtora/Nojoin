# Nojoin - Product Requirements Document (PRD)

## 1. Introduction

**Application Name:** Nojoin  
**Purpose:** Nojoin is a modern desktop application for Windows 11 that enables individuals to record system audio (input and output) during meetings, generate accurate transcripts using OpenAI Whisper, and perform fully offline speaker diarization with Pyannote. The application is designed for personal use, focusing on privacy, robust local processing, and actionable meeting minutes with speaker attribution.

---

## 2. Core Features

### 2.1 System Audio Recording
- Simultaneous recording of system input and output audio using `soundcard` (with fallback to default devices).
- MP3 format only, saved in a user-configurable directory (default: `recordings`).
- Human-friendly default recording names (e.g., 'Wednesday 30th April, Afternoon Meeting').
- Robust error handling and status reporting.
- UI controls for start/stop, elapsed time, and status indicator.

### 2.2 Transcription
- Transcription of audio recordings using OpenAI Whisper (local, no cloud upload).
- User-selectable model size (turbo, tiny, base, small, medium, large) with 'turbo' as the recommended default, and processing device (CPU/GPU).
- Progress reporting via UI callbacks.
- Raw transcript saved as JSON if enabled in settings.

### 2.3 Speaker Diarization
- Fully offline diarization using Pyannote with local models/config (no Hugging Face token required).
- Audio is preprocessed to mono, 16kHz WAV, VAD-processed (using Silero VAD), and converted back to MP3 for diarization.
- Short or ambiguous speaker segments (under 1 second) are filtered out to improve transcript clarity and reduce noise from overlapping speech.
- Diarization progress is reported in the UI by parsing subprocess output (tqdm).
- Clearest segment for each speaker is identified and stored for playback.
- Speaker snippet playback is clamped to 10 seconds.

### 2.4 Speaker Labeling & Management
- **Global Speaker Library:**
    - A persistent, global library of known speaker names to ensure consistency across recordings.
    - Managed via a dedicated "Global Speakers" dialog accessible from the main window's control bar.
    - Dialog allows adding, renaming, and deleting global speakers, with fuzzy search for easy lookup.
    - Deleting a global speaker unlinks them from meeting participants but does not delete the participants themselves.
- **Participant Dialog Enhancements:**
    - When renaming a speaker in the 'Manage Participants' dialog, their name is automatically added to the Global Speaker Library if it's not a generic placeholder (e.g., "SPEAKER_01").
    - If a new name matches an existing global speaker, the user is prompted to link them.
    - If a new name is unique, the user is prompted to add it to the global library and link.
    - Speaker name input fields in the 'Manage Participants' dialog feature QCompleter suggestions from the Global Speaker Library.
    - Visual cue (asterisk in window title) indicates unsaved changes.
    - Clearer visual indication (e.g., icon and tooltip) for speakers already linked to the Global Speaker Library.
    - Enhanced confirmation dialog when attempting to delete a speaker who is linked to a global entry, clarifying that only the local recording link is removed.
    - Corrected speaker merge logic to ensure subsequent merge operations are independent and do not carry over selections from previous merges within the same dialog session.
- Clearest segment per speaker is stored and playable from the UI.
- UI for relabeling, merging, and deleting speakers per recording (handled via a 'Manage Participants' dialog).
- Speaker relabel/merge/delete updates both the database (including `global_speaker_id` links) and diarized transcript.
- Speaker names/chips are displayed in the recordings list items for clarity.
- LLM-powered speaker name inference with robust fallback, integrated with the 'Manage Participants' dialog.
- Improved handling of overlapping speech segments in transcripts during speaker management operations.

### 2.5 Tagging System
- Fully normalized tag system (tags and recording_tags tables).
- Autocomplete, chip display, and robust UI editing for tags (via 'Edit Tags' dialog).
- Recordings can be filtered/searched by tags and speakers (partial, case-insensitive matching). (Tag filtering UI elements in main window to be confirmed).

### 2.6 Recording & Transcript Management
- List view of all recordings with metadata, tags, and speakers (now implemented as a modern list widget, not a table).
- View, delete, rename, and process (transcribe/diarize) recordings from the UI via list selection and context menus.
- "Manage Participants" option in context menu to open speaker labeling dialog.
- **Database-First Architecture:** All transcript content (raw and diarized) stored directly in SQLite database as the single source of truth.
- **Automatic Migration:** Existing file-based transcripts automatically migrated to database storage on startup.
- **Performance Optimized:** Database storage eliminates file I/O for transcript operations, enabling faster search, replace, and speaker management.
- Audio files remain on disk in configurable directories (never hardcoded in codebase).
- Deletion is always allowed, even during processing (with process cancellation and user warning).
- Stuck/orphaned processing states are surfaced on startup for user action.

### 2.7 Audio Playback
- Uses `just-playback` for robust playback (play, pause, stop, seek, true pause/resume).
- Dedicated playback controls and seeker slider in the UI.
- Snippet playback for speakers (max 10 seconds).

### 2.8 Settings & Data Management
- Modern, thematically consistent settings dialog (dark/light theme, orange accents).
- Configurable: Whisper model, processing device, directories, input/output devices, transcript save flags, API keys.
- **Backup & Restore System:** Integrated backup/restore functionality with progress tracking.
  - **Complete Backup:** Creates single zip file containing database and all audio files.
  - **Non-Destructive Restore:** Merges backup data with existing recordings without overwriting.
  - **Progress Tracking:** Real-time progress dialogs for backup and restore operations.
  - **Manifest Integrity:** Backup files include manifest for data validation and integrity checking.
- All settings are persisted in `config.json` and validated.

### 2.9 LLM-Powered Meeting Notes
- Meeting Notes panel generates concise, actionable summaries using LLM providers.
- API keys required (prompted on first run if missing).
- Notes generated with robust prompt, rendered as rich HTML/Markdown in the UI.
- Notes are editable and saved to the database.
- Toggle to show/hide the raw diarized transcript.

### 2.10 Search & Filter Bar
- Search bar above the meetings list enables real-time filtering of meetings.
- Searches across meeting name, notes, tags/labels, and participant names (including raw diarization labels).
- Supports partial matches, case-insensitive search, and typo-tolerant fuzzy matching (using `RapidFuzz`).
- UI: QLineEdit with a bordered container and a clear ('X') button, styled to match the current theme.
- Results are shown in the meetings list as filtered items, updating live as the user types.
- Fully integrated with the theming system and respects all accessibility and UX guidelines.

### 2.11 LLM-Powered Chat Q&A
- Chat panel allows users to ask questions about the selected meeting.
- Makes use of API calls to LLM providers with the meeting's diarized transcript and generated notes as context.
- Conversation history is maintained for follow-up questions within a session.
- UI shows user questions and LLM responses, with a typing indicator for LLM activity.
- Requires API keys to be configured.

### 2.12 Find and Replace
- Comprehensive find and replace functionality accessible via toolbar button (magnifying glass icon) or Ctrl+F keyboard shortcut.
- **Notepad++-inspired dialog** with grouped sections for Find/Replace fields, Search Options, and Search Scope.
- **Search Options:** Case-sensitive matching, whole word only matching.
- **Search Scope:** Current document (meeting notes or transcript) or all transcripts across all recordings.
- **Operations:** Find Next, Find All (with occurrence counting), Replace, and Replace All.
- **Formatting Preservation:** Uses QTextDocument.find() for precise replacements that maintain rich text formatting in meeting notes.
- **High-Performance Bulk Operations:** Replace All across all transcripts with database-optimized processing, progress tracking, user confirmation, and threaded operations.
- **Database Integration:** All transcript modifications work directly with database storage for instant updates and atomic transactions.
- **Theme Integration:** Fully theme-aware dialog that adapts to dark/light themes.
- **Auto-refresh:** Automatically refreshes current view after bulk operations to show changes.
- **Pre-population:** Automatically populates search field with selected text when dialog is opened.
- **Database Autosave:** All transcript and meeting notes changes are automatically saved to the database with immediate persistence.

### 2.13 Dynamic UI Scaling & Responsive Design
- **Adaptive UI Scaling:** Dynamic screen resolution detection with automatic scaling tier assignment based on display width.
- **Scaling Tiers:** Three predefined tiers - Compact (< 1400px), Standard (1400-1800px), and Comfortable (≥ 1800px).
- **Component-Specific Scaling:** Independent scaling factors for UI elements, spacing, and fonts with intelligent minimum value enforcement.
- **Responsive Layouts:** Automatic adjustment of minimum window and panel sizes based on detected screen resolution.
- **Compact Mode Optimizations:** Reduced button text, smaller controls, and streamlined layouts for narrow screens (< 1300px width).
- **Manual Override:** User-configurable manual scale factor (0.5-2.0x) with settings panel integration.
- **Real-Time Adaptation:** Dynamic scaling updates when display configuration changes or user modifies settings.
- **Settings Integration:** UI Scale Mode (Auto/Manual) and custom scale factor controls in the Settings dialog.
- **Graceful Degradation:** All functionality remains accessible even on very small screens while maintaining usability.
- **Production-Ready:** Singleton pattern implementation with caching, error handling, and comprehensive logging.

---

## 3. Technical Requirements

- **Language:** Python 3.11.9 (strictly required)
- **UI Framework:** PySide6
- **Transcription:** openai-whisper (local, model/device selection)
- **Diarization:** pyannote.audio (offline, local models/config)
- **VAD:** Silero VAD (for audio preprocessing before transcription/diarization)
- **Audio Recording:** soundcard (MP3 format via pydub/ffmpeg)
- **Audio Preprocessing:** pydub (mono, 16kHz conversion; MP3 input, WAV intermediate)
- **Processing:** All transcription/diarization is local (no cloud for audio/diarization)
- **Compute Backend:** CPU and GPU (CUDA via PyTorch) supported
- **Database:** SQLite (normalized schema for recordings, speakers)