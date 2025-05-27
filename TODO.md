### Meeting List & Context
- Allow for the renaming of meetings via the Meeting Context Display.

- Speakers are listed but not separated by commas.

- Change default name suffix from 'Recording' to 'Meeting', for example: "Tuesday 27th May, Afternoon Meeting".

### Meeting Notes & Transcript
- In settings, allow toggling of meeting note auto save, otherwise it should be manual via a button or CTRL S.

- Have a toggle between Meeting Notes and Meeting Transcript.

- Meeting Notes display area background is now finally theme aware but the text is not. The meeting list card widget text colour also needs to be theme-aware and update on theme change.

- Meeting notes are not generated on first finish of transcription and diarization, particularly when speakers are merged or changed. - Investigate.

### Backend & Data Handling
- Enable parallel processing and recording of meetings.
- Check CUDA support, it looks like CPU is being used even when settings say CUDA.

### Meeting Chat
- Add datestamp alongside timestamp  