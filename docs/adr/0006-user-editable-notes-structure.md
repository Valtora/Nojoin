# ADR-0006: A protected/editable split for the meeting-notes prompt

- **Status:** Accepted
- **Date:** 2026-07-26
- **Deciders:** Valtora

## Context

A user asked to be able to edit the meeting-notes prompt ([#137](https://github.com/Valtora/Nojoin/issues/137)). The reasoning is sound and general: a technical architecture review, a user interview, an incident post-mortem and a marketing planning session need different sections and different extraction priorities, and one fixed structure cannot serve all of them. Without this, the alternatives are rewriting notes by hand, pasting transcripts into another tool, or maintaining a local source modification that has to be re-applied on every upgrade.

The obvious implementation — expose the prompt as a text field — is wrong here, and the reason is specific to this codebase rather than a general objection to editable prompts.

`NOTES_BODY_SPEC` is not merely the wording of a prompt. Four separate parts of the application depend on what it makes the model produce:

1. The unified automatic path parses a JSON object and asserts `notes_markdown` matches `^##\s+\S`.
2. `append_user_notes_section` appends its own `## User Notes` heading to the result.
3. `strip_leading_title_heading` removes a `#` title, because the UI renders the meeting title separately and a second one contradicts it.
4. The Markdown table rules are what make decisions and action items render as real tables in the TipTap editor rather than as broken pipe-delimited text.

Exposing the whole template hands the user all four contracts at once, and it recreates the maintenance problem the issue is complaining about: every future improvement to the shipped prompt stops reaching anyone who has customised.

Separately, every prompt was rendered through `str.format`. That put three failure modes on the note-generation path, all of which surfaced at generation time rather than at import or save time: a single `{` in interpolated text raised `KeyError`; JSON schema examples inside a template had to be written with doubled braces; and adding a placeholder silently required updating every call site, with a miss only failing when that provider was next used. Introducing user-authored text into such a template makes the first of those a routine occurrence rather than a latent bug.

## Decision

**The notes body is split into protected and editable parts, and only the structure is editable.** `build_notes_body_spec(sections)` assembles a preamble, the section structure, the table rules and the fidelity rules. Three of the four are fixed; the second is the user's. Both generation paths — the unified JSON prompt and the standalone regeneration prompt — assemble it the same way, so they cannot drift, and passing `None` produces a byte-identical prompt to the one sent before this feature existed.

**Prompts are composed, not templated.** Every prompt is now assembled from `(heading, body)` blocks by `backend.utils.prompt_blocks.render_prompt_blocks`, with no substitution step anywhere. Braces, backslashes and Markdown in a user-authored structure are simply characters, so no escaping exists to get wrong; JSON schema examples are written with real braces; and a prompt section cannot be left unfilled, because there is nothing to fill. The conversion was verified by rendering all nine prompts before and after and diffing: byte-identical, so no model behaviour changed with it.

A single `prompt_template` argument survives on the backend methods, redefined as a **pre-rendered prompt override** rather than a format template. Nothing in the application passes it; it is a test seam. Meeting Edge's cacheable prefix is now composed as its own string rather than found by searching the rendered prompt for a heading, which removes the failure where renaming that heading silently disabled prompt caching.

**There are no placeholders in the editable text; metadata is always injected instead.** (This is now a product decision rather than a safety one, since composition removed the mechanical hazard.) Recording title, date, duration and participants are supplied to every notes prompt in a protected block. A structure can say "open with the date and attendees" in prose and the facts are there. This avoids a second templating layer, a placeholder validator, and the class of support question that starts "why is my prompt showing `{date}` literally".

**The opening-heading contract is relaxed only for custom structures.** The `^##` assertion encoded the built-in structure's own first line, not a property of well-formed notes. `AutomaticMeetingIntelligenceResult.require_section_heading` is set `False` when a custom structure is in use; every other check still applies. A run that fails anyway fails closed, exactly as before, with the notes marked in error rather than silently regenerated with a different structure. The alternative — retrying with the built-in structure — produces notes the user did not ask for and hides a broken template indefinitely.

**Templates are two tiers in one table, and resolution always degrades.** Install templates (`user_id` NULL) are visible to everyone and writable by owner/admins; personal templates are private. Resolution runs explicit choice → user default → install default → built-in, and each tier falls through rather than failing when a template is missing or not visible. A deleted template must never stop a meeting's notes from being generated.

**Provenance is stored as an id *and* a text snapshot.** `transcripts.notes_template_id` is `ON DELETE SET NULL` and `transcripts.notes_template_sections` holds the structure verbatim. An id alone would eventually describe text that never ran, because templates can be edited after the fact.

**Forked templates are version-stamped.** `builtin_version` records which shipped structure a template was forked from, and `NULL` means written from scratch and therefore never stale. When `NOTES_SECTIONS_VERSION` moves ahead of a template's stamp, the UI offers a diff and a reset. Nothing is auto-updated: a user who pinned wording by not touching it keeps it.

**A structure can be generated from a plain-language brief.** The user describes the meetings they run; a worker task asks the configured provider for a `{name, description, sections}` proposal and the editor fills in with it for review. It is never saved automatically — a structure decides how every future meeting is written up, so it gets read first. The generator prompt spends most of its length on what *not* to produce, because a proposal that restates the protected rules wastes tokens and one that contradicts them is silently overridden. The proposal is validated by exactly the validators a hand-typed structure passes, so an unusable draft fails in the editor rather than on a real meeting. It runs on the worker, not in the request path, per the repo's no-inference-in-the-API rule; the browser polls a short-lived Redis job record.

**The glossary is a separate, protected input that reaches notes and Meeting Edge.** Install and personal lists are merged rather than replaced, with the personal definition winning on conflict, because a term list is additive by nature and losing the organisation's vocabulary to add one acronym would be a trap. It deliberately does **not** touch the transcript: two of the three ASR engines are onnx-asr and have no prompt or hotword hook, so a glossary that claimed to fix transcription would work for Whisper users only.

## Consequences

The output ceiling is handled asymmetrically, on purpose. Only Anthropic needs a number, because `max_tokens` is a required parameter of the Messages API; OpenAI, Gemini and Ollama are sent no output cap at all, so each model's own maximum applies and adding a shared ceiling would *lower* them. On OpenAI it would also add a new failure mode, since the reasoning models reject `max_tokens` in favour of `max_completion_tokens`. Anthropic walks a ladder of 128000, 64000, 8192, 4096 — 128k being the true maximum *output* on current Claude models, not the 1M context window — stepping down only on the specific "above this model's maximum" error, so a current model is never capped for the sake of an old one.

Reaching those values forced the note-producing Anthropic calls to stream. The SDK refuses any non-streaming request whose `max_tokens` implies a run over ten minutes — roughly 21,000 tokens — raising `ValueError` before the request is sent. Streaming with `get_final_message()` removes both that guard and the HTTP timeout it exists to prevent.

Truncation is made loud everywhere instead. Ollama already refused to save output cut short by its context window; Anthropic (`stop_reason == "max_tokens"`), OpenAI (`finish_reason == "length"`) and Gemini (`MAX_TOKENS`) now raise `TruncatedNotesError` rather than saving notes that end mid-sentence. Half-written notes are worse than no notes, because nothing distinguishes them from a short meeting.

Restore has to remap two id-carrying settings that are not foreign-key columns: `users.settings.notes_template_id` and the install config's `install_notes_template_id`. Left alone, a restored id would either dangle or — worse — match an unrelated template on the target installation and quietly change how notes are written. Both are remapped through the restore's id map and dropped when unmappable.

The install glossary and install default template are admin-only through the existing `INSTALL_WIDE_AI_SETTING_KEYS` mechanism, so a non-admin's attempt to set them is dropped rather than stored on their user row.

What this does not solve: a user can still write a structure that produces poor notes. Validation rejects only what is mechanically broken — empty, oversized, control characters, no Markdown heading at all — because a validator that guessed at note quality would block the workflows the feature exists to enable.
