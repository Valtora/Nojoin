# Meeting Notes Feature Implementation

## Summary
Implement a comprehensive Meeting Notes feature powered by LLM services that allows users to generate, view, and edit AI-powered meeting summaries alongside the transcript.

## Changes Made

### Backend
- Added `notes` field to Transcript model for storing generated meeting notes
- Created database migration for the notes column
- Enhanced LLM_Services.py with a comprehensive prompt for high-quality meeting notes generation
- Added new API endpoints:
  - GET /transcripts/{id}/notes - Retrieve meeting notes
  - PUT /transcripts/{id}/notes - Update meeting notes
  - POST /transcripts/{id}/notes/generate - Generate notes using LLM
  - POST /transcripts/{id}/notes/replace - Find/replace in notes (also updates transcript)
- Updated export endpoint to support content_type parameter (transcript, notes, or both)
- Modified find/replace endpoints to apply changes to both transcript and notes for consistency

### Frontend
- Created NotesView component with:
  - Markdown rendering support
  - Search and find/replace functionality
  - Generate notes button with loading state
  - Edit mode for manual modifications
- Created ExportModal component for selecting export type
- Added tab navigation to switch between Transcript and Notes panels
- Updated TranscriptView to support export modal integration
- Added notes-related API functions (getNotes, updateNotes, generateNotes, etc.)
- Implemented separate undo/redo history for notes

### Meeting Notes Prompt Template
The new prompt generates structured notes with:
- Topics Discussed
- Comprehensive Summary
- Detailed Notes per topic (key points, discussions, decisions, rationales, open questions)
- Action Items/Tasks with assignments
- Miscellaneous information
