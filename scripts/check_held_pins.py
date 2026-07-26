#!/usr/bin/env python3
"""Guard the deliberately held dependency pins and report when one can move.

Some pins sit below the newest release because a companion package has not
shipped a matching build, not because we chose to lag. A held pin keeps
attracting Dependabot security alerts that no upgrade can fix, so the hold has
to be re-justified every time the advisory's affected range is revised. The
policy, and the one-off dismissal procedure, are in
docs/DEVELOPMENT.md#held-pins-and-unfixable-advisories.

This script does the two things that keep the hold honest:

1. **Drift check (offline).** Every file that declares part of a matched stack
   must declare the same version. Nothing else enforces that today, so a bump
   applied to one requirements file and missed in another would install a
   mismatched, ABI-incompatible pair.
2. **Release check (network).** Ask PyPI whether the blocking package has
   published a version that lets the pin move, so the hold is revisited when it
   can actually be lifted rather than on every alert.

    python scripts/check_held_pins.py            # drift + release check
    python scripts/check_held_pins.py --offline  # drift check only
    python scripts/check_held_pins.py --json     # machine-readable report

Exit codes: 0 = consistent and still blocked, 1 = action available (a hold can
move) or drift found, 2 = a release check could not be completed.

The release check needs network access to pypi.org, so only the ``--offline``
half runs in scripts/check.py and CI. The networked half runs on a schedule
from .github/workflows/held-pin-watch.yml.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPI_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class Hold:
    """A pin held below the latest release by an upstream blocker."""

    package: str
    pinned: str
    blocker: str
    # First ``package`` version that resolves the advisory below. The hold is
    # only fully cleared when ``blocker`` reaches this version too, because the
    # two must be installed as a matched pair.
    clears_advisory_at: str
    advisory: str
    rationale: str
    # Files whose pins must all agree with ``pinned``. Independently numbered
    # companions (torchvision, torchcodec) are excluded: they track the stack
    # but do not share its version string.
    matched_packages: tuple[str, ...] = ()
    matched_files: tuple[str, ...] = ()
    # Docker base images whose tag must start with ``pinned``.
    matched_images: tuple[str, ...] = field(default_factory=tuple)


HOLDS: tuple[Hold, ...] = (
    Hold(
        package="torch",
        pinned="2.11.0",
        blocker="torchaudio",
        clears_advisory_at="2.13.0",
        advisory="GHSA-rrmf-rvhw-rf47",
        rationale=(
            "pyannote.audio and torch-audiomentations both require torchaudio, "
            "whose wheels are compiled against a matching torch minor. "
            "torchaudio has published nothing above 2.11.0, so torch cannot "
            "move without breaking the ABI pair."
        ),
        matched_packages=("torch", "torchaudio"),
        matched_files=(
            "requirements/test.txt",
            "requirements/local.txt",
        ),
        matched_images=("docker/Dockerfile.worker",),
    ),
)

# torch==2.11.0, torchaudio==2.11.0 --index-url https://...
PIN_RE_TEMPLATE = r"^{package}==([^\s;#]+)"
# FROM pytorch/pytorch:2.11.0-cuda12.6-cudnn9-runtime@sha256:...
IMAGE_TAG_RE = re.compile(r"^FROM\s+pytorch/pytorch:([^\s@]+)", re.MULTILINE)


def parse_version(raw: str) -> tuple[int, ...] | None:
    """Return a comparable tuple for a final release, or None otherwise.

    Pre-releases (rc/a/b/dev) are deliberately rejected: a hold must not be
    reported as liftable on the strength of a release candidate.
    """
    text = raw.strip().split("+", 1)[0]
    text = re.sub(r"\.post\d+$", "", text)
    if not re.fullmatch(r"\d+(\.\d+)*", text):
        return None
    return tuple(int(part) for part in text.split("."))


def require_version(raw: str) -> tuple[int, ...]:
    parsed = parse_version(raw)
    if parsed is None:
        raise ValueError(f"not a final release version: {raw!r}")
    return parsed


def latest_release(package: str) -> str:
    """Return the newest non-yanked final release of ``package`` on PyPI."""
    url = f"https://pypi.org/pypi/{package}/json"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=PYPI_TIMEOUT_SECONDS) as response:
        payload = json.load(response)

    candidates: list[tuple[tuple[int, ...], str]] = []
    for version, files in payload.get("releases", {}).items():
        parsed = parse_version(version)
        # Skip pre-releases, and releases with no installable file left: a
        # fully yanked or file-less version must not clear a hold.
        if parsed is None or not any(not f.get("yanked", False) for f in files):
            continue
        candidates.append((parsed, version))

    if not candidates:
        raise ValueError(f"no final releases found on PyPI for {package}")
    return max(candidates)[1]


def declared_pins(hold: Hold) -> list[tuple[str, int, str, str]]:
    """Return (file, line, package, version) for every matched pin on disk."""
    found: list[tuple[str, int, str, str]] = []

    def line_of(text: str, offset: int) -> int:
        return text.count("\n", 0, offset) + 1

    for relative in hold.matched_files:
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        for package in hold.matched_packages:
            pattern = re.compile(
                PIN_RE_TEMPLATE.format(package=re.escape(package)), re.MULTILINE
            )
            for match in pattern.finditer(text):
                found.append(
                    (relative, line_of(text, match.start()), package, match.group(1))
                )
    for relative in hold.matched_images:
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        for match in IMAGE_TAG_RE.finditer(text):
            found.append(
                (
                    relative,
                    line_of(text, match.start()),
                    "pytorch/pytorch",
                    match.group(1),
                )
            )
    return found


def check_drift(hold: Hold) -> list[str]:
    """Return a problem description per pin that disagrees with the hold."""
    problems: list[str] = []
    pins = declared_pins(hold)

    for package in hold.matched_packages:
        if not any(found_package == package for _, _, found_package, _ in pins):
            problems.append(
                f"{hold.package}: no {package}== pin found in "
                f"{', '.join(hold.matched_files)}; the drift check is not "
                f"covering what it claims to."
            )

    for relative, line, package, version in pins:
        # An image tag carries a suffix (2.11.0-cuda12.6-...), so compare the
        # leading version component rather than the whole string.
        actual = version.split("-", 1)[0] if package == "pytorch/pytorch" else version
        if actual != hold.pinned:
            problems.append(
                f"{hold.package}: {relative}:{line} declares {package} "
                f"{version}, but the recorded hold is {hold.pinned}. Either the "
                f"stack moved and HOLDS in {Path(__file__).name} is stale, or "
                f"the bump was applied inconsistently and the stack is now "
                f"mismatched."
            )
    return problems


def evaluate(hold: Hold) -> dict[str, object]:
    """Ask PyPI whether the blocker has released far enough to move the pin."""
    latest = latest_release(hold.blocker)
    latest_key = require_version(latest)
    pinned_key = require_version(hold.pinned)
    clears_key = require_version(hold.clears_advisory_at)

    if latest_key >= clears_key:
        status = "clear"
        note = (
            f"{hold.blocker} {latest} is available. Move the whole stack to "
            f"{latest}, drop the {hold.package} entry from "
            f".github/dependabot.yml, and close out {hold.advisory}."
        )
    elif latest_key > pinned_key:
        status = "advanced"
        note = (
            f"{hold.blocker} {latest} is available, so the stack can move off "
            f"{hold.pinned}, but {hold.advisory} is not resolved until "
            f"{hold.package} {hold.clears_advisory_at}. Worth taking; the "
            f"alert will persist."
        )
    else:
        status = "held"
        note = f"{hold.blocker} is still at {latest}. The hold stands."

    return {
        "package": hold.package,
        "pinned": hold.pinned,
        "blocker": hold.blocker,
        "blocker_latest": latest,
        "clears_advisory_at": hold.clears_advisory_at,
        "advisory": hold.advisory,
        "status": status,
        "note": note,
    }


def check_releases(
    holds: tuple[Hold, ...],
) -> tuple[list[dict[str, object]], list[str]]:
    """Evaluate every hold against PyPI, collecting lookup failures separately."""
    results: list[dict[str, object]] = []
    failures: list[str] = []
    for hold in holds:
        try:
            results.append(evaluate(hold))
        except (urllib.error.URLError, ValueError, json.JSONDecodeError) as error:
            failures.append(f"{hold.package} (via {hold.blocker}): {error}")
    return results, failures


def render_text(
    problems: list[str], results: list[dict[str, object]], failures: list[str]
) -> None:
    for problem in problems:
        print(f"DRIFT: {problem}")
    for result in results:
        status = str(result["status"]).upper()
        print(f"[{status:>8}] {result['package']}: {result['note']}")
    for failure in failures:
        print(f"ERROR: could not check {failure}")
    if not problems:
        print("Matched-stack pins agree with every recorded hold.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check held dependency pins.")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Run only the drift check; do not contact PyPI.",
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit the report as JSON on stdout."
    )
    args = parser.parse_args()

    problems = [problem for hold in HOLDS for problem in check_drift(hold)]
    results, failures = ((), []) if args.offline else check_releases(HOLDS)
    results = list(results)
    actionable = bool(problems or [r for r in results if r["status"] != "held"])

    if args.json:
        report = {
            "drift": problems,
            "holds": results,
            "errors": failures,
            "actionable": actionable,
        }
        print(json.dumps(report, indent=2))
    else:
        render_text(problems, results, failures)

    if actionable:
        return 1
    return 2 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
