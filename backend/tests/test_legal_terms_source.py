"""The terms of use come from one source (#1004).

The setup step and the notice both render ``frontend/src/lib/legalTerms.json``.
``scripts/validate_docs.py``, which CI runs on every pull request, fails when
docs/LEGAL.md says anything different. These tests pin what that check finds.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.validate_docs import LEGAL_DOC, LEGAL_TERMS, legal_terms_drift

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def legal_markdown() -> str:
    return LEGAL_DOC.read_text(encoding="utf-8")


@pytest.fixture
def terms() -> dict:
    return json.loads(LEGAL_TERMS.read_text(encoding="utf-8"))


def test_the_page_and_the_app_give_the_same_terms(legal_markdown, terms) -> None:
    assert legal_terms_drift(legal_markdown, terms) == []


@pytest.mark.parametrize(
    ("before", "after", "problem"),
    [
        ("sole responsibility", "responsibility", "Section 1's text differs"),
        ("diarisation, and similar", "diarization, etc.", "Section 2's text differs"),
        (
            "## 2. Data Privacy and Local Processing",
            "## 2. Data Protection",
            "Section 2 is headed",
        ),
        ("## 1. Compliance With Laws", "## 1. Laws", "Section 1 is headed"),
        (
            "By proceeding, you agree to these terms",
            "By continuing, you agree to these terms",
            "closing sentence",
        ),
        (
            "> As the owner, you are also responsible",
            "> As the owner, you are responsible",
            "ownerResponsibility sentence",
        ),
    ],
)
def test_a_change_on_the_page_alone_is_drift(
    legal_markdown: str, terms: dict, before: str, after: str, problem: str
) -> None:
    assert before in legal_markdown

    found = legal_terms_drift(legal_markdown.replace(before, after), terms)

    assert any(problem in entry for entry in found), found


def test_a_change_in_the_app_alone_is_drift(legal_markdown: str, terms: dict) -> None:
    terms["sections"][1]["blocks"][1]["list"].append("A promise the page never made.")

    assert legal_terms_drift(legal_markdown, terms) == [
        "Section 2's text differs between LEGAL.md and the app."
    ]


def test_a_missing_section_is_drift(legal_markdown: str, terms: dict) -> None:
    trimmed = legal_markdown.replace("## 2. Data Privacy and Local Processing", "")

    assert "LEGAL.md has no section 2" in " ".join(legal_terms_drift(trimmed, terms))


def test_heading_case_follows_each_surface(legal_markdown: str, terms: dict) -> None:
    """Docs headings are title case and the app's sentence case."""
    assert "## 1. Compliance With Laws" in legal_markdown
    assert terms["sections"][0]["heading"] == "Compliance with laws"
    assert legal_terms_drift(legal_markdown, terms) == []


def test_no_component_spells_out_the_terms_itself() -> None:
    """Every surface renders the JSON: none carries its own copy of the text."""
    fragment = "require the consent of all"
    holders = sorted(
        str(path.relative_to(REPO_ROOT))
        for path in (REPO_ROOT / "frontend" / "src").rglob("*")
        if path.suffix in {".ts", ".tsx"}
        and not path.name.endswith((".test.ts", ".test.tsx"))
        and fragment in path.read_text(encoding="utf-8")
    )

    assert holders == []
