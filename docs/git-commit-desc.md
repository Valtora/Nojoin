# Git Commit Description

feat(ui): hide 'Add to People' if speaker exists

Refined the Speaker Management UX to hide the "Add to People" context menu option if the speaker is already present in the Global Library.

The duplication check logic now considers both:

1. Explicit linkage (global_speaker_id is present).
2. Implicit linkage by name match (speaker name matches an existing global speaker).

This prevents the accidental creation of duplicate global speakers.
