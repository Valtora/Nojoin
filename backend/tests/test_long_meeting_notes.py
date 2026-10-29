"""Notes for a meeting longer than the context window, written in parts.

The model here is a function that refuses any prompt over a byte limit, as
Ollama refuses one over num_ctx, and answers drafts and combining calls with
fixed text. The budget prices a byte at one token, so the window is a byte
count and every size below can be worked out from the prompts themselves.
"""

from __future__ import annotations

import re

import pytest

from backend.utils.context_window import ContextWindowExceededError
from backend.utils.long_meeting_notes import (
    PartsBudget,
    context_for_notes,
    generate_meeting_intelligence_in_parts,
    generate_notes_in_parts,
    reconcile_speaker_mappings,
)
from backend.utils.meeting_intelligence import AutomaticMeetingIntelligenceRequest
from backend.utils.meeting_notes import AttachedDocument, NotesPromptContext
from backend.utils.transcript_parts import split_transcript

WINDOW = 16_000
DRAFT = "## Summary\nDraft for {part}."
FINAL = "## Summary\nThe whole meeting."


def _transcript(lines: int, *, words: int = 40) -> str:
    return "\n".join(
        f"[{minute:02d}:00 - {minute:02d}:59] Ana: "
        + " ".join(f"m{minute}w{word}" for word in range(words))
        + "."
        for minute in range(lines)
    )


def _part_of(prompt: str) -> str:
    match = re.search(r"This is part (\d+) of (\d+)", prompt)
    assert match, "not a draft prompt"
    return match.group(1)


class _Model:
    """Refuses prompts over ``limit`` bytes; answers drafts and combining calls."""

    def __init__(self, *, limit: int = WINDOW, draft: str = DRAFT, refuse=()):
        self.limit = limit
        self.draft = draft
        self.refuse = list(refuse)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if len(prompt.encode()) > self.limit or (self.refuse and self.refuse.pop(0)):
            raise ContextWindowExceededError("refused", window=self.limit)
        if "# Transcript Part" in prompt:
            return self.draft.format(part=_part_of(prompt))
        if "this stretch of the meeting" in prompt:
            first = re.search(r"\(parts (\d+) to (\d+)", prompt)
            return f"## Summary\nStretch {first.group(1)}-{first.group(2)}."
        return FINAL

    def drafts(self) -> list[str]:
        return [p for p in self.prompts if "# Transcript Part" in p]

    def merges(self) -> list[str]:
        return [p for p in self.prompts if "# Notes of the Parts" in p]


def _budget(window: int = WINDOW) -> PartsBudget:
    return PartsBudget(window=window, tokens_per_byte=1.0, prose_tokens_per_byte=1.0)


def _context(documents: str | None = None):
    notes_context = NotesPromptContext(
        documents=[AttachedDocument(title="Deck", text=documents)]
        if documents
        else None
    )
    return context_for_notes("| SPEAKER_00 | Ana |", None, None, None, notes_context)


# --- Splitting -------------------------------------------------------------


def test_parts_are_cut_between_utterances_and_run_to_equal_shares():
    transcript = _transcript(30)
    parts = split_transcript(transcript, 4000)

    assert "\n".join(part.text for part in parts) == transcript
    assert all(part.size <= 4000 for part in parts)
    sizes = [part.size for part in parts]
    line = len(transcript.splitlines()[0].encode())
    assert max(sizes) - min(sizes) <= 2 * line


def test_a_transcript_that_would_fit_is_still_split_in_two():
    parts = split_transcript(_transcript(10), 1_000_000)

    assert len(parts) == 2


def test_each_part_knows_the_time_it_starts_and_ends():
    transcript = (
        "[01:05:10 - 01:05:20] Ana: First.\n"
        "[01:05:20 - 01:05:30] Bo: Second.\n"
        "[65:30 - 65:40] Ana: Third.\n"
        "[65:40 - 66:10] Bo: Fourth."
    )
    first, second = split_transcript(transcript, 1_000)

    assert (first.start, first.end) == ("01:05:10", "01:05:30")
    assert (second.start, second.end) == ("65:30", "66:10")


def test_a_note_stays_in_the_part_of_the_utterance_it_follows():
    lines = _transcript(10).splitlines()
    note = ("[Note — mira]: " + "This is the figure we argued about. " * 10).strip()
    # After the sixth utterance, where a cut by size alone would fall before it.
    lines.insert(6, note)
    parts = split_transcript("\n".join(lines), 1_000_000)

    assert len(parts) == 2
    assert parts[0].text.splitlines()[-2:] == [lines[5], note]
    assert parts[1].text.splitlines()[0] == lines[7]
    # The note carries no time, so the part still ends where its last line does.
    assert parts[0].end == "05:59"


def test_an_utterance_too_long_for_a_part_is_cut_between_sentences():
    sentences = " ".join(f"Sentence {n} has a few more words in it." for n in range(60))
    parts = split_transcript(f"[00:00 - 09:00] Ana: {sentences}", 600)

    assert len(parts) > 2
    for part in parts:
        assert part.size <= 600
        for line in part.text.splitlines():
            assert line.startswith("[00:00 - 09:00] Ana: Sentence ")
            assert line.endswith(".")


# --- Speaker names ---------------------------------------------------------


def test_names_the_parts_agree_on_are_kept_and_the_rest_are_disputed():
    names = reconcile_speaker_mappings(
        [
            {"SPEAKER_01": "Ana", "SPEAKER_02": "Bo"},
            {"SPEAKER_01": "Ana Lima", "SPEAKER_02": "Cy", "SPEAKER_04": "Speaker 4"},
            {"SPEAKER_02": "bo", "SPEAKER_03": "Dee", "SPEAKER_09": "Eve"},
        ],
        ["SPEAKER_01", "SPEAKER_02", "SPEAKER_03", "SPEAKER_04"],
    )

    # The fuller name wins, a placeholder is no name, and a label that was
    # never unresolved is ignored.
    assert names.agreed == {"SPEAKER_01": "Ana Lima", "SPEAKER_03": "Dee"}
    assert names.disputed == {"SPEAKER_02": ("Bo", "Cy")}


def test_a_disputed_label_takes_only_a_name_the_parts_gave():
    names = reconcile_speaker_mappings(
        [
            {"SPEAKER_01": "Ana", "SPEAKER_02": "Bo"},
            {"SPEAKER_01": "Cy", "SPEAKER_02": "Di"},
        ],
        ["SPEAKER_01", "SPEAKER_02"],
    )

    settled = names.settle({"SPEAKER_01": " cy ", "SPEAKER_02": "Someone Else"})

    assert settled == {"SPEAKER_01": "Cy"}
    # The list copied back is no choice: names are applied without review.
    assert names.settle({"SPEAKER_02": "Bo; Di"}) == {}


def test_a_copied_example_and_a_role_inside_a_name_are_read_sensibly():
    names = reconcile_speaker_mappings(
        [
            {"SPEAKER_00": "Rose Lindgren (Project Manager)"},
            {"SPEAKER_00": "Project Manager", "SPEAKER_01": "Person name or role"},
        ],
        ["SPEAKER_00", "SPEAKER_01"],
    )

    assert names.agreed == {"SPEAKER_00": "Rose Lindgren (Project Manager)"}
    assert names.disputed == {}


# --- Notes in parts --------------------------------------------------------


def test_each_part_is_drafted_and_the_drafts_combined_once():
    model = _Model()
    notes = generate_notes_in_parts(_transcript(60), _context(), model, _budget())

    drafts = model.drafts()
    assert len(drafts) >= 2
    assert [_part_of(p) for p in drafts] == [str(n) for n in range(1, len(drafts) + 1)]
    # Everything before the part's own instructions is identical, so a server
    # can reuse its cached prefix from one part to the next.
    prefixes = {p.split("# This Part")[0] for p in drafts}
    assert len(prefixes) == 1
    [merge] = model.merges()
    assert "the whole meeting" in merge
    for n in range(1, len(drafts) + 1):
        assert f"Draft for {n}." in merge
    assert notes == FINAL


def test_attached_documents_go_to_the_drafts_and_not_to_the_combining_call():
    model = _Model()
    generate_notes_in_parts(
        _transcript(60), _context(documents="QUARTERLY-DECK-TEXT"), model, _budget()
    )

    assert all("QUARTERLY-DECK-TEXT" in p for p in model.drafts())
    assert not any("QUARTERLY-DECK-TEXT" in p for p in model.merges())


def test_a_part_whose_draft_is_refused_is_drafted_as_two():
    model = _Model(refuse=[True])
    generate_notes_in_parts(_transcript(60), _context(), model, _budget())

    refused, *drafted = model.drafts()
    assert _part_of(refused) == "1"
    planned = int(re.search(r"part 1 of (\d+)", refused).group(1))
    assert len(drafted) == planned + 1
    assert f"of {planned + 1}" in drafted[-1]
    assert "Draft for 1." in model.merges()[-1]
    assert f"Draft for {planned + 1}." in model.merges()[-1]


def test_drafts_too_long_to_combine_at_once_are_combined_in_groups():
    long_draft = "## Summary\nDraft for {part}. " + "detail " * 200
    model = _Model(draft=long_draft)
    notes = generate_notes_in_parts(_transcript(150), _context(), model, _budget())

    merges = model.merges()
    assert any("this stretch of the meeting" in p for p in merges)
    assert "the whole meeting" in merges[-1]
    assert notes == FINAL


def test_a_context_that_leaves_too_little_room_is_refused_with_its_size():
    model = _Model()
    with pytest.raises(ContextWindowExceededError, match="too little room"):
        generate_notes_in_parts(
            _transcript(60), _context(documents="x " * 4000), model, _budget()
        )
    assert model.prompts == []


def test_a_combined_answer_that_runs_out_of_window_is_asked_for_at_half_length():
    class _Overrunning(_Model):
        def __call__(self, prompt):
            if "# Notes of the Parts" in prompt and len(self.merges()) == 0:
                self.prompts.append(prompt)
                raise ContextWindowExceededError(
                    "stopped", window=WINDOW, answer_incomplete=True
                )
            return super().__call__(prompt)

    model = _Overrunning()
    notes = generate_notes_in_parts(_transcript(60), _context(), model, _budget())

    first, second = model.merges()
    limit = int(re.search(r"Write at most ([\d,]+) words\. Where", first).group(1))
    assert f"Write at most {limit // 2:,} words. Where" in second
    assert notes == FINAL


def test_drafts_that_cannot_be_combined_within_the_window_are_an_error():
    model = _Model(draft="## Summary\n" + "detail " * 1300)
    with pytest.raises(ContextWindowExceededError, match="too long to combine"):
        generate_notes_in_parts(_transcript(60), _context(), model, _budget())


def test_drafts_are_asked_to_be_short_enough_to_combine_several_at_once():
    model = _Model()
    generate_notes_in_parts(_transcript(60), _context(), model, _budget())

    targets = {
        re.search(r"Write at most ([\d,]+) words, so", p).group(1)
        for p in model.drafts()
    }
    [words] = targets
    # Three drafts of that many words, at seven bytes a word, fit beside the
    # combining call's instructions and its answer.
    merge = model.merges()[-1]
    fixed = len(merge.split("# Notes of the Parts")[0].encode())
    assert 3 * int(words.replace(",", "")) * 7 <= WINDOW - WINDOW // 3 - fixed


def test_drafts_the_price_keeps_apart_are_still_tried_in_pairs():
    # Priced at three tokens a byte no two drafts share a call, but the model
    # takes them: Ollama, not the estimate, decides.
    budget = PartsBudget(window=WINDOW, tokens_per_byte=1.0, prose_tokens_per_byte=3.0)
    model = _Model(draft="## Summary\nDraft for {part}. " + "detail " * 150)
    notes = generate_notes_in_parts(_transcript(60), _context(), model, budget)

    assert any("this stretch of the meeting" in p for p in model.merges())
    assert notes == FINAL


# --- The automatic pipeline ------------------------------------------------


def _request(transcript: str) -> AutomaticMeetingIntelligenceRequest:
    return AutomaticMeetingIntelligenceRequest(
        resolved_transcript=transcript,
        unresolved_speakers=("SPEAKER_01", "SPEAKER_02"),
        user_notes="Bring the budget",
        prefer_short_titles=True,
    )


def _names(mapping: dict[str, str]) -> str:
    lines = "".join(f"{label}: {name}\n" for label, name in mapping.items())
    return f"<speaker_names>\n{lines}</speaker_names>"


class _TaggedModel(_Model):
    """Drafts name speakers, and the final call gives a title, in tagged lines."""

    def __init__(
        self, mappings, settle=None, *, title="<title>Budget Review</title>", **kwargs
    ):
        super().__init__(**kwargs)
        self.mappings = list(mappings)
        self.settle = settle or {}
        self.title = title

    def __call__(self, prompt: str) -> str:
        answer = super().__call__(prompt)
        if "# Transcript Part" in prompt:
            mapping = self.mappings.pop(0) if self.mappings else {}
            return f"{answer}\n\n{_names(mapping)}"
        if "# Title Rules" in prompt:
            notes = "## Summary\nSPEAKER_01 and SPEAKER_02 agreed."
            return (
                f"{self.title}\n<gist>The team reviewed the budget.</gist>\n"
                f"{notes}\n\n{_names(self.settle)}"
            )
        return answer


def test_the_automatic_pipeline_gets_agreed_names_a_title_and_notes():
    model = _TaggedModel(
        [
            {"SPEAKER_01": "Ana"},
            {"SPEAKER_01": "Ana Lima", "SPEAKER_02": "Bo"},
            {"SPEAKER_02": "Cy"},
        ]
    )
    result = generate_meeting_intelligence_in_parts(
        _request(_transcript(60)), model, _budget()
    )

    assert result.title == "Budget Review"
    assert result.gist == "The team reviewed the budget."
    assert result.speaker_mapping == {"SPEAKER_01": "Ana Lima"}
    # Names go in after combining; the disputed label stays generic.
    assert result.notes_markdown.startswith(
        "## Summary\nAna Lima and SPEAKER_02 agreed."
    )
    assert "<speaker_names>" not in result.notes_markdown
    drafts = model.drafts()
    # The user's notes section reaches every part, not only the first.
    assert all("Bring the budget" in p for p in drafts)
    assert all("exactly as the transcript does" in p for p in drafts)
    assert all("<speaker_names>" in p for p in drafts)
    # The drafts' name blocks do not reach the combining calls.
    assert not any("Ana Lima" in p for p in model.merges())
    assert "# Title Style" in model.merges()[-1]


def test_the_final_call_settles_a_disputed_name_from_the_parts_suggestions():
    model = _TaggedModel(
        [{"SPEAKER_02": "Bo"}, {"SPEAKER_02": "Cy"}],
        settle={"SPEAKER_02": "Cy", "SPEAKER_01": "Invented"},
    )
    result = generate_meeting_intelligence_in_parts(
        _request(_transcript(60)), model, _budget()
    )

    final = model.merges()[-1]
    assert "# Speaker Names To Settle" in final
    assert "- SPEAKER_02: Bo; Cy" in final
    # SPEAKER_01 was never disputed, so the final call cannot name it.
    assert result.speaker_mapping == {"SPEAKER_02": "Cy"}
    assert result.notes_markdown.startswith("## Summary\nSPEAKER_01 and Cy agreed.")


def test_a_self_introduction_names_its_speaker_whatever_the_drafts_said():
    transcript = "\n".join(
        [
            "[00:00:01 - 00:00:05] SPEAKER_01: Hello everyone, my name is Ana Lima.",
            "[00:00:05 - 00:00:09] SPEAKER_02: Thanks. I'm here for finance.",
        ]
        + _transcript(60).splitlines()
    )
    model = _TaggedModel([{"SPEAKER_01": "Bo", "SPEAKER_02": "Cy"}])
    result = generate_meeting_intelligence_in_parts(
        _request(transcript), model, _budget()
    )

    # SPEAKER_01's introduction beats the draft; SPEAKER_02 keeps the draft's
    # name, which no other part disputed.
    assert result.speaker_mapping == {"SPEAKER_01": "Ana Lima", "SPEAKER_02": "Cy"}
    assert "# Speaker Names To Settle" not in model.merges()[-1]


def test_loosely_written_names_and_an_untagged_title_are_still_read():
    class _Loose(_TaggedModel):
        def __call__(self, prompt: str) -> str:
            answer = super().__call__(prompt)
            if "# Transcript Part" in prompt:
                notes = answer.split("<speaker_names>")[0]
                return (
                    f"{notes}<speaker_names>\n- SPEAKER_01: **Ana Lima**\n"
                    "- SPEAKER_02: none\n</speaker_names>"
                )
            return answer

    model = _Loose([], title="# Budget Review")
    result = generate_meeting_intelligence_in_parts(
        _request(_transcript(60)), model, _budget()
    )

    assert result.title == "Budget Review"
    assert result.speaker_mapping == {"SPEAKER_01": "Ana Lima"}


def test_an_empty_draft_is_asked_for_again():
    model = _TaggedModel([])
    original = model.__call__
    answers = iter([""])

    def flaky(prompt):
        if "# Transcript Part" in prompt and "This is part 1 of" in prompt:
            nxt = next(answers, None)
            if nxt is not None:
                model.prompts.append(prompt)
                return nxt
        return original(prompt)

    result = generate_meeting_intelligence_in_parts(
        _request(_transcript(60)), flaky, _budget()
    )

    assert result.title == "Budget Review"
    assert sum("This is part 1 of" in p for p in model.drafts()) == 2
