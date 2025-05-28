### Meeting List & Context
- Allow for the renaming of meetings via the Meeting Context Display.

### Meeting Notes & Transcript
- In settings, allow toggling of meeting note auto save, otherwise it should be manual via a button or CTRL S.

- Have a toggle between Meeting Notes and Meeting Transcript.

- Meeting Notes display area background is now finally theme aware but the text is not. The meeting list card widget text colour also needs to be theme-aware and update on theme change.

### Backend & Data Handling
- Enable parallel processing and recording of meetings.

### Meeting Chat
- Add datestamp alongside timestamp  

### UI Styling & Refactoring
- Refactor UI styling for a single source of truth:
    - Goal: Centralize all QSS definitions and styling logic within `theme_utils.py`.
    - Actions:
        - Gradually eliminate inline `widget.setStyleSheet(...)` calls in UI files (e.g., `main_window.py`).
        - Transition specific styling logic (e.g., the `_set_settings_button_accent` method in `main_window.py`) into `theme_utils.py`. This might involve extending `THEME_PALETTE` and the `get_theme_qss` function or creating new focused QSS generation functions within `theme_utils.py`.
        - Utilize Qt object names (`widget.setObjectName(...)`) and dynamic properties (`widget.setProperty(...)`) extensively to allow specific widgets to be targeted by the centralized QSS in `theme_utils.py`.
        - For HTML content within widgets (e.g., `QTextEdit`), continue to use helper functions like `wrap_html_body` in `theme_utils.py` to inject theme-aware CSS, ensuring these functions also draw colors and font information from the central `THEME_PALETTE` and `FONT_HIERARCHY`.  