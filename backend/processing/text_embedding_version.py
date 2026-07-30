"""Text (RAG) embedding model version.

Deliberately free of heavy imports, for the same reason as its voiceprint
counterpart in ``embedding_version``: the API image ships without fastembed or
torch, but the ORM models and the chat retrieval path -- both of which run in
the API process -- need to reason about which vectors are comparable.

Cosine distance is only meaningful between two vectors produced by the SAME
model, so every similarity search filters on ``TEXT_EMBEDDING_VERSION`` and a
model change is a rebuild, never a silent degradation.

  1 -- sentence-transformers/all-MiniLM-L6-v2, 384 dimensions. Truncated input
       at roughly 256 tokens, so anything past the first ~1,000 characters of a
       chunk never influenced whether that chunk was found.
  2 -- jinaai/jina-embeddings-v2-small-en, 512 dimensions, 8192-token context.
       A whole page or slide embeds intact, which is what lets a document page
       be a single retrieval unit instead of a window sliced across it.

Version 2 changes the vector width, so the migration that introduces it also
alters the pgvector column and purges the old rows: there is no arithmetic that
makes a 384-dimension vector comparable to a 512-dimension one.
"""

from __future__ import annotations

TEXT_EMBEDDING_VERSION = 2
LEGACY_TEXT_EMBEDDING_VERSION = 1

TEXT_EMBEDDING_MODEL = "jinaai/jina-embeddings-v2-small-en"
TEXT_EMBEDDING_DIMENSIONS = 512

# Documented context window of the model above, in tokens. Used to decide when
# a page has to be split into several chunks rather than embedded whole.
TEXT_EMBEDDING_MAX_TOKENS = 8192

# Characters per token, deliberately pessimistic (English prose runs nearer
# 4.0). Used only to decide whether to split, where over-splitting costs a
# little retrieval coherence and under-splitting silently truncates.
TEXT_EMBEDDING_CHARS_PER_TOKEN = 3.0

# The character budget a single chunk is allowed to occupy. Kept well inside
# the window so a page of dense tables cannot overrun it.
TEXT_EMBEDDING_MAX_CHUNK_CHARS = int(
    TEXT_EMBEDDING_MAX_TOKENS * TEXT_EMBEDDING_CHARS_PER_TOKEN * 0.8
)
