#!/usr/bin/env python3
"""Recompute retained native/NeMo comparison tables without models or downloads."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RECORD = ROOT / "docs/evaluation/nemotron-710.json"
ARMS = ("nemo-original-argmax", "nemo-control-argmax", "hf-argmax", "hf-fp32-argmax")


def read_record(path: Path) -> dict[str, Any]:
    record = json.loads(path.read_text())
    names = [row["name"] for row in record["streams"]]
    if len(names) != 10 or len(set(names)) != 10:
        raise ValueError("Evaluation record must contain ten unique dev streams")
    if record["numerical_parity"] != "fails":
        raise ValueError("Retained native/NeMo numerical parity fails")
    for row in record["streams"]:
        if set(row["accuracy"]) != set(ARMS):
            raise ValueError(f"Incomplete comparator arms for {row['name']}")
    return record


def pool(rows: list[dict[str, Any]], arm: str) -> tuple[float, float]:
    parts = [row["accuracy"][arm] for row in rows]
    count = sum(part["reference_lines"] for part in parts)
    correct = sum(part["first_correct"] for part in parts)
    total = sum(part["der"]["total_s"] for part in parts)
    errors = sum(
        sum(part["der"][key] for key in ("confusion_s", "missed_s", "false_alarm_s"))
        for part in parts
    )
    if count <= 0 or total <= 0:
        raise ValueError("Comparison denominators must be positive")
    return 100 * correct / count, 100 * errors / total


def render(record: dict[str, Any]) -> str:
    lines = [
        "Numerical/output parity: FAILS. Anonymous mapped accuracy is a separate measure.",
        "",
        "| Dev stream | HF/NeMo frames | max Δp | p99 Δp | >0.5 agreement % |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in record["streams"]:
        parity = row["parity"]["hf_vs_new_control_fp32"]
        lines.append(
            f"| {row['name']} | {row['hf_frames']}/{row['nemo_frames']} | "
            f"{parity['max_abs']:.7f} | {parity['p99_abs']:.7f} | "
            f"{parity['threshold_agreement_pct']:.5f} |"
        )
    lines.extend(
        ["", "| Cohort | Comparator | First strict % | DER % |", "|---|---|---:|---:|"]
    )
    for group in ("AMI headset", "AMI far-field", "Lords", "London"):
        rows = [row for row in record["streams"] if row["group"] == group]
        for arm in ARMS:
            first, der = pool(rows, arm)
            lines.append(f"| {group} | {arm} | {first:.3f} | {der:.3f} |")
    return "\n".join(lines) + "\n"


def verify_supplied_sources(record: dict[str, Any], directory: Path) -> None:
    """Check explicit retained research files before a local reproduction audit."""
    for relative, expected in record["retained_sources"].items():
        path = directory / relative
        try:
            with path.open("rb") as source:
                actual = hashlib.file_digest(source, "sha256").hexdigest()
        except OSError as error:
            raise ValueError(
                f"Missing retained comparison input: {relative}"
            ) from error
        if actual != expected:
            raise ValueError(f"Retained comparison input changed: {relative}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--recorded-only",
        action="store_true",
        help="recompute tables from shipped counts",
    )
    parser.add_argument("--record", type=Path, default=DEFAULT_RECORD)
    parser.add_argument(
        "--retained-root",
        type=Path,
        help="explicit local hf-parity directory to verify",
    )
    args = parser.parse_args()
    if not args.recorded_only and args.retained_root is None:
        parser.error(
            "choose --recorded-only or supply --retained-root; no inputs are downloaded"
        )
    try:
        record = read_record(args.record)
        if args.retained_root is not None:
            verify_supplied_sources(record, args.retained_root)
        print(render(record), end="")
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"Nemotron evaluation failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
