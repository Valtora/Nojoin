# Release v0.5.7

## Features & Improvements

### Performance & Resource Management

- **VRAM Optimization:** Implemented model unloading for Whisper and Pyannote. AI models are now unloaded from GPU memory after processing tasks, reducing idle VRAM usage and improving system stability.

### Audio & Processing

- **High Fidelity Audio:** Fixed an issue preventing proxy audio from reaching the target bitrate. Audio is now correctly processed at **320kbps / 44.1kHz**, ensuring consistent high quality.

## Bug Fixes

- **Documentation:** Fixed markdown table rendering in the Deployment guide environment variables section.
