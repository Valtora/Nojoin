"""Composition helper for LLM prompts.

Prompts used to be ``str.format`` templates. That put three failure modes on the
note-generation path, all of which surfaced at generation time rather than at
import or save time:

* a stray ``{`` in interpolated text raised ``KeyError``/``IndexError``;
* JSON schema examples inside a template had to be written with doubled braces,
  which is easy to get wrong and unreadable when it is;
* adding a placeholder to a template silently required updating every call site,
  and a missed one only failed when that provider was next used.

Prompts are now composed from ``(heading, body)`` blocks with no substitution
step at all, so text is only ever concatenated. Nothing in a body is special,
which is the entire point: a user-authored notes structure containing braces,
backslashes or Markdown is just text.
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence, Tuple

# (heading, body). A ``None`` heading emits the body on its own, which is how
# preamble text and already-headed fragments are carried.
PromptBlock = Tuple[Optional[str], Optional[str]]


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
