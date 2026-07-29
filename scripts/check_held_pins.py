#!/usr/bin/env python3
"""Guard the deliberately held dependency pins and report when one can move.

Some pins sit below the newest release because a companion package has not
shipped a matching build, not because we chose to lag. A held pin keeps
attracting Dependabot security alerts that no upgrade can fix, so the hold has
to be re-justified every time the advisory's affected range is revised. The
policy, and the one-off dismissal procedure, are in
docs/DEVELOPMENT.md#held-pins-and-unfixable-advisories.

This script does the three things that keep the hold honest:

1. **Drift check (offline).** Every file that declares part of a matched stack
   must declare the same version. Nothing else enforces that today, so a bump
   applied to one requirements file and missed in another would install a
   mismatched, ABI-incompatible pair.
2. **Interpreter check (offline).** Every surface that runs ``backend/`` must
   declare the same Python minor. This is a consequence of the torch hold rather
   than a separate decision: the worker inherits its interpreter from the held
   PyTorch base image, so the API image, CI, and mypy have to follow it. Left
   unchecked, Dependabot walked the API base image from 3.12 to 3.14 on its own
   and made the interpreter serving every HTTP request the only one the test
   suite never ran on.
3. **Release check (network).** Ask PyPI whether the blocking package has
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


@dataclass(frozen=True)
class Declaration:
    """A file that states the Python version, and how to read it back out."""

    path: str
    pattern: re.Pattern[str]
    # What the declaration governs, for the failure message.
    what: str
    # How many matches the file must contain, when that count is itself load
    # bearing. Dockerfile.api names the interpreter once per build stage, and is
    # only aligned if *both* moved: a half-applied bump builds the venv on one
    # interpreter and runs it on another, which can still appear to work.
    expected_matches: int | None = None


# The Python minor that every surface running backend/ must agree on.
#
# This is derived, not chosen. The worker's interpreter comes from the PyTorch
# base image, which is held at 2.11.x because torchaudio has published nothing
# above it (see the torch Hold above), and that image ships Python 3.12. The
# worker's Python is therefore fixed until the torch hold lifts, and everything
# else has to match it rather than the other way round.
#
# The worker image is the source of truth but cannot be checked statically: the
# pytorch/pytorch tag encodes the torch version, not the Python one. So this
# constant records what that base ships, verified by running `python -V` in the
# built image, and the declarations below are checked against it. Moving it is a
# deliberate act that should come with the same verification.
EXPECTED_PYTHON = "3.12"

PYTHON_DECLARATIONS: tuple[Declaration, ...] = (
    Declaration(
        path="docker/Dockerfile.api",
        # FROM python:3.12-slim@sha256:...
        pattern=re.compile(r"^FROM\s+python:(\d+\.\d+)", re.MULTILINE),
        what="the API image base",
        expected_matches=2,
    ),
    Declaration(
        path="pyproject.toml",
        # python_version = "3.12"  (mypy's target)
        pattern=re.compile(r"^python_version\s*=\s*\"([^\"]+)\"", re.MULTILINE),
        what="the mypy target",
    ),
    Declaration(
        # Globbed rather than listed, so a new workflow is covered on the day it
        # lands instead of whenever someone remembers to add it here. Both
        # extensions, so a .yaml file does not escape the check.
        path=".github/workflows/*.y*ml",
        # python-version: "3.12"
        pattern=re.compile(r"python-version:\s*\"([^\"]+)\"", re.MULTILINE),
        what="the CI interpreter",
    ),
    Declaration(
        path="CONTRIBUTING.md",
        # - Python 3.12   (the contributor prerequisite bullet)
        pattern=re.compile(r"^-\s+Python\s+(\d+\.\d+)", re.MULTILINE),
        what="the documented prerequisite",
    ),
    Declaration(
        path="docs/DEVELOPMENT.md",
        pattern=re.compile(r"^-\s+Python\s+(\d+\.\d+)", re.MULTILINE),
        what="the documented prerequisite",
    ),
)


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


def minor_of(raw: str) -> str:
    """Reduce a version to its ``X.Y`` minor, so 3.12.13 and 3.12 compare equal.

    Patch releases are free to differ: the API image and the worker will not ship
    the same 3.12.z, and that is fine. The minor is what changes behaviour.
    """
    parts = raw.strip().split(".")
    return ".".join(parts[:2])


def resolve_paths(pattern: str) -> list[Path]:
    """Expand a declaration path, which may be a glob, to concrete files."""
    if "*" in pattern:
        return sorted(REPO_ROOT.glob(pattern))
    return [REPO_ROOT / pattern]


def check_one_file(
    declaration: Declaration, path: Path, globbed: bool
) -> tuple[list[str], int]:
    """Check a single file, returning its problems and how many pins it declared.

    The match count is returned because a globbed declaration can only judge
    coverage across the whole set: see ``check_declaration``.
    """
    problems: list[str] = []
    relative = path.relative_to(REPO_ROOT).as_posix()

    if not path.is_file():
        return (
            [f"python: {relative} is missing, so {declaration.what} is unchecked."],
            0,
        )

    text = path.read_text(encoding="utf-8")
    matches = list(declaration.pattern.finditer(text))

    if not matches:
        # For a named file, declaring nothing means the check silently stopped
        # covering it. Across a glob it is ordinary, so it is judged by the caller.
        if not globbed:
            problems.append(
                f"python: {relative} declares no Python version, so "
                f"{declaration.what} is unchecked. The file's format probably "
                f"changed and PYTHON_DECLARATIONS in {Path(__file__).name} needs "
                f"updating."
            )
        return problems, 0

    expected_count = declaration.expected_matches
    if expected_count is not None and len(matches) != expected_count:
        problems.append(
            f"python: {relative} declares the Python version {len(matches)} "
            f"time(s), expected {expected_count}. Every build stage must name the "
            f"same interpreter, or the venv is built on one and run on another."
        )

    for match in matches:
        if minor_of(match.group(1)) == EXPECTED_PYTHON:
            continue
        line = text.count("\n", 0, match.start()) + 1
        problems.append(
            f"python: {relative}:{line} sets {declaration.what} to "
            f"{match.group(1)}, but every surface running backend/ must be on "
            f"{EXPECTED_PYTHON} — the minor the held PyTorch base ships to the "
            f"worker. Either align this back, or move the worker, CI, mypy, the "
            f"docs and EXPECTED_PYTHON in {Path(__file__).name} together in one "
            f"reviewed change."
        )

    return problems, len(matches)


def check_declaration(declaration: Declaration) -> list[str]:
    """Check every file one declaration resolves to."""
    # A globbed declaration sweeps a whole directory, where most files
    # legitimately say nothing about Python (a workflow that never sets up an
    # interpreter, say). So "declares nothing" is only a problem for a named file;
    # across a glob the coverage assertion is that *some* file matched.
    globbed = "*" in declaration.path
    paths = resolve_paths(declaration.path)

    if not paths:
        return [
            f"python: no file matched {declaration.path}, so {declaration.what} is "
            f"unchecked. Either the path moved or the glob is wrong; the "
            f"interpreter check is not covering what it claims to."
        ]

    problems: list[str] = []
    total_matches = 0
    for path in paths:
        found, matched = check_one_file(declaration, path, globbed)
        problems.extend(found)
        total_matches += matched

    if globbed and not total_matches:
        problems.append(
            f"python: no file under {declaration.path} declares a Python version, "
            f"so {declaration.what} is unchecked. The format probably changed and "
            f"PYTHON_DECLARATIONS in {Path(__file__).name} needs updating."
        )
    return problems


def check_interpreter() -> list[str]:
    """Return a problem per surface whose Python version is out of alignment."""
    return [
        problem
        for declaration in PYTHON_DECLARATIONS
        for problem in check_declaration(declaration)
    ]


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
        print(
            "Matched-stack pins agree with every recorded hold, and every "
            f"surface running backend/ declares Python {EXPECTED_PYTHON}."
        )


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
    problems += check_interpreter()
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
