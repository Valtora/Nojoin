- **Configuration Architecture**:
  - Decoupled System Config (`config.json`) from User Config (Database).
  - Refactored worker pipeline (`tasks.py`) to merge User settings with System defaults at runtime.
  - Updated processing modules (`transcribe.py`, `diarize.py`, `embedding.py`) to accept injected configuration, ensuring user-specific API keys are respected.
  - There is still more work to do here.

## Documentation
- **Developer Guide**:
  - Refactored `Nojoin-Development-Instructions.md` for clarity, removing verbose installation steps and adding a "First Run" guide.