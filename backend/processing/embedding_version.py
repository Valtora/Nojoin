"""Voiceprint extraction method version.

Deliberately free of heavy imports. The API image ships without torch, so the
version constants cannot live in ``embedding_core`` -- importing that module
pulls in torch and pyannote and fails outright in the API process. Anything on a
request path that needs to reason about embedding versions imports from here.

The version is bumped whenever the extraction procedure changes in a way that
moves embeddings to a different region of the vector space. Cosine similarity is
only meaningful between two embeddings produced by the SAME version, so every
comparison site must check this before scoring.

  1 -- Inference(window="sliding"): the model saw 5s sub-windows and their raw
       outputs were mean-averaged, overlapped speech included.
  2 -- Inference(window="whole"): one pooled embedding per crop, overlapped
       speech extruded, unit-normalised before and after averaging.
"""

from __future__ import annotations

EMBEDDING_METHOD_VERSION = 2
LEGACY_EMBEDDING_METHOD_VERSION = 1
