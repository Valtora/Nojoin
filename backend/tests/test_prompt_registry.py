"""The shipped catalogue and its independent substitution contracts stay usable."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from backend.prompts import REGISTRY, load_registry, render_prompt


def _write_catalogue(directory: Path) -> None:
    """Write valid synthetic blocks without copying any shipped prose."""
    files: dict[str, list[str]] = {}
    for spec in REGISTRY.values():
        text = " ".join(f"${{{name}}}" for name in spec.placeholders) or "Valid block"
        files.setdefault(spec.file, []).append(f"{spec.block} = {json.dumps(text)}")
    for name, lines in files.items():
        (directory / name).write_text("\n".join(lines), encoding="utf-8")


def _parameterised_id() -> str:
    return next(prompt_id for prompt_id, spec in REGISTRY.items() if spec.placeholders)


def test_shipped_registry_renders_from_other_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    load_registry.cache_clear()
    assert REGISTRY, "The catalogue must cover the application's prompts"
    templates = load_registry()
    assert templates.keys() == REGISTRY.keys()
    for prompt_id, spec in REGISTRY.items():
        values = {
            name: "{braces} \\ $ ${opaque} café\nUNKNOWN / Unidentified speaker"
            for name in spec.placeholders
        }
        assert render_prompt(prompt_id, **values).strip(), prompt_id


@pytest.mark.parametrize(
    "defect",
    ["unknown", "missing", "syntax", "empty", "type", "block", "file", "toml"],
)
def test_invalid_catalogue_fails_at_load_with_id(tmp_path: Path, defect: str) -> None:
    _write_catalogue(tmp_path)
    needs_placeholders = defect in ("unknown", "missing", "syntax")
    prompt_id = next(
        key
        for key, item in reversed(REGISTRY.items())
        if bool(item.placeholders) == needs_placeholders
    )
    spec = REGISTRY[prompt_id]
    valid = " ".join(f"${{{name}}}" for name in spec.placeholders)
    bad_text = {
        "unknown": valid + " ${undeclared}",
        "missing": "No required placeholders",
        "syntax": valid + " ${",
        "empty": " \n\t",
    }
    source = tmp_path / spec.file
    if defect == "file":
        source.unlink()
    elif defect == "toml":
        source.write_text("[broken", encoding="utf-8")
    else:
        replacement = "42" if defect == "type" else json.dumps(bad_text.get(defect))
        lines = source.read_text(encoding="utf-8").splitlines()
        lines = [line for line in lines if not line.startswith(f"{spec.block} = ")]
        if defect != "block":
            lines.append(f"{spec.block} = {replacement}")
        source.write_text("\n".join(lines), encoding="utf-8")
    if defect in ("file", "toml"):
        prompt_id = next(
            key for key, item in REGISTRY.items() if item.file == spec.file
        )
    with pytest.raises(ValueError, match=re.escape(prompt_id)):
        load_registry(tmp_path)


def test_substitution_does_not_interpret_replacement_text() -> None:
    prompt_id = _parameterised_id()
    value = "${undeclared} $bad $$ {braces} \\ café\nUNKNOWN / Unidentified speaker"
    values = {name: value for name in REGISTRY[prompt_id].placeholders}
    assert value in render_prompt(prompt_id, **values)


@pytest.mark.parametrize("defect", ["missing", "extra"])
def test_render_rejects_wrong_arguments(defect: str) -> None:
    prompt_id = _parameterised_id()
    values = {name: "sample" for name in REGISTRY[prompt_id].placeholders}
    if defect == "missing":
        values.pop(next(iter(values)))
    else:
        values["undeclared"] = "sample"
    with pytest.raises(ValueError, match=re.escape(prompt_id)):
        render_prompt(prompt_id, **values)
