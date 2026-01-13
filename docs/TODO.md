# Nojoin To-Do List

## People Management

- When a Person's voiceprint is deleted, all their metadata is deleted alongside it. This is unexpected behavior.
- Voiceprints should be treated as an optional metadata item, not as a required one and if deleted, the Person should still exist with their other metadata intact. In essence, voiceprint is a wholly decoupled metadata item from other metadata items with respect to individual Persons.
- Separately, when deleting a voiceprint the built-in browser modal appears for the confirmation with the text "Are you sure you want to delete this voiceprint? Speaker recognition for this person will stop working until a new voiceprint is created." The modal should be replaced with a custom modal that has a more user-friendly interface.
