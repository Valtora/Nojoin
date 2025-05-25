### Playback & Audio Controls
- Implement a silent/quiet sound detection warning banner in the main window below the main controls bar if a meeting is started but no audio is being detected. Differentiate between no audio being received and no audio being sent. This doesn't need to be a LOUD warning or notification, as sometimes meetings will start quietly or have quiet periods. The banner shouldn't pop up for every quiet period but if no audio is detected for over 10 seconds then the banner should appear.


### UI Feedback & Dialogs
- Add a spinner progress dialog to indicate when meeting notes are being generated. Currently app looks like its hanging. See Meeting Notes dialog.
- Review the processing dialog as it still needs improvement.

### Meeting List & Context
- Add speaker tags to meeting list to see speakers at a glance from the meeting list.
- Meeting titles in Meeting List also need more vertical space.
- Allow for the renaming of meetings via the Meeting Context Display.

### Meeting Notes & Transcript
- In settings, allow toggling of meeting note auto save, otherwise it should be manual via a button or CTRL S.
- Add an undo button AND copy to clipboard button for meeting notes and meeting chat messages.
- In general, minimise control via the context menu because most people are stupid and don't realise they can right-click things. For example, have a toggle between Meeting Notes and Meeting Transcript.
- Update application nomenclature to move away from 'diarization' and just refer to it as transcription.
- Remove the 'Transcribe' button.
- Remove the 'no tags' placeholder. Just have 'Add Label', add '+'.

### Theming & Visual Consistency
- Background AND text colour of Meeting Notes is not theme-aware. In addition, the theme dictates colour of any new text so new text on old background is not visible.
- In light mode, the button hover accent colour is also white which blends with theme background. In addition the view transcript dialog is also NOT theme aware.

### Backend & Data Handling
- Remove the option to not save transcripts as they are required in the back-end.
- Enable parallel processing and recording of meetings.
- Allow minimum meeting time to be zero, i.e., record any length.