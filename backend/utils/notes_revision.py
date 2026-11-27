"""A revision token for a meeting's notes, from their content.

Several people may edit a shared meeting's notes. Each save sends the
revision of the notes it started from; when the stored notes no longer match
it, someone else changed them meanwhile and the save is refused with a
conflict instead of silently overwriting their edit.

The token is a hash of the text, so it needs no column and every writer
(hand edits, generation, find and replace, speaker renames) moves it without
having to remember to. Two edits that arrive at the same text compare equal,
which is no conflict: there is nothing to lose.
"""

from __future__ import annotations

import hashlib
from typing import Optional

NOTES_CONFLICT_CODE = "notes_conflict"


def notes_revision(notes: Optional[str]) -> str:
    return hashlib.sha256((notes or "").encode("utf-8")).hexdigest()[:32]
