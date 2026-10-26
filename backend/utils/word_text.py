"""Text of a run of ASR words.

Kept apart from transcript_utils so that modules which only rebuild text, such
as unheard_words, do not import the merge.
"""

import re

_PUNCTUATION_ONLY_RE = re.compile(r"^[\.,!?;:%)\]\}]+$")
_CONTRACTION_SUFFIXES = {
    "'d",
    "'ll",
    "'m",
    "'re",
    "'s",
    "'ve",
    "n't",
    "’d",
    "’ll",
    "’m",
    "’re",
    "’s",
    "’ve",
}


def reconstruct_text_from_words(words: list[dict]) -> str:
    """A line's text from its words, spaced as the engines' tokens ask.

    A word that starts with whitespace keeps it. Otherwise a contraction
    suffix, or a token of punctuation alone, joins the word before it, and any
    other word gets one space. The text has no space at either end.
    """
    text_parts: list[str] = []
    previous_source = None
    for index, word in enumerate(words):
        token = str(word.get("word") or "")
        if not token:
            continue
        if index == 0:
            text_parts.append(token.lstrip())
            previous_source = word.get("source_id")
            continue
        source = word.get("source_id")
        if source and source == previous_source:
            text_parts.append(token)
            continue
        previous_source = source
        if token[:1].isspace():
            text_parts.append(token)
            continue

        stripped_token = token.strip()
        if not stripped_token:
            continue
        if stripped_token in _CONTRACTION_SUFFIXES or stripped_token.startswith(
            ("'", "’")
        ):
            text_parts.append(stripped_token)
            continue
        if _PUNCTUATION_ONLY_RE.match(stripped_token):
            text_parts.append(stripped_token)
            continue
        text_parts.append(f" {stripped_token}")

    return "".join(text_parts).strip()
