"""The shipped catalogue and its independent substitution contracts stay usable."""

from __future__ import annotations

import json
import re
import runpy
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


def test_duplicate_prompt_id_names_both_declarations(tmp_path: Path) -> None:
    """Import a real catalogue with a conflicting code-owned declaration."""
    import backend.prompts

    source = Path(backend.prompts.__file__).read_text(encoding="utf-8")
    original = '("shared", "calendar_title recording_title", "title")'
    assert original in source
    duplicate = '("shared", "calendar_title recording_title notes_legend", "title")'
    module = tmp_path / "__init__.py"
    module.write_text(source.replace(original, duplicate, 1), encoding="utf-8")
    with pytest.raises(ValueError, match=r"shared\.notes_legend") as error:
        runpy.run_path(str(module))
    message = str(error.value)
    assert "first declaration _CONTRACTS[0] block 0" in message
    assert "second declaration _CONTRACTS[2] block 2" in message
    assert message.count("file='shared.toml', block='notes_legend'") == 2
    assert "frozenset()" in message
    assert "frozenset({'title'})" in message


def test_non_utf8_catalogue_names_id_and_file(tmp_path: Path) -> None:
    _write_catalogue(tmp_path)
    prompt_id = next(iter(REGISTRY))
    spec = REGISTRY[prompt_id]
    (tmp_path / spec.file).write_bytes(b"\xff")
    with pytest.raises(ValueError) as error:
        load_registry(tmp_path)
    assert f"{prompt_id}: cannot load {spec.file}:" in str(error.value)
    assert isinstance(error.value.__cause__, UnicodeDecodeError)


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
