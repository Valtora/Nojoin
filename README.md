# Nojoin

Nojoin is a Python based desktop application for recording meeting audio (system input/output), transcribing with Whisper, and diarizing with Pyannote to create speaker-attributed minutes. Once the meeting notes are generated you can then use AI to ask questions about the meeting using your own API key from Google, OpenAI, or Anthropic (other providers to be supported in future based on demand).

This project was created initially for personal use only but I wanted to offer it free of charge to others in case they also found it useful. I also wouldn't mind getting some feedback and help from other users. I built this project using Cursor mainly so as you can imagine the typical AI artifacts are all over the place so I appreciate the codebase could use some work.

I know there are similar free solutions out there which are all great and quirky in their own ways, Nojoin is no different. My goal was to have something relatively simple that can be deployed fairly quickly without too complicated of an initial, basic setup.

## Manual Setup (Release Package Coming Soon)

1.  **Prerequisites:**
    *   Python 3.11.9 (IMPORTANT: This version specifically because of some compatabiliy issues with PyTorch)
    *   `ffmpeg` installed and added to system PATH ([https://ffmpeg.org/](https://ffmpeg.org/))
    *   NVIDIA GPU with CUDA toolkit installed (Optional, for GPU acceleration, see notes below)

2.  **Clone the repository:**
    ```bash
    git clone https://github.com/Valtora/Nojoin
    ```

3.  **Create a virtual environment (Recommended):**
    ```bash
    py -m venv .venv
    # Activate the environment
	
    # Windows (PowerShell)
    .venv\Scripts\Activate

4.  **Install dependencies:**
     Install the correct torch, torchaudio, and torchvision. I've tested on Windows 11 amd64 architecture with CUDA 12.8 and Python 3.11.9. The current requirements.txt file does this but it's highlighted again here just in case.

     Intall the requirements
    ```bash
    pip install -r requirements.txt
    ```

## GPU Acceleration (CUDA Support)

Nojoin supports GPU acceleration for transcription and diarization using CUDA. To enable GPU support:

- You must have an NVIDIA GPU and the CUDA Toolkit version **12.8** installed.
- Compatible NVIDIA drivers must be installed.
- PyTorch must be installed with CUDA 12.8 support (see [PyTorch Get Started](https://pytorch.org/get-started/locally/)).
- CUDA availability is automatically detected by Nojoin. If available, you can select "cuda" as the processing device in the Settings dialog.
- If CUDA is not detected, only CPU processing will be available.

**Note:** CUDA 12.8 is the only supported version. Other versions may not work correctly. See this URL to troubleshoot if you have CUDA issues: https://saturncloud.io/blog/how-to-troubleshoot-pytorchs-torchcudaisavailable-returning-false-in-windows-10/
