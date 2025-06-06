# How to Configure Nojoin

This guide explains how to configure Nojoin to suit your needs, from selecting a transcription model to setting up your AI provider.

While most settings can be changed easily through the application's **Settings** dialog, they are all stored in a `config.json` file. We recommend using the UI for all changes.

## ⚙️ Using the Settings Dialog

You can access the main configuration window by clicking the **Settings** button in the top control bar of the application.

### Transcription & Processing

*   **Whisper Model Size:** This determines the power of the transcription model. Smaller models are faster but less accurate, while larger models are more accurate but require more time and computational resources.
    *   *Options:* `tiny`, `base`, `small`, `medium`, `large`
*   **Processing Device:** This sets whether to use your computer's CPU or a compatible NVIDIA GPU (CUDA) for transcription and diarization.
    *   *CPU:* The default option, works on all computers.
    *   *CUDA:* Significantly faster, but requires a supported NVIDIA graphics card and the CUDA toolkit to be installed.

### Audio Devices

*   **Default Input Device:** Your microphone. This is the device that will capture your voice during a recording.
*   **Default Output Device (Loopback):** Your system's audio output (e.g., speakers or headphones). Nojoin captures this to record what other participants in a meeting are saying.

### LLM Provider (for Notes & Q&A)

This section configures the Large Language Model used for generating meeting notes and answering questions in the chat panel.

*   **LLM Provider:** Choose your preferred AI provider.
    *   *Options:* Google Gemini, OpenAI, Anthropic.
*   **API Key:** You must provide your own API key from your chosen provider. These features will not work without a valid key. The key is stored locally and securely on your machine.
*   **Model:** Select the specific model you wish to use from the chosen provider (e.g., `gemini-1.5-pro`, `gpt-4-turbo`, `claude-3-sonnet`).

### File & Directory Settings

*   **Recordings Directory:** The folder where your raw MP3 audio recordings will be saved.
*   **Transcripts Directory:** The folder where the generated `.json` transcript files will be stored.

### General Application Settings

*   **Theme:** Switch between a `dark` or `light` user interface theme.
*   **Notes Font Size:** Adjust the text size (`Small`, `Medium`, `Large`) in the Meeting Notes panel for better readability.
*   **Auto-Transcribe on Finish:** If checked, Nojoin will automatically start the transcription and diarization process as soon as a recording ends.

## Advanced Configuration (Editing `config.json`)

For advanced users, some settings can be changed by directly editing the `config.json` file.

**Warning:** Editing this file directly can cause issues if not done correctly. We recommend backing up the file before making changes.

*   **File Location:** The `config.json` file is located in the `nojoin` directory where the application's database and logs are stored.
*   **`min_meeting_length_seconds`:** This setting (default: `1`) prevents very short, likely accidental, recordings from being processed. You can increase this value if you only want longer meetings to be saved and processed.
*   **`advanced.log_verbosity`:** This controls the level of detail in the `nojoin.log` file. The default is `INFO`. For troubleshooting, you might be asked to change this to `DEBUG` to capture more detailed information when reporting an issue. 