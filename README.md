# Nojoin

Nojoin is a Python based desktop application for recording meeting audio (system input/output), transcribing with Whisper, and diarizing with Pyannote to create speaker-attributed minutes. Once the meeting notes are generated you can then use AI to ask questions about the meeting using your own API key from Google, OpenAI, or Anthropic (other providers to be supported in future based on demand). You can still view the full transcript without any AI processing if you don't have an API key.

## Setup

1.  **Prerequisites:**
    *   [Python 3.11.9](https://www.python.org/downloads/release/python-3119/) (IMPORTANT: This version specifically for now because of some compatabiliy issues with PyTorch)
    *   `ffmpeg` installed and added to system PATH, easily done via winget install in Terminal/Powershell: `winget install ffmpeg`
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

## GPU Acceleration (CUDA Support)

Nojoin supports GPU acceleration for transcription and diarization using CUDA. To enable GPU support:

- You must have an NVIDIA GPU and the CUDA Toolkit version [**12.8.x**](https://developer.nvidia.com/cuda-12-8-1-download-archive) installed.
- Compatible NVIDIA drivers must be installed.
- PyTorch must be installed with CUDA 12.8 support (see [PyTorch Get Started](https://pytorch.org/get-started/locally/)).
- CUDA availability is automatically detected by Nojoin. If available, you can select "cuda" as the processing device in the Settings dialog.
- If CUDA is not detected, only CPU processing will be available.

**Note:** CUDA 12.8.x is the only supported version for now. Other versions may not work correctly. Saturn Cloud wrote a [helpful troubleshooting guide](https://saturncloud.io/blog/how-to-troubleshoot-pytorchs-torchcudaisavailable-returning-false-in-windows-10/) if you have CUDA issues.