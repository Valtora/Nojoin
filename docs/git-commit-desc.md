# Git Commit Description

feat(backend): improve speaker detection accuracy

- Increased voiceprint sampling from top 3 to top 10 segments.
- Filtered out short segments (< 0.5s) from voiceprint extraction.
- Increased base similarity threshold for matching from 0.65 to 0.75.
- Implemented a margin of victory (0.05) to reject ambiguous voice matches.
- Consolidated `find_matching_global_speaker` to remove duplication.

fix(frontend): show "no audio" state for demo meeting

- Added condition in `AudioPlayer` to explicitly check for the demo
  meeting ("Welcome to Nojoin").
- Bypasses the "audio processing" loader state so the player correctly
  displays the "imported with no audio" disabled state for the
  demo recording.
