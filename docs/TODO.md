# Nojoin To-Do List

## Cross-Meeting Context

- Implement a way to 'cross-pollinate' context between meetings. Using tags to associate multiple meeting transcripts and notes in one chat would be a very powerful way for users to interact with their data. It would allow users to leverage multiple meetings across a topic in order to generate useful outputs.
- The existing tag system should be leveraged for this feature.
- This would likely require changes to the backend to support querying across multiple meetings based on tags, as well as frontend changes to allow users to select tags in the chat interface.
- We should also allow the user to upload documents on a per-meeting basis, and have those documents be included in the cross-meeting context when relevant. The documents should be stored or at least their directory path OR embeddings (should we vectorize them on upload for efficient retreieval? Perhaps use RAG?) stored in the database for retrieval during chat sessions.
