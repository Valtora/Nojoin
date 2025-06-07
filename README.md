# Nojoin

Nojoin is a Python based desktop application for recording meeting audio (system input/output), transcribing with Whisper, and diarizing with Pyannote to create speaker-attributed minutes. Once the meeting notes are generated you can then use AI to ask questions about the meeting using your own API key from Google, OpenAI, or Anthropic (other providers to be supported in future based on demand). You can still view the full transcript without any AI processing if you don't have an API key.

This project was created initially for personal use only but I wanted to offer it free of charge to others in case they also found it useful. I also wouldn't mind getting some feedback and help from other users. I built this project using Cursor mainly so as you can imagine the typical AI artifacts are all over the place so I appreciate the codebase could use some work.

I know there are similar free solutions out there which are all great and quirky in their own ways, Nojoin is no different. My goal was to have something relatively simple that can be deployed fairly quickly without too complicated of an initial, basic setup.

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
    *   Find and replace text within a single transcript or across all of them.
    *   Organize recordings with custom tags.
*   **Full Control:** Manage recordings, view transcripts, and configure settings like transcription models and audio devices through a modern UI.

## Legal Disclaimer

**Important:** You are responsible for complying with all applicable laws in your jurisdiction regarding the recording of conversations. Many jurisdictions require the consent of all parties before a conversation can be recorded. By using Nojoin, you acknowledge that you will use this software in a lawful manner. The developers of Nojoin assume no liability for any unlawful use of this application.

## Setup

1.  **Prerequisites:**
    *   [Python 3.11.9](https://www.python.org/downloads/release/python-3119/) (IMPORTANT: This version specifically for now because of some compatabiliy issues with PyTorch) You can also install via winget which should grab 3.11.9:
        ```
        winget install Python.Python.3.11
        ```
    *   FFMpeg installed and added to system PATH, easily done via winget install in Terminal/Powershell:
        ```
        winget install ffmpeg
        ```
    *   OPTIONAL but recommended: NVIDIA GPU with CUDA [**12.8.x**](https://developer.nvidia.com/cuda-12-8-1-download-archive) toolkit installed for GPU acceleration, see notes at the end.

2.  **Clone the repository:**
    ```bash
    git clone https://github.com/Valtora/Nojoin
    ```

3.  **Create a virtual environment (Recommended):**
    ```bash
    # Create virtual environment '.venv'
    py -m venv .venv
    
    # Activate the environment

    # Windows (Terminal/PowerShell)
    .venv\Scripts\Activate

4.  **Install dependencies:**
     
     Install the correct torch, torchaudio, and torchvision for your system. I've tested on Windows 11 amd64 architecture with CUDA 12.8.x and Python 3.11.9. I've also tested without CUDA and it should work fine, albeit with inferior performance in terms of transcription and diarization times.
     
     The current requirements.txt file assumes you have an NVIDIA GPU and attempts to install a suitable whl for CUDA 12.8. If you do not then install a suitable version of torch, torchaudio, and torchvision separately in your venv. See [PyTorch Get Started](https://pytorch.org/get-started/locally/) for the correct pip install command for your setup.

     Intall the requirements
    ```bash
    pip install -r requirements.txt
    ```

5.  **Run Nojoin:**
     Run while your virtual environment is active:
     ```bash
     python nojoin.py
     ```

## GPU Acceleration (CUDA Support)

Nojoin supports GPU acceleration for transcription and diarization using CUDA. To enable GPU support:

- You must have an NVIDIA GPU and the CUDA Toolkit version [**12.8.x**](https://developer.nvidia.com/cuda-12-8-1-download-archive) installed.
- Compatible NVIDIA drivers must be installed.
- PyTorch must be installed with CUDA 12.8 support (see [PyTorch Get Started](https://pytorch.org/get-started/locally/)).
- CUDA availability is automatically detected by Nojoin. If available, you can select "cuda" as the processing device in the Settings dialog.
- If CUDA is not detected, only CPU processing will be available.

**Note:** CUDA 12.8.x is the only supported version for now. Other versions may not work correctly. Saturn Cloud wrote a [helpful troubleshooting guide](https://saturncloud.io/blog/how-to-troubleshoot-pytorchs-torchcudaisavailable-returning-false-in-windows-10/) if you have CUDA issues.

## ☕ Buy Me a Coffee

If Nojoin is useful to you please consider [buying me a coffee](https://ko-fi.com/valtorra) as a way to support the project.