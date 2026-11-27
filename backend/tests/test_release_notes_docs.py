"""Docs validation keeps the release notes in their two parts (#1017).

The GitHub release body, ``.github/release-notes-template.md``, stays short: it
keeps the placeholders release.yml fills and the link to the full notes, and
its hand-written part has a word limit. The full notes for the version in
``docs/VERSION`` are a page under ``docs/releases``, listed in its index, and
``next.md`` keeps the headings ``scripts/cut_release_notes.py`` defines.
``scripts/validate_docs.py``, which CI runs on every pull request, enforces
both. These tests pin what it finds.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from scripts.cut_release_notes import next_skeleton
from scripts.validate_docs import (
    RELEASE_BODY_MAX_WORDS,
    RELEASE_NOTES_FULL_LINK,
    RELEASE_NOTES_TEMPLATE,
    WHATS_NEW_MAX_BULLET_WORDS,
    WHATS_NEW_MAX_BULLETS,
    count_words,
    main,
    release_body_hand_written,
    release_body_problems,
    release_pages_problems,
    whats_new_bullets,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _body(
    *, before: str = "- Nothing to do before upgrading.", whats_new: str = ""
) -> str:
    """A release body in the template's shape."""
    return "\n".join(
        [
            "<!-- Maintainer: guidance the count leaves out. -->",
            "",
            "## Nojoin {{VERSION}}",
            "",
            "### Before you upgrade",
            "",
            before,
            "",
            "### What's new",
            "",
            whats_new or "- **Test connection** asks for a model.",
            "",
            "### Upgrade",
            "",
            "```bash",
            "docker compose pull",
            "```",
            "",
            f"The full notes are [here]({RELEASE_NOTES_FULL_LINK}).",
            "",
            "<details>",
            "<summary>Container images</summary>",
            "",
            "{{IMAGE_DIGESTS}}",
            "",
            "</details>",
            "",
            "<details>",
            "<summary>Commits</summary>",
            "",
            "{{CHANGELOG}}",
            "",
            "</details>",
            "",
        ]
    )


def _words(count: int) -> str:
    return " ".join(["word"] * count)


def test_the_committed_template_is_ready_to_publish() -> None:
    assert (
        release_body_problems(RELEASE_NOTES_TEMPLATE.read_text(encoding="utf-8")) == []
    )


def test_the_committed_pages_cover_docs_version() -> None:
    version = (REPO_ROOT / "docs" / "VERSION").read_text(encoding="utf-8").strip()
    assert release_pages_problems(version) == []


def test_the_whole_validator_passes_on_the_repository() -> None:
    assert main() == 0


def test_a_body_in_the_template_shape_passes() -> None:
    assert release_body_problems(_body()) == []


def test_the_count_leaves_out_comments_placeholders_code_and_folded_blocks() -> None:
    body = _body()
    folded = body.replace("{{IMAGE_DIGESTS}}", _words(500)).replace(
        "docker compose pull", _words(500)
    )
    commented = folded.replace("guidance the count leaves out", _words(500))

    assert count_words(release_body_hand_written(commented)) == count_words(
        release_body_hand_written(body)
    )
    assert "{{" not in release_body_hand_written(body)


def test_markers_and_punctuation_are_not_words() -> None:
    assert count_words("### What's new\n\n- **Test connection** works.\n\n-- ** ;") == 5


@pytest.mark.parametrize("extra", [RELEASE_BODY_MAX_WORDS, RELEASE_BODY_MAX_WORDS * 2])
def test_a_long_body_fails_with_the_limit_and_what_to_do(extra: int) -> None:
    body = _body(before=f"- {_words(extra)}")

    problems = release_body_problems(body)

    assert len(problems) == 1
    assert f"over the limit of {RELEASE_BODY_MAX_WORDS}" in problems[0]
    assert "docs/releases/<version>.md" in problems[0]


def test_a_body_at_the_limit_passes_and_one_word_over_fails() -> None:
    room = RELEASE_BODY_MAX_WORDS - count_words(
        release_body_hand_written(_body(before="-"))
    )
    assert release_body_problems(_body(before=f"- {_words(room)}")) == []
    assert release_body_problems(_body(before=f"- {_words(room + 1)}")) != []


def test_more_than_five_bullets_under_whats_new_fail() -> None:
    bullets = "\n".join(f"- Change {n}." for n in range(WHATS_NEW_MAX_BULLETS + 1))

    problems = release_body_problems(_body(whats_new=bullets))

    assert problems == [
        f"What's new has {WHATS_NEW_MAX_BULLETS + 1} bullets. Keep the "
        f"{WHATS_NEW_MAX_BULLETS} that matter most and leave the rest to the full notes."
    ]


def test_a_long_bullet_fails_and_names_it() -> None:
    long = f"- Alice Example {_words(WHATS_NEW_MAX_BULLET_WORDS - 1)}"
    fine = f"- Bob Example {_words(WHATS_NEW_MAX_BULLET_WORDS - 2)}"

    problems = release_body_problems(_body(whats_new=f"{fine}\n{long}"))

    assert len(problems) == 1
    assert problems[0].startswith("What's new bullet 2 (Alice Example word")
    assert f"has {WHATS_NEW_MAX_BULLET_WORDS + 1} words" in problems[0]


def test_a_bullet_counts_its_continuation_lines_and_nothing_after_it() -> None:
    section = "\n".join(
        [
            "- First line",
            "  carried on, and",
            "lazily carried on.",
            "",
            "A paragraph after the list",
            "is not a bullet.",
            "- Second.",
        ]
    )

    assert whats_new_bullets(_body(whats_new=section)) == [
        "- First line   carried on, and lazily carried on.",
        "- Second.",
    ]


def test_bullets_under_other_headings_are_not_whats_new() -> None:
    before = "\n".join(f"- Step {n}." for n in range(WHATS_NEW_MAX_BULLETS + 3))
    assert release_body_problems(_body(before=before)) == []


@pytest.mark.parametrize(
    "placeholder", ["{{VERSION}}", "{{IMAGE_DIGESTS}}", "{{CHANGELOG}}"]
)
def test_a_lost_placeholder_fails(placeholder: str) -> None:
    body = _body().replace(RELEASE_NOTES_FULL_LINK, "https://example.invalid/notes")
    body = body.replace(placeholder, "")
    # Named in a comment, it is still lost.
    body += f"\n<!-- Keep {placeholder} here. -->\n"

    problems = release_body_problems(body)

    assert any(f"It has lost {placeholder}" in problem for problem in problems)


def test_a_lost_full_notes_link_fails() -> None:
    body = _body().replace(RELEASE_NOTES_FULL_LINK, "https://example.invalid/notes")

    problems = release_body_problems(body)

    assert len(problems) == 1
    assert "lost the link to the full notes" in problems[0]
    assert RELEASE_NOTES_FULL_LINK in problems[0]


@pytest.fixture
def releases(tmp_path: Path) -> Path:
    """A copy of docs/releases at the repository's own layout."""
    shutil.copytree(REPO_ROOT / "docs" / "releases", tmp_path / "docs" / "releases")
    return tmp_path


def test_the_skeleton_keeps_the_fixed_headings(releases: Path) -> None:
    (releases / "docs/releases/next.md").write_text(next_skeleton(), encoding="utf-8")
    assert release_pages_problems("3.1.0", releases) == []


def test_a_version_without_a_page_fails_and_says_how_to_make_one(
    releases: Path,
) -> None:
    problems = release_pages_problems("9.9.0", releases)

    assert problems[0] == (
        "docs/VERSION reads 9.9.0, and docs/releases/9.9.0.md does not exist. Run "
        "`python3 scripts/cut_release_notes.py 9.9.0` in the version-bump pull request."
    )
    assert problems[1].startswith("docs/releases/README.md does not list 9.9.0.")


def test_a_page_missing_from_the_index_fails(releases: Path) -> None:
    index = releases / "docs/releases/README.md"
    index.write_text(
        index.read_text(encoding="utf-8").replace("- [3.1.0](3.1.0.md)", "- 3.1.0"),
        encoding="utf-8",
    )

    assert release_pages_problems("3.1.0", releases) == [
        "docs/releases/README.md does not list 3.1.0. Add `- [3.1.0](3.1.0.md), "
        "<release date>` at the top of its list, as scripts/cut_release_notes.py does."
    ]


def test_a_missing_next_page_fails_and_says_how_to_restore_it(releases: Path) -> None:
    (releases / "docs/releases/next.md").unlink()

    assert release_pages_problems("3.1.0", releases) == [
        "docs/releases/next.md is missing. Restore it with "
        "`python3 scripts/cut_release_notes.py --skeleton > docs/releases/next.md`."
    ]


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (
            lambda text: text.replace("### API and MCP\n", ""),
            "it has lost `### API and MCP`",
        ),
        (
            lambda text: text.replace("## Rollback\n", "## Rollback\n\n### Calendar\n"),
            "`### Calendar` is not one of its headings",
        ),
        (
            lambda text: text.replace("## Upgrade\n", "## Upgrade\n\n## Upgrade\n"),
            "`## Upgrade` appears 2 times",
        ),
        (
            lambda text: (
                text.replace("## Known issues", "## Placeholder")
                .replace("## Browser capture", "## Known issues")
                .replace("## Placeholder", "## Browser capture")
            ),
            "its headings are out of order",
        ),
    ],
)
def test_next_page_heading_changes_fail(releases: Path, change, problem: str) -> None:
    next_notes = releases / "docs/releases/next.md"
    next_notes.write_text(
        change(next_notes.read_text(encoding="utf-8")), encoding="utf-8"
    )

    problems = release_pages_problems("3.1.0", releases)

    assert len(problems) == 1
    assert problem in problems[0]
    assert problems[0].startswith(
        "docs/releases/next.md must keep the skeleton's headings"
    )


def test_headings_in_comments_and_code_do_not_count(releases: Path) -> None:
    next_notes = releases / "docs/releases/next.md"
    next_notes.write_text(
        next_notes.read_text(encoding="utf-8")
        + "\n<!--\n## Draft\n-->\n\n```text\n# not a heading\n```\n",
        encoding="utf-8",
    )
    assert release_pages_problems("3.1.0", releases) == []
