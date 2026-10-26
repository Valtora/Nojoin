"""Offline compatibility policy for the native diarisation regression evidence."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


def check_nemotron_compatibility(root: Path) -> list[str]:
    """Require one dependency pin and matching asset, fixture and study records.

    This is a compatibility policy, not a security-advisory hold. Upgrades need
    regenerated golden/comparison evidence as well as a requirements change.
    """
    problems: list[str] = []
    try:
        requirements = (root / "requirements/worker.txt").read_text(encoding="utf-8")
        pins = re.findall(r"^transformers==([^\s;#]+)", requirements, re.MULTILINE)
        if len(pins) != 1:
            return ["nemotron: worker.txt must declare Transformers exactly once."]
        manifest = _read_object(root / "backend/processing/nemotron_assets.json")
        files = manifest.get("files", [])
        names = {"config.json", "processor_config.json", "model.safetensors"}
        if (
            manifest.get("schema_version") != 1
            or manifest.get("repository") != "nvidia/Nemotron-3-Diarization"
            or not re.fullmatch(r"[0-9a-f]{40}", str(manifest.get("revision", "")))
            or not isinstance(files, list)
            or len(files) != 3
            or {entry.get("filename") for entry in files} != names
            or any(
                type(entry.get("size")) is not int
                or entry["size"] <= 0
                or not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256", "")))
                for entry in files
            )
        ):
            return ["nemotron: immutable three-file asset manifest is incomplete."]
        version = manifest.get("transformers_version")
        if version != pins[0]:
            problems.append(
                "nemotron: Transformers pin disagrees with the asset manifest."
            )
        digest = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        for relative in (
            "backend/tests/fixtures/nemotron/manifest.json",
            "docs/evaluation/nemotron-710.json",
        ):
            record = _read_object(root / relative)
            if record.get("transformers_version") != version:
                problems.append(
                    f"nemotron: {relative} needs new Transformers evidence."
                )
            if record.get("asset_manifest_sha256") != digest:
                problems.append(
                    f"nemotron: {relative} needs evidence for the asset manifest."
                )
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        problems.append(f"nemotron: compatibility evidence is unreadable: {exc}")
    return problems
