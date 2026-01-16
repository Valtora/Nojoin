# Release v0.5.5

## Features & Improvements

### Enhanced Speaker Management

- **Split Local Speakers:** You can now split or unmerge _any_ speaker directly from the Speaker Panel, even if they haven't been added to your Global People Library yet. This makes correcting diarization errors much faster.
- **Smarter "Add to People":** The "Add to People" option is now intelligently hidden if a speaker already exists in your library (checking both explicit links and name matches) to prevent duplicates.
- **UI Polish:** We've cleaned up the Speaker Panel by removing the legacy "Add All Voiceprints" banner (extraction is now automatic!) and refining the context menus for a sleeker, icon-free look.

### UI/UX Refinements

- **Context Menu Consistency:** All context menus across the app have been standardized to a clean, text-only design with improved layering (Z-index) fixes.
- **Navigation Highlighting:** Fixed an issue where the sidebar navigation wouldn't stay highlighted when viewing a specific recording.

## Infrastructure & Workflow

### Unified Release Pipeline

- **Lock-step Versioning:** We've unified our release process. Every Server release (`vX.Y.Z`) now automatically produces a corresponding Companion App release with the exact same version number.
- **Automated Sync:** A new auto-sync system ensures version numbers are consistent across all configuration files (`package.json`, `tauri.conf.json`, `Cargo.toml`), eliminating manual errors.
