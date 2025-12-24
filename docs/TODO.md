# Nojoin To-Do List

After the colons I will provide a list of tasks/instructions that need to be completed. Now read completely (not just first 100 lines) the following files in the /docs directory to get an understanding of the project: AGENTS.md, DEPLOYMENT.md, PRD.md, and USAGE.md. Present a plan for approval before making any changes:

## Meeting Transcript and Notes Export Feature

- Improve the meeting transcript and notes export feature to allow users to export the notes and transcript as a single file in multiple formats. The user should be able to choose between PDF and DOCX. The notes should be formatted in the same way as the notes appear in the Notes view. The notes view is Markdown and the exported formats don't natively support it so some parsing, conversion and or formatting may be needed.
- In the exported files, the PDF, TXT, and DOCX etc. should all contain a section with the meeting title, date, begin and end time, and speaker list, like a formal meeting minute document would.

## Speaker Management and Meeting Chat Panel UI/UX Improvements

- Currently the speaker management and meeting chat panels are side by side.
- Typically there are not more than 4-5 speakers for any given meeting, this means that usually half of the vertical space is wasted in the speaker management panel.
- Therefore a better use of space might be to have the speaker management panel and the meeting chat panel stack vertically instead of horizontally, side-by-side.
- This would allow for a more compact and efficient use of space.
- At the same time, the header panel containing the meeting title, tags, and audio playback controls doesn't need to extend ALL the way across to the right of the page. Instead it should line up with the Transcript/Notes/Documents panel, freeing up space on the top right hand side of the page for the new vertically stacked speaker management and meeting chat panel to sit.
