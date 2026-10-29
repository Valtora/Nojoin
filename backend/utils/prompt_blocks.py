"""Composition helper for LLM prompts.

Prompts are composed from ``(heading, body)`` blocks with no substitution step,
so text is only ever concatenated. A ``str.format`` template puts three failure
modes on the note-generation path, all of which surface at generation time
rather than at import or save time:

* a stray ``{`` in interpolated text raises ``KeyError``/``IndexError``;
* JSON schema examples have to be written with doubled braces, which is easy to
  get wrong and unreadable when it is;
* adding a placeholder requires updating every call site, and a missed one only
  fails when that provider is next used.

Nothing in a body is special: a user-authored notes structure containing
braces, backslashes or Markdown is just text.
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence, Tuple

# (heading, body). A ``None`` heading emits the body on its own, which is how
# preamble text and already-headed fragments are carried.
PromptBlock = Tuple[Optional[str], Optional[str]]

RECORDING_NOTICE_PROCEDURE = (
    "Treat a recording consent notice and its answer as procedure, and leave them "
    "out of titles, summaries and minutes unless the meeting discusses them further."
)


def render_prompt_blocks(blocks: Iterable[PromptBlock]) -> str:
    """Join blocks into a prompt, skipping any whose body is empty.

    Headings are emitted verbatim, so callers own the ``#`` level. Blocks are
    separated by a blank line, and the result has no trailing whitespace.
    """
    rendered: list[str] = []

    for heading, body in blocks:
        text = (body or "").strip()
        if not text:
            continue
        rendered.append(f"{heading}\n{text}" if heading else text)

    return "\n\n".join(rendered)


def join_prompt_sections(sections: Sequence[str]) -> str:
    """Join already-rendered fragments with a blank line, dropping empties."""
    return "\n\n".join(section.strip() for section in sections if section.strip())
