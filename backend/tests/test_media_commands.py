"""Every ffmpeg and ffprobe run reads local files only.

The commands start from ``backend.utils.media_commands``, which allows their
inputs the file protocol and no other. The first test finds any command line
built elsewhere; the rest run the real tools.
"""

from __future__ import annotations

import ast
import http.server
import os
import shutil
import subprocess
import threading
import wave
from collections.abc import Iterator
from pathlib import Path

import pytest

from backend.utils import audio as audio_utils
from backend.utils import import_audio_probe

BACKEND = Path(__file__).resolve().parents[1]
TOOLS = {"ffmpeg", "ffprobe"}


def _command_literals(tree: ast.AST) -> Iterator[ast.AST]:
    """List and tuple literals that start with the name of a tool."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
            first = node.elts[0]
            if isinstance(first, ast.Constant) and first.value in TOOLS:
                yield node


def test_every_command_line_starts_from_media_commands() -> None:
    allowed = BACKEND / "utils" / "media_commands.py"
    found = []
    for path in sorted(BACKEND.rglob("*.py")):
        if "tests" in path.relative_to(BACKEND).parts or path == allowed:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        found += [
            f"{path.relative_to(BACKEND)}:{node.lineno}"
            for node in _command_literals(tree)
        ]

    assert found == []


needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg and ffprobe are not installed",
)


def _write_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\0\0" * 16000)


@pytest.fixture
def served_wav(tmp_path: Path) -> Iterator[tuple[str, list[str]]]:
    """A WAV served over HTTP on localhost, and the paths requested from it."""
    _write_wav(tmp_path / "speech.wav")
    requested: list[str] = []

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(tmp_path), **kwargs)

        def do_GET(self) -> None:
            requested.append(self.path)
            super().do_GET()

        def log_message(self, *args) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/speech.wav", requested
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@needs_ffmpeg
def test_a_local_file_is_still_read(tmp_path: Path) -> None:
    _write_wav(tmp_path / "speech.wav")

    assert audio_utils.get_audio_duration(
        str(tmp_path / "speech.wav")
    ) == pytest.approx(1.0)
    assert audio_utils.convert_to_wav(
        str(tmp_path / "speech.wav"), str(tmp_path / "out.wav")
    )


@needs_ffmpeg
def test_ffprobe_reads_no_other_protocol(served_wav) -> None:
    url, requested = served_wav

    with pytest.raises(RuntimeError):
        audio_utils.get_audio_duration(url)
    with pytest.raises(import_audio_probe.UnreadableMediaError):
        import_audio_probe.run_ffprobe(["-show_streams"], url, timeout=30)

    assert requested == []


@needs_ffmpeg
def test_ffmpeg_reads_no_other_protocol(served_wav, tmp_path: Path) -> None:
    url, requested = served_wav

    assert not audio_utils.convert_to_wav(url, str(tmp_path / "out.wav"))
    with pytest.raises(RuntimeError):
        audio_utils.convert_to_16k_wav(
            url, str(tmp_path / "out16k.wav"), mono=True, timeout=30
        )

    assert requested == []


@needs_ffmpeg
def test_the_served_file_is_readable_without_the_whitelist(served_wav) -> None:
    # The server above does serve the file, so the refusals are the whitelist's.
    url, requested = served_wav

    no_proxy = {k: v for k, v in os.environ.items() if "proxy" not in k.lower()}
    subprocess.run(
        ["ffprobe", "-v", "error", url],
        check=True,
        capture_output=True,
        timeout=30,
        env=no_proxy,
    )

    assert "/speech.wav" in requested
