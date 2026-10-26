#!/usr/bin/env python3
"""Guard the matched dependency stacks and report when a held pin can move.

Some versions are declared in several files that must agree, and some pins sit
below the newest release because a companion package has not shipped a
matching build. A held pin keeps attracting Dependabot security alerts that no
upgrade can fix, so the hold has to be re-justified every time the advisory's
affected range is revised. The policy, and the one-off dismissal procedure, are
in docs/DEVELOPMENT.md#held-pins-and-unfixable-advisories.

This script does four things:

1. **Drift check (offline).** Every file that declares a matched stack must
   declare the same version, and every hold must match the stack it holds.
   Nothing else enforces that, so a bump applied to one requirements file and
   missed in another would test one torch and ship another.
2. **Interpreter check (offline).** Every surface that runs ``backend/`` must
   declare the same Python minor. This follows from the torch stack rather than
   being a separate decision: the worker inherits its interpreter from the
   PyTorch base image, so the API image, CI, and mypy have to follow it. Left
   unchecked, Dependabot walked the API base image from 3.12 to 3.14 on its own
   and made the interpreter serving every HTTP request the only one the test
   suite never ran on.
3. **Hook check (offline).** A tool that both CI and a pre-commit hook run
   must be the same release in both, or a file the hook passes, or rewrites
   with ``--fix``, can still fail CI. Dependabot bumps the requirements pin and
   not the hook, so the bump fails here until the hook moves with it.
4. **Release check (network).** For each hold, ask PyPI whether the blocking
   package has published a version that lets the pin move, so the hold is
   revisited when it can actually be lifted rather than on every alert. No pin
   is held at present, so this half currently has nothing to check.

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
from dataclasses import dataclass
from pathlib import Path

from nemotron_compatibility import check_nemotron_compatibility

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPI_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class MatchedStack:
    """A version that several files must declare identically."""

    package: str
    version: str
    rationale: str
    # Requirements files that must each pin ``package==version``. Independently
    # numbered companions (torchvision, torchcodec, torchaudio) are excluded:
    # they track the stack but do not share its version string.
    files: tuple[str, ...]
    # Dockerfiles whose pytorch/pytorch base tag must start with ``version``.
    images: tuple[str, ...] = ()


@dataclass(frozen=True)
class Hold:
    """A matched stack held below its latest release by an upstream blocker."""

    # Must name a MATCHED_STACKS entry: the drift check is what keeps ``pinned``
    # true of the files.
    package: str
    pinned: str
    blocker: str
    # First ``package`` version that resolves the advisory below. The hold is
    # only fully cleared when ``blocker`` reaches this version too, because the
    # two must be installed as a matched pair.
    clears_advisory_at: str
    advisory: str
    rationale: str


MATCHED_STACKS: tuple[MatchedStack, ...] = (
    MatchedStack(
        package="torch",
        version="2.14.1",
        rationale=(
            "The worker image takes torch from its pytorch/pytorch base, while "
            "CI and the GPU host setup install it from the requirements files. "
            "All three must be the same release, or the suite passes on a "
            "torch that production does not run."
        ),
        files=(
            "requirements/test.txt",
            "requirements/local.txt",
        ),
        images=("docker/Dockerfile.worker",),
    ),
)

# No pin is held at present. Read "Held Pins and Unfixable Advisories" in
# docs/DEVELOPMENT.md before adding one.
HOLDS: tuple[Hold, ...] = ()

# torch==2.14.1 --index-url https://...
PIN_RE_TEMPLATE = r"^{package}==([^\s;#]+)"
# FROM pytorch/pytorch:2.14.1-cuda13.0-cudnn9-runtime@sha256:...
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
# base image (the torch MatchedStack above), and the 2.14.1 image ships Python
# 3.12. The worker's Python therefore changes only when that base does, and
# everything else has to match it rather than the other way round.
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
        path=".github/CONTRIBUTING.md",
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


def declared_pins(stack: MatchedStack) -> list[tuple[str, int, str, str]]:
    """Return (file, line, package, version) for every matched pin on disk."""
    found: list[tuple[str, int, str, str]] = []

    def line_of(text: str, offset: int) -> int:
        return text.count("\n", 0, offset) + 1

    pattern = re.compile(
        PIN_RE_TEMPLATE.format(package=re.escape(stack.package)), re.MULTILINE
    )
    for relative in stack.files:
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            found.append(
                (relative, line_of(text, match.start()), stack.package, match.group(1))
            )
    for relative in stack.images:
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


def check_drift(stack: MatchedStack) -> list[str]:
    """Return a problem description per declaration that disagrees with the stack."""
    problems: list[str] = []
    pins = declared_pins(stack)

    for relative in (*stack.files, *stack.images):
        if not any(found_file == relative for found_file, _, _, _ in pins):
            problems.append(
                f"{stack.package}: {relative} declares no {stack.package} "
                f"version; the drift check is not covering what it claims to."
            )

    for relative, line, package, version in pins:
        # An image tag carries a suffix (2.14.1-cuda13.0-...), so compare the
        # leading version component rather than the whole string.
        actual = version.split("-", 1)[0] if package == "pytorch/pytorch" else version
        if actual != stack.version:
            problems.append(
                f"{stack.package}: {relative}:{line} declares {package} "
                f"{version}, but the recorded stack is {stack.version}. Either "
                f"the stack moved and MATCHED_STACKS in {Path(__file__).name} is "
                f"stale, or the bump was applied inconsistently and the stack is "
                f"now mismatched."
            )
    return problems


def check_hold_records() -> list[str]:
    """Return a problem per hold whose pin no matched stack confirms."""
    stacks = {stack.package: stack for stack in MATCHED_STACKS}
    problems: list[str] = []
    for hold in HOLDS:
        stack = stacks.get(hold.package)
        if stack is None:
            problems.append(
                f"{hold.package}: HOLDS records a hold but MATCHED_STACKS has no "
                f"entry for it, so nothing checks the held pin against the files."
            )
        elif stack.version != hold.pinned:
            problems.append(
                f"{hold.package}: HOLDS records {hold.pinned} but MATCHED_STACKS "
                f"records {stack.version}. Move both together."
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
            f"{EXPECTED_PYTHON}, the minor the PyTorch base ships to the "
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


@dataclass(frozen=True)
class HookedTool:
    """A tool pinned in a requirements file and run again by a pre-commit hook."""

    package: str
    requirements: str
    # The hook repository, whose ``rev`` is ``v`` and the package version.
    hook_repo: str


HOOKED_TOOLS: tuple[HookedTool, ...] = (
    HookedTool(
        package="ruff",
        requirements="requirements/lint.txt",
        hook_repo="https://github.com/astral-sh/ruff-pre-commit",
    ),
)

PRE_COMMIT_CONFIG = ".pre-commit-config.yaml"


def check_hooks() -> list[str]:
    """Return a problem per pre-commit hook that runs another release than CI."""
    config = (REPO_ROOT / PRE_COMMIT_CONFIG).read_text(encoding="utf-8")
    problems: list[str] = []
    for tool in HOOKED_TOOLS:
        requirements = (REPO_ROOT / tool.requirements).read_text(encoding="utf-8")
        pin = re.search(
            PIN_RE_TEMPLATE.format(package=re.escape(tool.package)),
            requirements,
            re.MULTILINE,
        )
        hook = re.search(
            rf"^\s*-\s+repo:\s*{re.escape(tool.hook_repo)}\s*\n\s*rev:\s*v?(\S+)",
            config,
            re.MULTILINE,
        )
        if pin is None or hook is None:
            problems.append(
                f"{tool.package}: expected a pin in {tool.requirements} and a "
                f"{tool.hook_repo} hook in {PRE_COMMIT_CONFIG}; the hook check "
                f"is not covering what it claims to."
            )
        elif pin.group(1) != hook.group(1):
            problems.append(
                f"{tool.package}: {tool.requirements} pins {pin.group(1)} but the "
                f"pre-commit hook in {PRE_COMMIT_CONFIG} runs {hook.group(1)}. "
                f"Move the hook's rev to v{pin.group(1)} in the same change."
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
            "Matched-stack pins agree with every recorded stack and hold, "
            f"every surface running backend/ declares Python {EXPECTED_PYTHON}, "
            "and every pre-commit hook runs the release CI pins."
        )
    if not HOLDS:
        print("No dependency pin is currently held.")


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

    problems = [problem for stack in MATCHED_STACKS for problem in check_drift(stack)]
    problems += check_hold_records()
    problems += check_interpreter()
    problems += check_hooks()
    problems += check_nemotron_compatibility(REPO_ROOT)
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
