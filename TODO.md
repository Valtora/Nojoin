### Pipeline Logic

* The application is printing the following error in the logs, struggling to find the path to diarised transcripts. The logic for placing and finding these transcripts should be reviewed for issues. Though an error is reported the application functions as intended:
    2025-05-30 10:39:14,292 - nojoin.db.database - ERROR - Diarized transcript path not found for recording ID 20250530095438.
    ERROR: Diarized transcript path not found for recording ID 20250530095438.

### Speaker Management

*   Improve overlapping speech management and speaker attribution in general. See:
        https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb
        and
        https://www.reddit.com/r/MachineLearning/comments/1ibzhsc/d_speaker_diarization_models_for_3_or_more/
        """
        The approach I’ve found best to cleanup the diarization (or replace pyannote entirely) is to generate speaker embeddings for each segment whisper generates, then group by matching the speaker embeddings.

        For segment in segments:
        Generate speaker embedding
        For known speakers:
        If match, add to array of segments for that speaker.
        Else create a new entry for a new speaker.

        I have found that to massively reduce the number of speakers found in an audio recording. Though if someone gets emotional or changes their speech significantly it still produces a bonus extra speaker. But far less than before.

        This approach might struggle with the overlapping part of the overlapping speakers. But it’ll do a lot better than pyannote and might give you a start in finding the overlaps before using another model to analyse them.
        """

        ALSO
        """
        I have a solution that works 95% of the time with some post-processing of the diarization with pandas. My goal is to have zero overlaps in the final dataframe.

            First distinguish between full overlaps (like the 2 first in your example) and partial overlaps (like your last example at the end of first segment)
            General: Delete all segments that are shorter than 0.5 seconds (mostly "hmm" and short "yes" while the other speaker is speaking)
            Full overlap: Delete all segments that are shorter than 1 seconds (mostly speaking too soon and not continuing before 1st speaker finishes)
            Full overlap: Longer segments. I divide the longer segment into two, the shorter segment intercepts it and overwrites.
            Partial overlaps: If segment is less than 2 seconds, and overlaps more than 0.6 seconds -> delete (unnecessary interruptions at the end of the sentence)
            Partial overlaps longer: I modify end of first segment to a new value (start of 2nd segment)
        """
* Speaker merging is currently not functioning as intended. The checkbox logic is not being handled correctly is my suspicion. There are unintended merges happening when speakers are merged.

### Meeting List & Context
- Allow for the renaming of meetings via the Meeting Context Display.

### UI Styling & Refactoring
- Refactor UI styling for a single source of truth:
    - Goal: Centralize all QSS definitions and styling logic within `theme_utils.py`.
    - Actions:
        - Gradually eliminate inline `widget.setStyleSheet(...)` calls in UI files (e.g., `main_window.py`).
        - Transition specific styling logic (e.g., the `_set_settings_button_accent` method in `main_window.py`) into `theme_utils.py`. This might involve extending `THEME_PALETTE` and the `get_theme_qss` function or creating new focused QSS generation functions within `theme_utils.py`.
        - Utilize Qt object names (`widget.setObjectName(...)`) and dynamic properties (`widget.setProperty(...)`) extensively to allow specific widgets to be targeted by the centralized QSS in `theme_utils.py`.
        - For HTML content within widgets (e.g., `QTextEdit`), continue to use helper functions like `wrap_html_body` in `theme_utils.py` to inject theme-aware CSS, ensuring these functions also draw colors and font information from the central `THEME_PALETTE` and `FONT_HIERARCHY`.  