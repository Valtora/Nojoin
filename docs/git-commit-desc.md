# v0.2.5

feat(security): implement input validation and sanitization

- Added `validate_config_value` to `ConfigManager` in backend to enforce strict setting values.
- Integrated validation into `update_settings` API endpoints.
- Created `Tooltip` component in frontend for better user guidance.
- Implemented `validateSettings` in `SettingsPage` to block invalid inputs before saving.
- Added tooltips to AI Settings for Provider, Model, and API Key fields.
- Added unit tests for backend configuration validation.
