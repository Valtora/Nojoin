feat: add model download step to Setup Wizard

- **Backend**: Refactored `preload_models.py` to support progress tracking via callbacks.
- **Backend**: Added `download_models_task` to Celery worker to handle background model downloads.
- **Backend**: Added `/system/download-models` and `/system/tasks/{task_id}` endpoints to trigger and monitor downloads.
- **Frontend**: Updated Setup Wizard to trigger model download after account creation.
- **Frontend**: Added progress bar UI to Setup Wizard to show download status (VAD, Whisper, Pyannote).
- **UX**: Users are now blocked from entering the app until models are ready, preventing initial runtime errors.
