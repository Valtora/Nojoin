# Release v0.5.6

## Features & Improvements

### Audio & Processing

- **High Fidelity Audio:** We've significantly increased the proxy audio bitrate from 64kbps to **320kbps**, ensuring crystal clear playback quality in the web player.
- **Smart Transcript Splitting:** The processing pipeline now utilizes word-level timestamps to split transcript segments intelligently, preventing words from being cut in half during segmentation.

### Companion App

- **Standardized Storage:** The Companion App now correctly stores configuration and logs in the system's standard `AppData/Roaming` directory on Windows, improving reliability and convention compliance.
- **Semantic Update Checks:** Implemented robust semantic versioning (`semver`) for update checks to ensure accurate comparison between installed and available versions.

### User Interface

- **Report a Bug:** A new section in **Settings > Help** provides a direct link to our GitHub Issues page, making it easier to report bugs and feedback.

## Bug Fixes

- **Context Menus:** Fixed Z-index layering issues where context menus and popovers would appear behind other elements.
- **Notes Navigation:** Resolved race conditions in the Notes View to ensure search matches rely on reliable scrolling behavior.
- **Transcript View:** Fixed index mismatch errors that could occur in the transcript display.

## Documentation

- Updated `README.md` with new screenshots and configuration details.
- Refactored code comments and addressed accumulated technical debt.
