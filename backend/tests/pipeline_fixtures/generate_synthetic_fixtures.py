#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import wave
from pathlib import Path
from typing import Iterable

SAMPLE_RATE = 16_000
SPEAKER_FREQUENCIES = {
    "speaker_a": 220.0,
    "speaker_b": 330.0,
    "speaker_c": 440.0,
}


def _tone_sample(t: float, frequency: float, amplitude: float) -> float:
    return amplitude * math.sin(2.0 * math.pi * frequency * t)


def _fixture_duration(known_turns: list[dict]) -> float:
    if not known_turns:
        return 1.0
    return max(float(turn["end"]) for turn in known_turns) + 0.25


def _render_samples(fixture: dict) -> Iterable[int]:
    known_turns = fixture.get("known_turns") or []
    duration_s = _fixture_duration(known_turns)
    total_samples = int(duration_s * SAMPLE_RATE)
    scenarios = set(fixture.get("scenarios") or [])

    for index in range(total_samples):
        t = index / SAMPLE_RATE
        value = 0.0
        for turn in known_turns:
            start = float(turn["start"])
            end = float(turn["end"])
            if start <= t < end:
                speaker = str(turn.get("speaker", "speaker_a"))
                frequency = SPEAKER_FREQUENCIES.get(
                    speaker, SPEAKER_FREQUENCIES["speaker_a"]
                )
                amplitude = 0.20
                if "quiet_speaker" in scenarios and speaker == "speaker_b":
                    amplitude = 0.055
                value += _tone_sample(t, frequency, amplitude)

        if "noise" in scenarios:
            value += 0.025 * math.sin(2.0 * math.pi * 73.0 * t)

        value = max(-0.95, min(0.95, value))
        yield int(value * 32767)


def _write_wav(path: Path, samples: Iterable[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE)
        frames = bytearray()
        for sample in samples:
            frames.extend(int(sample).to_bytes(2, byteorder="little", signed=True))
        wav_file.writeframes(bytes(frames))


def generate_fixtures(manifest_path: Path, output_dir: Path) -> list[Path]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    generated: list[Path] = []
    for fixture in manifest.get("fixtures", []):
        relative_path = Path(str(fixture["path"]))
        target_path = output_dir / relative_path.name
        _write_wav(target_path, _render_samples(fixture))
        generated.append(target_path)
    return generated


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate synthetic Nojoin pipeline baseline WAV fixtures."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).with_name("manifest.synthetic.json"),
        help="Fixture manifest to render.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).with_name("generated"),
        help="Directory where generated WAV files are written.",
    )
    args = parser.parse_args()

    generated = generate_fixtures(args.manifest, args.output_dir)
    for path in generated:
        print(path)


if __name__ == "__main__":
    main()
