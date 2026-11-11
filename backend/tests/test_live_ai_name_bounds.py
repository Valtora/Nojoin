"""What a live AI answer may offer as a person's name (#981).

A name of letters in any script, combining marks, spaces, hyphens, apostrophes
and full stops, up to the length the live rename route accepts. A role word on
its own is a role, but one that is also a surname passes beside a name.
"""

from __future__ import annotations

import pytest

from backend.utils.speaker_name_suggestions import (
    MAX_PERSONAL_NAME_LENGTH,
    is_personal_name,
)

NAMES = [
    "Alice Head",
    "Bob Clerk",
    "Carol Sales",
    "Dana Guest",
    "Erin Lead",
    "Frank Host",
    "Grace Speaker",
    "Olive Senior",
    "Alice Example",
    "Ana Maria de Souza",
    "Jean-Luc Example",
    "Siobhán O'Example",
    "D’Arcy Example",
    "J. R. Example",
    "A. Example",
    "Zoë Example",
    "李明",
    "محمد",
    "प्रिया",
    "Nguyễn Văn An",
    # Decomposed: e and a combining acute accent.
    "Rene\u0301 Example",
    # The marks that sit inside names in other scripts, written as escapes
    # where they are invisible or look like a hyphen or an apostrophe.
    "علی\u200cرضا",  # Persian, zero-width non-joiner
    "ジョン\u30fbスミス",  # Japanese katakana middle dot
    "ｼﾞｮﾝ\uff65ｽﾐｽ",  # its half-width form
    "山田\u3000太郎",  # Japanese, ideographic space
    "ג\u05f3ון",  # Hebrew geresh
    "בן\u05beגוריון",  # Hebrew maqaf
    "Gal\u00b7la Example",  # Catalan middle dot
    "Alice\u00a0Example",  # no-break space
    "Jean\u2013Luc Example",  # en dash
    "Ka\u2018iulani Example",  # okina typed as a left quotation mark
    "བསྟན\u0f0bའཛིན",  # Tibetan tsheg
    "A" * MAX_PERSONAL_NAME_LENGTH,
]

NOT_NAMES = [
    # A role word on its own, or a phrase that is plainly a role.
    "Guest",
    "Speaker",
    "Host",
    "Head",
    "Head of Sales",
    "The Guest",
    "Sales Lead",
    "Guest Speaker",
    "A Guest",
    "An Engineer",
    "Special Guest",
    "Co-host",
    "Project Lead",
    "Industrial Designer",
    "Software Architect",
    "Accountant",
    "the project lead",
    # Not letters, or not a name at all.
    "Speaker 2",
    "Alice2",
    "Alice (host)",
    "Alice, the host",
    "alice@example.com",
    "Alice & Bob",
    "Alice and Bob",
    "Alice\nExample",
    "Alice\tExample",
    "Alice\u202eExample",  # a bidi control
    "Alice_Example",
    "\U0001f600 Alice",
    "...",
    "' - '",
    "",
    "A" * (MAX_PERSONAL_NAME_LENGTH + 1),
]


@pytest.mark.parametrize("name", NAMES)
def test_a_persons_name_is_offered(name: str) -> None:
    assert is_personal_name(name)


@pytest.mark.parametrize("name", NOT_NAMES)
def test_anything_else_is_no_name(name: str) -> None:
    assert not is_personal_name(name)
