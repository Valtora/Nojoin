"""Screen study (#653): the ring detector on synthetic screens.

Every image here is drawn at runtime by scripts/speaker_frames/synth.py and
never written to disk. The full gate, 99% per frame on the held-out
synthetic meetings, is run by the study's runner before it reads a real
frame; this suite checks the pieces and a few meetings end to end.
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image, ImageDraw
from scipy import ndimage

from scripts.speaker_frames import detect, evaluate, synth

TEAMS = (127, 133, 245)


def _encode(image: Image.Image, long_edge: int = 1280) -> np.ndarray:
    spec = synth.MeetingSpec(platform="teams", seed=0, long_edge=long_edge)
    return synth.decode(synth.browser_encode(image, spec))


def _stage(
    width: int = 1920, height: int = 1080
) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (width, height), (31, 31, 31))
    draw = ImageDraw.Draw(image)
    for row in range(3):
        for col in range(3):
            x, y = 200 + col * 520, 100 + row * 300
            draw.rounded_rectangle(
                (x, y, x + 500, y + 281), radius=8, fill=(41, 41, 41)
            )
    return image, draw


@pytest.mark.parametrize("size", [3, 5, 9, 13])
@pytest.mark.parametrize("axis", [0, 1])
def test_the_sliding_window_matches_scipy(size: int, axis: int) -> None:
    values = np.random.default_rng(size).random((31, 47)).astype(np.float32)
    assert np.array_equal(
        detect._slide(values, size, axis, np.minimum),
        ndimage.minimum_filter1d(values, size, axis=axis),
    )
    assert np.array_equal(
        detect._slide(values, size, axis, np.maximum),
        ndimage.maximum_filter1d(values, size, axis=axis),
    )


def test_hue_is_the_angle_of_a_colour_or_a_difference() -> None:
    assert detect.hue_distance(detect.hue(TEAMS), 237) < 2
    assert detect.hue_distance(detect.hue((255, 0, 0)), 0) < 1
    # Adding grey to a colour, or taking it away, does not move its hue.
    assert (
        detect.hue_distance(
            detect.hue((127 - 41, 133 - 41, 245 - 41)), detect.hue(TEAMS)
        )
        < 1
    )


def test_a_lit_ring_is_found_where_it_was_drawn() -> None:
    image, draw = _stage()
    draw.rounded_rectangle((720, 400, 1220, 681), radius=8, outline=TEAMS, width=3)
    rings = detect.find_rings(_encode(image), TEAMS)
    expected = (720 * 2 / 3, 400 * 2 / 3, 1220 * 2 / 3, 681 * 2 / 3)
    assert any(detect.iou(r.box, expected) > 0.9 for r in rings)


def test_filled_buttons_and_unlit_tiles_are_not_rings() -> None:
    image, draw = _stage()
    draw.rounded_rectangle((900, 20, 1000, 60), radius=12, fill=TEAMS)
    draw.ellipse((420, 200, 560, 340), fill=(180, 90, 200))
    assert detect.find_rings(_encode(image), TEAMS) == []


def test_an_outline_in_another_colour_is_found_with_its_own_hue() -> None:
    image, draw = _stage()
    draw.rounded_rectangle(
        (200, 100, 700, 381), radius=8, outline=(35, 217, 89), width=3
    )
    rings = detect.find_rings(_encode(image), None)
    assert len(rings) == 1
    assert detect.hue_distance(detect.hue(rings[0].rgb), detect.hue((35, 217, 89))) < 20


def test_a_slot_that_lights_over_another_starts_a_new_layout() -> None:
    slots = [
        detect.Slot((0, 0, 100, 60)),
        detect.Slot((110, 0, 210, 60)),
        detect.Slot((50, 30, 150, 90)),  # the gallery after a reflow
    ]
    layouts, per_frame = detect._layouts([(0,), (1,), (2,), (0,)], slots)
    assert per_frame == [0, 0, 1, 2]
    result = detect.MeetingResult((0, 0, 0), True, slots, layouts, [])
    numbers = result.layout_ids()
    # The first gallery comes back as the same layout.
    assert numbers[0] == numbers[2] != numbers[1]


def test_the_two_groups_of_a_toggling_slot_split_and_a_steady_one_does_not() -> None:
    toggling = np.array([0.1, 0.2, 9.0, 8.5, 0.0, 9.2, 0.3] * 4)
    low, high, spread = detect._two_groups(toggling)
    assert high - low > 8 and spread < 1
    steady = np.array([4.0, 4.3, 3.8, 4.1, 4.2] * 6)
    low, high, spread = detect._two_groups(steady)
    assert high - low < detect.DEFAULT.lit_separation


@pytest.mark.parametrize("platform", synth.PLATFORMS)
def test_easy_meetings_are_read_frame_by_frame(platform: str) -> None:
    # 1080p at 150%: a ring three pixels wide at the sampler's 1920 px.
    spec = synth.MeetingSpec(
        platform=platform,
        seed=4242,
        frames=40,
        screen=synth.Screen(1920, 1080, 1.5),
        participants=6,
        kinds=("gallery",),
    )
    scores = evaluate.score_meeting(spec)
    accuracy = sum(s.correct for s in scores) / len(scores)
    # Tiles lit in fewer than three frames cannot form a slot, so a short
    # meeting is not read perfectly.
    assert accuracy >= 0.9


def test_synthetic_frames_follow_the_samplers_change_rule() -> None:
    rule = synth.ChangeRule()
    still = Image.new("RGB", (1280, 720), (31, 31, 31))
    assert rule.keep(still, 0)
    assert not rule.keep(still, 1)
    # A thin coloured line lighting up moves one block's blue by seven
    # levels, as the sampler's own test has it.
    lit = still.copy()
    ImageDraw.Draw(lit).line((645, 400, 645, 419), fill=(95, 99, 174))
    assert rule.keep(lit, 2)
    assert not rule.keep(lit, 16)
    assert rule.keep(lit, 17)
