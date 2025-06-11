# Nojoin

## Legal Disclaimer

**Important:** You are responsible for complying with all applicable laws in your jurisdiction regarding the recording of conversations. Many jurisdictions require the consent of all parties before a conversation can be recorded. By using Nojoin, you acknowledge that you will use this software in a lawful manner. The developers of Nojoin assume no liability for any unlawful use of this application.

## Quick Setup

### Windows
1. **Download the installer** from the [Releases page](https://github.com/Valtora/Nojoin/releases)
2. **Run the installer** - it will copy all files to your chosen directory
3. **Run the setup script** - after installation, run `setup_windows.bat` to install dependencies:
   - Installs Python 3.11.9 to your user directory if needed
   - Installs ffmpeg for audio processing to your user directory
   - Sets up a virtual environment
   - Detects and configures GPU acceleration (CUDA) if available
   - Installs all dependencies
   - Creates desktop shortcuts and launch scripts
4. **Launch Nojoin** by double-clicking the desktop shortcut or running `Start Nojoin.bat`

### macOS
For macOS, please follow the [Manual Setup](#manual-setup-advanced-users) instructions below. An automated installer for macOS will be available in a future release.


## ✨ Features

*   **System Audio Recording:** Simultaneously record what you hear (system output) and what you say (microphone input).
*   **Local-First Transcription:** Uses OpenAI's Whisper to generate accurate transcripts of your recordings, running entirely on your machine.
*   **Offline Speaker Diarization:** Automatically identifies who spoke when using Pyannote for offline speaker diarization. No cloud connection required for core processing.
*   **Comprehensive Speaker Management:** Relabel, merge, and manage speakers per recording. Build a global speaker library for consistent naming across all your meetings.
*   **LLM-Powered Insights:**
    *   Generate concise, actionable meeting notes and summaries.
    *   Ask questions about your meeting transcript in a chat-style Q&A interface.
    *   Supports OpenAI, Google Gemini, and Anthropic models (requires your own API key).
*   **Powerful Search and Organization:**
    *   Full-text search across all meeting transcripts.
    *   High-performance find and replace text within a single transcript or across all of them.
    *   Organize recordings with custom tags.
*   **Data Management & Backup:**
    *   Complete backup and restore system creating portable zip files with all your data.
    *   Database-first architecture for faster operations and reliable data storage.
    *   Non-destructive restore that merges with existing data.
*   **Automatic Updates:** Built-in update checking and installation system with user-configurable preferences.
*   **Full Control:** Manage recordings, view transcripts, and configure settings like transcription models and audio devices through a modern UI.

## Manual Setup (Advanced Users)

If you prefer manual installation or need custom configuration:

1.  **Prerequisites:**

    *   **Python 3.11.9:**
        This project requires Python 3.11.9 SPECIFICALLY due to PyTorch compatibility issues. You can download it directly from the [Python website](https://www.python.org/downloads/release/python-3119/). The typical caveats of running multiple Python versions/installations apply.

        Alternatively, you can install it using your system's package manager:

        *   **Windows (winget):**
            ```bash
            winget install Python.Python.3.11 --version 3.11.9
            ```
        *   **macOS (Homebrew):**
            ```bash
            brew install python@3.11
            ```
        *   **Linux (Debian/Ubuntu):**
            ```bash
            sudo apt update && sudo apt install python3.11 python3.11-venv
            ```

    *   **ffmpeg:**
        `ffmpeg` is required for audio processing and must be available in your system's PATH.

        *   **Windows (winget):**
            ```bash
            winget install ffmpeg
            ```
        *   **macOS (Homebrew):**
            ```bash
            brew install ffmpeg
            ```
        *   **Linux (Debian/Ubuntu):**
            ```bash
            sudo apt update && sudo apt install ffmpeg
            ```
    *   **Optional (for GPU Acceleration):**
        NVIDIA GPU with the [CUDA 12.8 Toolkit](https://developer.nvidia.com/cuda-12-8-1-download-archive) installed. See the "GPU Acceleration" section for more details.

2.  **Clone the repository:**
    ```bash
    git clone https://github.com/Valtora/Nojoin
    ```
    **Switch to the newly cloned directoy:**
    ```bash
    cd Nojoin
    ```

3.  **Create and activate a virtual environment:**
    It is highly recommended to use a virtual environment to manage project dependencies.
    **Create the virtual environment:**
    ```bash
    python -m venv .venv
    ```
    **Activate the environment:**
    *   **Windows (PowerShell/CMD):**
        ```bash
        .venv\Scripts\Activate
        ```
    *   **macOS/Linux (bash/zsh):**
        ```bash
        source .venv/bin/activate
        ```

4.  **Install Dependencies**

    The dependencies are listed in `requirements.txt`. However, the PyTorch dependency is highly system-specific (CPU/GPU, OS, architecture). It is crucial to install the correct PyTorch version for your machine *before* installing the other packages.

    *   **Step 1: Install PyTorch**
        Visit the [PyTorch website](https://pytorch.org/get-started/locally/) to determine the correct installation command for your system configuration. For example:

        *   **Windows/Linux (CPU-only):**
            ```bash
            pip install torch torchvision torchaudio
            ```
        *   **Windows/Linux (CUDA 12.8):**
            ```bash
            pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
            ```
        *   **macOS (ARM/M-series chip):**
            ```bash
            pip install torch torchvision torchaudio
            ```
    
    *   **Step 2: Install Remaining Dependencies**
        Once PyTorch is installed, you can install the rest of the required packages. The `requirements.txt` file is configured for a CUDA-enabled setup, but `pip` will use your manually installed PyTorch version and skip reinstalling it.

        ```bash
        pip install -r requirements.txt
        ```

5.  **Run Nojoin:**
     Make sure your virtual environment is active before running the application:
     ```bash
     python Nojoin.py
     ```

## GPU Acceleration (CUDA Support)

Nojoin supports GPU acceleration for transcription and diarization using CUDA. To enable GPU support:

- You must have an NVIDIA GPU and the CUDA Toolkit version [**12.8**](https://developer.nvidia.com/cuda-12-8-1-download-archive) installed.
- Compatible NVIDIA drivers must be installed.
- PyTorch must be installed with CUDA 12.8 support (see the installation steps above).
- CUDA availability is automatically detected by Nojoin. If available, you can select "cuda" as the processing device in the Settings dialog.
- If CUDA is not detected, only CPU processing will be available.

**Note:** CUDA 12.8 is the only supported version for now. Other versions may not work correctly. Saturn Cloud wrote a [helpful troubleshooting guide](https://saturncloud.io/blog/how-to-troubleshoot-pytorchs-torchcudaisavailable-returning-false-in-windows-10/) if you have CUDA issues.

## ☕ Buy Me a Coffee

If Nojoin is useful to you please consider [buying me a coffee](https://ko-fi.com/valtorra) as a way to support the project.
