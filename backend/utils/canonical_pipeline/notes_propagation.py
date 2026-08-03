"""Carrying a speaker rename into the meeting notes.

Renaming a speaker relabels the transcript, because the transcript stores a
diarisation label and resolves the display name at read time. The notes do not
work that way: they are prose an LLM wrote once, with the names of the moment
baked into the sentences. Left alone they keep calling someone by a name the
rest of the recording no longer uses.

The replacement is deliberately narrow, because editing generated prose by
string substitution is not safe in general:

* It matches on word boundaries, so renaming "Matt" does not maul "Matthew"
  or "matter".
* It only touches ``Transcript.notes``, the generated notes. ``user_notes`` is
  the user's own writing, and rewriting what a person wrote themselves is a
  different act from updating text a model produced.
* It skips a rename whose old name is short enough to collide by accident, and
  skips the no-op case where the name has not actually changed.

None of that makes it safe against a display name that is also an ordinary
phrase. A speaker called "NASA Communications" renamed to a person's name will
take every genuine mention of the organisation with it. That is a known and
accepted cost of substituting rather than regenerating: regeneration is the
only thing that can rewrite prose correctly, and it costs an inference call
and discards notes the user may be happy with.
"""

import re
from typing import Any

from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import select

from backend.models.transcript import Transcript

# Below this length a name is more likely to collide with ordinary words than
# to be a real reference, even on a word boundary. Initials and short handles
# lose their propagation rather than risk mangling the notes.
_MIN_REPLACEABLE_NAME = 3


def propagate_speaker_rename_to_notes(
    session: Any,
    *,
    recording_id: int,
    old_names: list[str | None],
    new_name: str,
) -> int:
    """Rewrite a renamed speaker's old name in the generated notes.

    Returns the number of replacements made, for the caller to record.
    """
    resolved_new_name = (new_name or "").strip()
    if not resolved_new_name:
        return 0

    candidates = {
        name.strip()
        for name in old_names
        if name
        and name.strip()
        and name.strip() != resolved_new_name
        and len(name.strip()) >= _MIN_REPLACEABLE_NAME
    }
    if not candidates:
        return 0

    transcript = session.execute(
        select(Transcript).where(Transcript.recording_id == recording_id)
    ).scalar_one_or_none()
    if transcript is None or not transcript.notes:
        return 0

    notes = transcript.notes
    replacements = 0
    # Longest first, so a rename from "Mark Clampin" does not get pre-empted by
    # a shorter candidate that is a prefix of it.
    for old_name in sorted(candidates, key=len, reverse=True):
        pattern = re.compile(rf"(?<!\w){re.escape(old_name)}(?!\w)")
        notes, count = pattern.subn(resolved_new_name, notes)
        replacements += count

    if replacements:
        transcript.notes = notes
        flag_modified(transcript, "notes")
        session.add(transcript)

    return replacements
