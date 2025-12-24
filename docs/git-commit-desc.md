# Git Commit Description - Use Conventional Commit Guidelines

feat(settings): add version tag and update check

- Implemented `GET /api/v1/version` to check for updates via GitHub (30m cache).
- Added `VersionTag` component to Settings header.
- Displays "Current" vs "Latest" versions side-by-side when updates are available.
- Added link to GitHub releases.

fix(ui): simplify tag display in active filters

- Removed hierarchical path (e.g., "Parent -> Child") from tag filter pills.
- Now displays only the active tag name for a cleaner look.
- Modified `Sidebar.tsx`.

docs(readme): add update instructions

- Added "Updating Nojoin" section to README.
- Included docker compose commands for updating and checking versions.
- Added optional prune instruction for cleaning up old images.

feat(frontend): unify search/replace and improve notes highlighting

- Implemented Global Search and Replace: Calls to "Replace All" now execute across both Transcript and Notes simultaneously.
- Fixed Notes Highlighting: Created custom `SearchExtension` (Tiptap) using ProseMirror Decorations to render persistent highlights (yellow/orange) independent of focus.
- Improved Notes Scrolling: Implemented DOM-based scrolling mechanism to reliably jump to matches without stealing focus from the search input.
- Refactored `NotesView` search logic to use node-based scanning for accurate positioning.
