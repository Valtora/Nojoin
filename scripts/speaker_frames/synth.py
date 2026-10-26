"""Synthetic Teams, Zoom and Meet screens with the lit tile known.

Each meeting is drawn at runtime from plain shapes, after the public look of
the three apps: a gallery of 2 to 49 tiles, the filmstrip beside someone's
screen share, speaker view, cameras on and off, Meet's audio-level indicator,
and each app's ring style and colour. The meeting window sits anywhere on an
Entire Screen desktop of 1080p to 4K at 100 to 200% scaling, beside other
windows whose accents are close to the ring's colour. One frame is drawn per
second of audio and shrunk to the sampler's long edge with a filtered
resize, as the sampler's high-quality canvas draw does. The sampler's change
rule then decides which frames are kept, and those are JPEG-encoded at
quality 0.8 and come out with the boxes of the tiles that are lit.

Nothing here reads a file or keeps an image: frames exist only as bytes the
caller asked for.
"""

from __future__ import annotations

import colorsys
import io
import math
import random
from collections.abc import Iterator
from dataclasses import dataclass, field, replace

import numpy as np
from PIL import Image, ImageDraw, ImageFont

RGB = tuple[int, int, int]
# x0, y0, x1, y1 with exclusive ends, in pixels.
Box = tuple[float, float, float, float]

PLATFORMS = ("teams", "zoom", "meet")
SAMPLER_LONG_EDGE = 1920
SAMPLER_JPEG_QUALITY = 80  # canvas.toBlob(..., "image/jpeg", 0.8)
SCREENS = (
    (1920, 1080),
    (1920, 1200),
    (2560, 1440),
    (2560, 1600),
    (3440, 1440),
    (3840, 2160),
)
SCALES = (1.0, 1.25, 1.5, 1.75, 2.0)
# Gallery sizes are drawn from these bands so every band is tested.
GALLERY_BANDS = ((2, 4), (5, 9), (10, 16), (17, 25), (26, 49))
TASKBAR = 48  # logical px


@dataclass(frozen=True)
class Look:
    """One app's meeting window, in logical pixels."""

    platform: str
    stage: RGB
    tile: RGB
    chrome: RGB
    ring: RGB
    ring_width: int
    radius: int
    gap: int
    header: int
    toolbar: int
    aspects: tuple[float, ...]
    strip_side: str
    # Zoom keeps the last speaker's ring on through a silence.
    holds_last: bool
    # Meet's audio-level indicator, a disc with moving bars.
    indicator: bool
    accent: RGB


@dataclass(frozen=True)
class Screen:
    width: int
    height: int
    scale: float


@dataclass(frozen=True)
class MeetingSpec:
    platform: str
    seed: int
    frames: int = 60
    screen: Screen | None = None
    participants: int | None = None
    long_edge: int = SAMPLER_LONG_EDGE
    quality: int = SAMPLER_JPEG_QUALITY
    # "area" shrinks with a filter, as the sampler's high-quality canvas
    # resize does. "point" samples without one, as a GPU bilinear draw with
    # no mipmaps would.
    resize: str = "area"
    # Which layouts the meeting may use: gallery, share and speaker.
    kinds: tuple[str, ...] = ("gallery", "share", "speaker")
    # Keep only the frames the sampler's change rule keeps.
    change_rule: bool = True


@dataclass(frozen=True)
class FrameTruth:
    """What a frame shows, in the encoded frame's pixels."""

    lit: tuple[Box, ...]
    tiles: tuple[Box, ...]
    kind: str
    tile_count: int
    segment: int


@dataclass(frozen=True)
class SyntheticFrame:
    jpeg: bytes
    truth: FrameTruth
    # The audio tick it was sampled on, one a second from 0.
    tick: int = 0


@dataclass
class Person:
    camera: bool
    avatar: RGB
    initials: str
    label: str
    backdrop: RGB
    shirt: RGB
    skin: RGB
    hair: RGB
    stripes: RGB | None


@dataclass
class Toggle:
    """A distractor outline that comes and goes between frames."""

    box: Box
    colour: RGB
    width: int
    radius: int
    on: bool = False


@dataclass
class Segment:
    kind: str
    window: Box
    stage: Box
    tiles: list[Box]
    people: list[int]
    big: Box | None = None
    content: Box | None = None
    side: Box | None = None
    # Speaker view's thumbnails, which never carry a ring.
    strip: list[Box] = field(default_factory=list)
    strip_people: list[int] = field(default_factory=list)
    toggles: list[Toggle] = field(default_factory=list)
    frames: int = 0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _jitter(colour: RGB, rng: random.Random, spread: int) -> RGB:
    return tuple(  # type: ignore[return-value]
        int(_clamp(c + rng.randint(-spread, spread), 0, 255)) for c in colour
    )


def _hue_shift(colour: RGB, degrees: float) -> RGB:
    h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in colour))
    r, g, b = colorsys.hsv_to_rgb((h + degrees / 360) % 1.0, s, v)
    return (round(r * 255), round(g * 255), round(b * 255))


def _random_colour(rng: random.Random, saturation: tuple[float, float]) -> RGB:
    r, g, b = colorsys.hsv_to_rgb(
        rng.random(), rng.uniform(*saturation), rng.uniform(0.45, 0.95)
    )
    return (round(r * 255), round(g * 255), round(b * 255))


def sample_look(platform: str, rng: random.Random) -> Look:
    """A variant of one app's look: ring colour, width, corners and gaps."""
    if platform == "teams":
        return Look(
            platform="teams",
            stage=(31, 31, 31),
            tile=(41, 41, 41),
            chrome=(41, 41, 41),
            ring=_jitter((127, 133, 245), rng, 6),
            ring_width=rng.choice((2, 2, 3)),
            radius=rng.choice((4, 8, 8)),
            gap=rng.choice((4, 8)),
            header=48,
            toolbar=0,
            aspects=(16 / 9,),
            strip_side="top",
            holds_last=False,
            indicator=False,
            accent=(91, 95, 199),
        )
    if platform == "zoom":
        return Look(
            platform="zoom",
            stage=rng.choice(((26, 26, 26), (0, 0, 0))),
            tile=(35, 35, 35),
            chrome=(26, 26, 26),
            ring=_jitter((35, 217, 89), rng, 6),
            ring_width=rng.choice((2, 3, 3)),
            radius=rng.choice((0, 6, 8)),
            gap=rng.choice((2, 3, 4)),
            header=28,
            toolbar=60,
            aspects=(16 / 9,),
            strip_side=rng.choice(("top", "right")),
            holds_last=True,
            indicator=False,
            accent=(35, 217, 89),
        )
    if platform == "meet":
        return Look(
            platform="meet",
            stage=(32, 33, 36),
            tile=(60, 64, 67),
            chrome=(32, 33, 36),
            ring=_jitter((138, 180, 248), rng, 6),
            ring_width=rng.choice((2, 3, 4)),
            radius=rng.choice((8, 12)),
            gap=rng.choice((8, 10)),
            header=0,
            toolbar=80,
            aspects=(4 / 3, 1.5, 16 / 9),
            strip_side="right",
            holds_last=False,
            indicator=True,
            accent=(138, 180, 248),
        )
    raise ValueError(f"unknown platform {platform!r}")


def grid_boxes(
    area: Box, count: int, gap: float, aspects: tuple[float, ...]
) -> list[Box]:
    """Tiles laid out as the apps do: the largest tile that fits, rows centred."""
    x0, y0, x1, y1 = area
    width, height = x1 - x0, y1 - y0
    best: tuple[float, int, float, float] | None = None
    for cols in range(1, count + 1):
        rows = math.ceil(count / cols)
        for aspect in aspects:
            tile_w = min(
                (width - (cols - 1) * gap) / cols,
                ((height - (rows - 1) * gap) / rows) * aspect,
            )
            if tile_w <= 0:
                continue
            area_px = tile_w * tile_w / aspect
            if best is None or area_px > best[0]:
                best = (area_px, cols, tile_w, tile_w / aspect)
    if best is None:
        return []
    _, cols, tile_w, tile_h = best
    rows = math.ceil(count / cols)
    top = y0 + (height - (rows * tile_h + (rows - 1) * gap)) / 2
    boxes: list[Box] = []
    for row in range(rows):
        in_row = min(cols, count - row * cols)
        left = x0 + (width - (in_row * tile_w + (in_row - 1) * gap)) / 2
        for col in range(in_row):
            bx = left + col * (tile_w + gap)
            by = top + row * (tile_h + gap)
            boxes.append(_round_box((bx, by, bx + tile_w, by + tile_h)))
    return boxes


def _round_box(box: Box) -> Box:
    return (round(box[0]), round(box[1]), round(box[2]), round(box[3]))


def _strip_boxes(
    stage: Box, look: Look, count: int, scale: float
) -> tuple[list[Box], Box]:
    """The filmstrip beside a screen share, and the area left for the share."""
    x0, y0, x1, y1 = stage
    gap = look.gap * scale
    if look.strip_side == "top":
        tile_h = _clamp((y1 - y0) * 0.16, 72 * scale, 120 * scale)
        tile_w = tile_h * 16 / 9
        fits = max(1, int((x1 - x0 + gap) // (tile_w + gap)))
        count = min(count, fits, 8)
        left = x0 + (x1 - x0 - (count * tile_w + (count - 1) * gap)) / 2
        boxes = [
            _round_box(
                (
                    left + i * (tile_w + gap),
                    y0,
                    left + i * (tile_w + gap) + tile_w,
                    y0 + tile_h,
                )
            )
            for i in range(count)
        ]
        return boxes, (x0, y0 + tile_h + gap * 2, x1, y1)
    tile_w = _clamp((x1 - x0) * 0.18, 160 * scale, 260 * scale)
    tile_h = tile_w * 9 / 16
    fits = max(1, int((y1 - y0 + gap) // (tile_h + gap)))
    count = min(count, fits, 8)
    right = x1 - tile_w
    boxes = [
        _round_box(
            (right, y0 + i * (tile_h + gap), x1, y0 + i * (tile_h + gap) + tile_h)
        )
        for i in range(count)
    ]
    return boxes, (x0, y0, right - gap * 2, y1)


def _fit(area: Box, aspect: float) -> Box:
    x0, y0, x1, y1 = area
    width, height = x1 - x0, y1 - y0
    w = min(width, height * aspect)
    h = w / aspect
    left, top = x0 + (width - w) / 2, y0 + (height - h) / 2
    return _round_box((left, top, left + w, top + h))


class _Fonts:
    def __init__(self) -> None:
        self._cache: dict[int, ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}

    def get(self, size: float) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        key = max(6, round(size))
        if key not in self._cache:
            self._cache[key] = ImageFont.load_default(size=key)
        return self._cache[key]


_FONTS = _Fonts()
_LETTERS = "abcdefghijklmnopqrstuvwxyz"


def _made_up_word(rng: random.Random, low: int = 3, high: int = 8) -> str:
    return "".join(
        rng.choice(_LETTERS) for _ in range(rng.randint(low, high))
    ).capitalize()


def _make_people(count: int, rng: random.Random) -> list[Person]:
    camera_rate = rng.choice((0.0, 0.3, 0.7, 1.0))
    people = []
    for _ in range(count):
        first, last = _made_up_word(rng), _made_up_word(rng)
        stripes = _random_colour(rng, (0.6, 1.0)) if rng.random() < 0.25 else None
        people.append(
            Person(
                camera=rng.random() < camera_rate,
                avatar=_random_colour(rng, (0.35, 0.8)),
                initials=(first[0] + last[0]).upper(),
                label=f"{first} {last[0]}.",
                backdrop=_random_colour(rng, (0.05, 0.4)),
                shirt=_random_colour(rng, (0.2, 1.0)),
                skin=_jitter(
                    rng.choice(((224, 172, 138), (141, 85, 36), (198, 134, 66))),
                    rng,
                    15,
                ),
                hair=_jitter(
                    rng.choice(((40, 30, 20), (90, 60, 30), (200, 170, 110))), rng, 10
                ),
                stripes=stripes,
            )
        )
    return people


def _speech(
    people: int, frames: int, look: Look, rng: random.Random
) -> list[frozenset[int]]:
    """Who is lit in each frame: turns of a few frames, some silence."""
    talkers = rng.sample(range(people), k=min(people, rng.randint(2, 6)))
    weights = [rng.uniform(0.3, 1.0) for _ in talkers]
    states: list[frozenset[int]] = []
    current: int | None = None
    last: int | None = None
    left = 0
    while len(states) < frames:
        if left <= 0:
            silent = rng.random() < 0.15
            current = None if silent else rng.choices(talkers, weights)[0]
            left = 1 + int(rng.expovariate(1 / (1.5 if silent else 4.0)))
        lit = {current} if current is not None else set()
        if current is not None and not look.holds_last and rng.random() < 0.03:
            lit.add(rng.choice(talkers))
        if look.holds_last and not lit and last is not None:
            lit = {last}
        if current is not None:
            last = current
        states.append(frozenset(lit))
        left -= 1
    return states


@dataclass(frozen=True)
class _Context:
    spec: MeetingSpec
    look: Look
    screen: Screen
    people: list[Person]
    wallpaper: Image.Image


def _choose_screen(rng: random.Random) -> Screen:
    width, height = rng.choice(SCREENS)
    return Screen(width, height, rng.choice(SCALES))


def _choose_participants(rng: random.Random) -> int:
    low, high = rng.choice(GALLERY_BANDS)
    return rng.randint(low, high)


def _wallpaper(screen: Screen, rng: random.Random) -> Image.Image:
    cells = np.array(
        [[_random_colour(rng, (0.1, 0.7)) for _ in range(8)] for _ in range(5)],
        dtype=np.uint8,
    )
    return Image.fromarray(cells, "RGB").resize(
        (screen.width, screen.height), Image.Resampling.BICUBIC
    )


def _place_window(screen: Screen, rng: random.Random) -> Box:
    usable_h = screen.height - TASKBAR * screen.scale
    if rng.random() < 0.4:
        return (0, 0, screen.width, round(usable_h))
    min_w = min(screen.width, max(screen.width * 0.5, 760 * screen.scale))
    min_h = min(usable_h, max(usable_h * 0.55, 480 * screen.scale))
    width = rng.uniform(min_w, screen.width)
    height = rng.uniform(min_h, usable_h)
    left = rng.uniform(0, screen.width - width)
    top = rng.uniform(0, usable_h - height)
    return _round_box((left, top, left + width, top + height))


def _stage_area(
    window: Box, look: Look, scale: float, panel: bool
) -> tuple[Box, Box | None]:
    x0, y0, x1, y1 = window
    margin = 8 * scale
    top = y0 + look.header * scale + margin
    bottom = y1 - look.toolbar * scale - margin
    right = x1 - margin
    side: Box | None = None
    if panel and (x1 - x0) > 1000 * scale:
        side = (x1 - 320 * scale, top, x1 - margin, bottom)
        right = side[0] - margin
    return (x0 + margin, top, right, bottom), side


def _plan_segments(ctx: _Context, rng: random.Random) -> list[Segment]:
    spec, look, scale = ctx.spec, ctx.look, ctx.screen.scale
    count = rng.randint(1, 3)
    lengths = _split(spec.frames, count, rng)
    window = _place_window(ctx.screen, rng)
    panel = rng.random() < 0.3
    order = list(range(len(ctx.people)))
    segments = []
    for index, length in enumerate(lengths):
        if index and rng.random() < 0.25:
            window = _place_window(ctx.screen, rng)
        if index == 0 or rng.random() < 0.5:
            rng.shuffle(order)
        kind = (
            rng.choice(spec.kinds)
            if index
            else ("gallery" if "gallery" in spec.kinds else spec.kinds[0])
        )
        stage, side = _stage_area(window, look, scale, panel)
        segment = _layout(kind, (window, stage), look, scale, order)
        segment.side = side
        segment.frames = length
        segments.append(segment)
    return segments


def _split(total: int, parts: int, rng: random.Random) -> list[int]:
    """Segment lengths, none shorter than 15 frames where the total allows."""
    parts = max(1, min(parts, total // 15))
    lengths = [15] * parts
    for _ in range(total - 15 * parts):
        lengths[rng.randrange(parts)] += 1
    return lengths


def _layout(
    kind: str, place: tuple[Box, Box], look: Look, scale: float, order: list[int]
) -> Segment:
    window, stage = place
    if kind == "gallery":
        shown = order[:49]
        tiles = grid_boxes(stage, len(shown), look.gap * scale, look.aspects)
        return Segment(kind, window, stage, tiles, shown[: len(tiles)])
    if kind == "share":
        tiles, content = _strip_boxes(stage, look, len(order), scale)
        return Segment(kind, window, stage, tiles, order[: len(tiles)], content=content)
    # Speaker view shows the speaker large and draws no ring. Zoom keeps a
    # row of thumbnails above it.
    strip: list[Box] = []
    area = stage
    if look.platform == "zoom":
        strip, area = _strip_boxes(
            stage, replace(look, strip_side="top"), min(5, len(order)), scale
        )
    return Segment(
        "speaker",
        window,
        stage,
        [],
        [],
        big=_fit(area, 16 / 9),
        strip=strip,
        strip_people=order[: len(strip)],
    )


class MeetingRenderer:
    """Draws one synthetic meeting frame by frame."""

    def __init__(self, spec: MeetingSpec) -> None:
        rng = random.Random(spec.seed)
        look = sample_look(spec.platform, rng)
        screen = spec.screen or _choose_screen(rng)
        count = spec.participants or _choose_participants(rng)
        people = _make_people(count, rng)
        self.ctx = _Context(spec, look, screen, people, _wallpaper(screen, rng))
        self.rng = rng
        self.segments = _plan_segments(self.ctx, rng)
        self.speech = _speech(count, spec.frames, look, rng)
        self.out_size = _out_size(screen, spec.long_edge)

    @property
    def look(self) -> Look:
        return self.ctx.look

    @property
    def screen(self) -> Screen:
        return self.ctx.screen

    def frames(self) -> Iterator[SyntheticFrame]:
        """The frames the sampler keeps, one tick a second."""
        frame = 0
        rule = ChangeRule()
        for index, segment in enumerate(self.segments):
            base = _render_base(
                segment, self.ctx, random.Random(self.ctx.spec.seed * 31 + index)
            )
            for _ in range(segment.frames):
                image, lit = self._draw(segment, base, frame)
                small = browser_shrink(image, self.ctx.spec)
                if not self.ctx.spec.change_rule or rule.keep(small, frame):
                    truth = self._truth(segment, lit, index)
                    yield SyntheticFrame(
                        browser_jpeg(small, self.ctx.spec), truth, frame
                    )
                frame += 1

    def _draw(
        self, segment: Segment, base: Image.Image, frame: int
    ) -> tuple[Image.Image, list[Box]]:
        image = base.copy()
        draw = ImageDraw.Draw(image)
        speaking = self.speech[min(frame, len(self.speech) - 1)]
        for box, person_index in zip(segment.tiles, segment.people, strict=False):
            _draw_tile_face(image, draw, box, self.ctx, (person_index, frame))
        for box, person_index in zip(segment.strip, segment.strip_people, strict=False):
            _draw_tile_face(image, draw, box, self.ctx, (person_index, frame))
        if segment.big is not None:
            speaker = min(speaking) if speaking else 0
            _draw_tile_face(image, draw, segment.big, self.ctx, (speaker, frame))
        for toggle in segment.toggles:
            _step_toggle(toggle, self.rng)
            if toggle.on:
                draw.rounded_rectangle(
                    toggle.box,
                    radius=toggle.radius,
                    outline=toggle.colour,
                    width=toggle.width,
                )
        lit = [
            box
            for box, p in zip(segment.tiles, segment.people, strict=False)
            if p in speaking
        ]
        for box in lit:
            _draw_ring(draw, box, self.ctx, frame)
        return image, lit

    def _truth(self, segment: Segment, lit: list[Box], index: int) -> FrameTruth:
        sx = self.out_size[0] / self.ctx.screen.width
        sy = self.out_size[1] / self.ctx.screen.height

        def scaled(box: Box) -> Box:
            return (box[0] * sx, box[1] * sy, box[2] * sx, box[3] * sy)

        return FrameTruth(
            lit=tuple(scaled(b) for b in lit),
            tiles=tuple(scaled(b) for b in segment.tiles),
            kind=segment.kind,
            tile_count=len(segment.tiles),
            segment=index,
        )


def _out_size(screen: Screen, long_edge: int) -> tuple[int, int]:
    factor = long_edge / max(screen.width, screen.height)
    return (round(screen.width * factor), round(screen.height * factor))


def _step_toggle(toggle: Toggle, rng: random.Random) -> None:
    if rng.random() < (0.35 if toggle.on else 0.2):
        toggle.on = not toggle.on


def _draw_ring(draw: ImageDraw.ImageDraw, box: Box, ctx: _Context, frame: int) -> None:
    look, scale = ctx.look, ctx.screen.scale
    width = max(1, round(look.ring_width * scale))
    draw.rounded_rectangle(
        box, radius=round(look.radius * scale), outline=look.ring, width=width
    )
    if not look.indicator:
        return
    # Meet's audio-level disc in the tile's top-right corner, bars moving.
    size = 26 * scale
    cx, cy = box[2] - 10 * scale - size / 2, box[1] + 10 * scale + size / 2
    draw.ellipse(
        (cx - size / 2, cy - size / 2, cx + size / 2, cy + size / 2),
        fill=(26, 115, 232),
    )
    rng = random.Random(frame * 7919 + int(box[0]))
    for i in (-1, 0, 1):
        bar = size * rng.uniform(0.15, 0.32)
        x = cx + i * size * 0.22
        draw.rectangle((x - scale, cy - bar, x + scale, cy + bar), fill=(255, 255, 255))


def _draw_tile_face(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    box: Box,
    ctx: _Context,
    who: tuple[int, int],
) -> None:
    person_index, frame = who
    person = ctx.people[person_index % len(ctx.people)]
    look, scale = ctx.look, ctx.screen.scale
    radius = round(look.radius * scale)
    w, h = int(box[2] - box[0]), int(box[3] - box[1])
    if w < 4 or h < 4:
        return
    if person.camera:
        video = _video(person, (w, h), frame, person_index)
        image.paste(video, (int(box[0]), int(box[1])), _round_mask((w, h), radius))
        if person.stripes is not None:
            for k in range(3):
                y = box[1] + h * (0.2 + 0.12 * k) + (frame % 3)
                draw.line(
                    (box[0] + w * 0.05, y, box[0] + w * 0.3, y),
                    fill=person.stripes,
                    width=max(1, round(scale)),
                )
    else:
        disc = min(h * 0.42, 120 * scale)
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        draw.ellipse(
            (cx - disc / 2, cy - disc / 2, cx + disc / 2, cy + disc / 2),
            fill=person.avatar,
        )
        font = _FONTS.get(disc * 0.38)
        draw.text(
            (cx, cy), person.initials, fill=(255, 255, 255), font=font, anchor="mm"
        )
    _draw_name(draw, box, person.label, scale)


def _draw_name(draw: ImageDraw.ImageDraw, box: Box, label: str, scale: float) -> None:
    size = 12 * scale
    if box[3] - box[1] < size * 3:
        return
    font = _FONTS.get(size)
    pad = 6 * scale
    x, y = box[0] + pad + 4 * scale, box[3] - pad - size * 1.6
    text_w = draw.textlength(label, font=font)
    draw.rounded_rectangle(
        (x - 4 * scale, y, x + text_w + 4 * scale, y + size * 1.5),
        radius=round(4 * scale),
        fill=(20, 20, 20),
    )
    draw.text((x, y + size * 0.15), label, fill=(240, 240, 240), font=font)


_MASKS: dict[tuple[int, int, int], Image.Image] = {}


def _round_mask(size: tuple[int, int], radius: int) -> Image.Image:
    key = (size[0], size[1], radius)
    if key not in _MASKS:
        if len(_MASKS) > 256:
            _MASKS.clear()
        mask = Image.new("L", size, 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255
        )
        _MASKS[key] = mask
    return _MASKS[key]


def _video(person: Person, size: tuple[int, int], frame: int, salt: int) -> Image.Image:
    """A camera frame: a backdrop, a head and shoulders, a little motion."""
    rng = np.random.default_rng(salt * 1009 + frame)
    lw, lh = 48, 27
    yy, xx = np.mgrid[0:lh, 0:lw].astype(np.float32)
    canvas = np.empty((lh, lw, 3), np.float32)
    canvas[:] = person.backdrop
    canvas *= (0.85 + 0.3 * xx / lw)[..., None]
    sway = math.sin(frame * 0.7 + salt) * 1.5
    cx, cy = lw / 2 + sway, lh * 0.55
    body = ((xx - cx) / 14) ** 2 + ((yy - lh) / 9) ** 2 < 1
    canvas[body] = person.shirt
    head = ((xx - cx) / 5.5) ** 2 + ((yy - cy + 4) / 7) ** 2 < 1
    canvas[head] = person.skin
    hair = head & (yy < cy - 5)
    canvas[hair] = person.hair
    canvas += rng.normal(0, 4, canvas.shape)
    small = Image.fromarray(np.clip(canvas, 0, 255).astype(np.uint8), "RGB")
    return small.resize(size, Image.Resampling.BILINEAR)


def _render_base(segment: Segment, ctx: _Context, rng: random.Random) -> Image.Image:
    look, scale = ctx.look, ctx.screen.scale
    image = ctx.wallpaper.copy()
    draw = ImageDraw.Draw(image)
    _draw_side_windows(draw, segment, ctx, rng)
    _draw_taskbar(draw, ctx.screen, rng)
    x0, y0, x1, y1 = segment.window
    draw.rectangle(segment.window, fill=look.stage)
    if look.header:
        draw.rectangle((x0, y0, x1, y0 + look.header * scale), fill=look.chrome)
        _draw_buttons(
            draw,
            (
                x1 - 420 * scale,
                y0 + 8 * scale,
                x1 - 12 * scale,
                y0 + (look.header - 8) * scale,
            ),
            look,
        )
    if look.toolbar:
        bar = (x0, y1 - look.toolbar * scale, x1, y1)
        draw.rectangle(bar, fill=look.chrome)
        mid = (x0 + x1) / 2
        _draw_buttons(
            draw,
            (
                mid - 260 * scale,
                bar[1] + 14 * scale,
                mid + 260 * scale,
                y1 - 14 * scale,
            ),
            look,
        )
    if segment.side is not None:
        _draw_panel(draw, segment.side, look, scale, rng)
    for box in segment.tiles + segment.strip:
        draw.rounded_rectangle(box, radius=round(look.radius * scale), fill=look.tile)
    if segment.big is not None:
        draw.rounded_rectangle(
            segment.big, radius=round(look.radius * scale), fill=look.tile
        )
    if segment.content is not None:
        _draw_share(draw, segment.content, ctx, rng)
    return image


def _draw_buttons(draw: ImageDraw.ImageDraw, area: Box, look: Look) -> None:
    x0, y0, x1, y1 = area
    size = y1 - y0
    count = max(1, int((x1 - x0) // (size * 1.6)))
    for i in range(count):
        left = x0 + i * size * 1.6
        fill = (
            (196, 49, 75)
            if i == count - 1
            else (look.accent if i == 2 else (70, 70, 70))
        )
        draw.rounded_rectangle(
            (left, y0, left + size * 1.3, y1), radius=round(size / 3), fill=fill
        )
        draw.rectangle(
            (left + size * 0.45, y0 + size * 0.3, left + size * 0.85, y1 - size * 0.3),
            fill=(235, 235, 235),
        )


def _draw_panel(
    draw: ImageDraw.ImageDraw, side: Box, look: Look, scale: float, rng: random.Random
) -> None:
    """A chat or participants panel: avatars, text and an input box."""
    x0, y0, x1, y1 = side
    draw.rounded_rectangle(side, radius=round(8 * scale), fill=(45, 45, 48))
    row = 44 * scale
    y = y0 + 12 * scale
    font = _FONTS.get(12 * scale)
    while y + row < y1 - 60 * scale:
        disc = 28 * scale
        draw.ellipse(
            (x0 + 12 * scale, y, x0 + 12 * scale + disc, y + disc),
            fill=_random_colour(rng, (0.3, 0.8)),
        )
        draw.text(
            (x0 + 50 * scale, y + 4 * scale),
            _made_up_word(rng, 4, 14),
            fill=(220, 220, 220),
            font=font,
        )
        y += row
    draw.rounded_rectangle(
        (x0 + 10 * scale, y1 - 50 * scale, x1 - 10 * scale, y1 - 12 * scale),
        radius=round(6 * scale),
        outline=look.accent,
        width=max(1, round(2 * scale)),
    )


def _draw_taskbar(
    draw: ImageDraw.ImageDraw, screen: Screen, rng: random.Random
) -> None:
    scale = screen.scale
    top = screen.height - TASKBAR * scale
    draw.rectangle((0, top, screen.width, screen.height), fill=(32, 32, 32))
    x = screen.width / 2 - 200 * scale
    for i in range(8):
        draw.rounded_rectangle(
            (x, top + 10 * scale, x + 28 * scale, top + 38 * scale),
            radius=round(4 * scale),
            fill=_random_colour(rng, (0.4, 0.9)),
        )
        if i == 3:
            draw.rectangle(
                (x + 8 * scale, top + 42 * scale, x + 20 * scale, top + 45 * scale),
                fill=(96, 205, 255),
            )
        x += 44 * scale


def _free_regions(window: Box, screen: Screen) -> list[Box]:
    usable = screen.height - TASKBAR * screen.scale
    x0, y0, x1, y1 = window
    regions = [
        (0, 0, x0, usable),
        (x1, 0, screen.width, usable),
        (x0, 0, x1, y0),
        (x0, y1, x1, usable),
    ]
    min_side = 260 * screen.scale
    return [r for r in regions if r[2] - r[0] >= min_side and r[3] - r[1] >= min_side]


def _draw_side_windows(
    draw: ImageDraw.ImageDraw, segment: Segment, ctx: _Context, rng: random.Random
) -> None:
    """Windows beside the meeting, with accents close to the ring's colour."""
    for region in _free_regions(segment.window, ctx.screen):
        kind = rng.choice(("browser", "chat", "sheet", "code"))
        pad = 12 * ctx.screen.scale
        box = (region[0] + pad, region[1] + pad, region[2] - pad, region[3] - pad)
        _draw_app_window(draw, box, kind, ctx, (rng, segment))


def _accent_near_ring(ring: RGB, rng: random.Random, same: float) -> RGB:
    if rng.random() < same:
        return ring
    return _hue_shift(ring, rng.choice((-1, 1)) * rng.uniform(20, 45))


def _draw_app_window(
    draw: ImageDraw.ImageDraw, box: Box, kind: str, ctx: _Context, state: tuple
) -> None:
    rng, segment = state
    scale = ctx.screen.scale
    x0, y0, x1, y1 = box
    dark = kind in ("chat", "code")
    draw.rectangle(box, fill=(30, 30, 30) if dark else (250, 250, 250))
    draw.rectangle(
        (x0, y0, x1, y0 + 34 * scale), fill=(50, 50, 55) if dark else (225, 225, 230)
    )
    accent = _accent_near_ring(ctx.look.ring, rng, 0.35)
    body = (x0 + 8 * scale, y0 + 42 * scale, x1 - 8 * scale, y1 - 8 * scale)
    if kind == "browser":
        _draw_browser(draw, body, ctx, (rng, segment, accent))
    elif kind == "sheet":
        _draw_sheet(draw, body, scale, (rng, segment))
    else:
        _draw_text_lines(draw, body, scale, (rng, accent, dark))
        focus = (
            body[0] + 10 * scale,
            body[1] + 6 * scale,
            body[2] - 10 * scale,
            body[1] + 36 * scale,
        )
        segment.toggles.append(
            Toggle(focus, accent, max(1, round(2 * scale)), round(6 * scale))
        )


def _draw_browser(
    draw: ImageDraw.ImageDraw, body: Box, ctx: _Context, state: tuple
) -> None:
    rng, segment, accent = state
    scale = ctx.screen.scale
    x0, y0, x1, y1 = body
    address = (x0 + 60 * scale, y0, x1 - 60 * scale, y0 + 30 * scale)
    draw.rounded_rectangle(address, radius=round(14 * scale), fill=(238, 238, 242))
    segment.toggles.append(
        Toggle(address, accent, max(1, round(2 * scale)), round(14 * scale))
    )
    draw.rectangle((x0, y0 - 8 * scale, x0 + 120 * scale, y0 - 6 * scale), fill=accent)
    card_w = rng.uniform(150, 320) * scale
    card_h = card_w / rng.uniform(1.3, 1.8)
    y = y0 + 50 * scale
    while y + card_h < y1:
        x = x0 + 10 * scale
        while x + card_w < x1:
            card = (x, y, x + card_w, y + card_h)
            draw.rounded_rectangle(
                card,
                radius=round(8 * scale),
                fill=(255, 255, 255),
                outline=(210, 210, 215),
            )
            draw.rectangle(
                (x + 10 * scale, y + 10 * scale, x + card_w * 0.6, y + 22 * scale),
                fill=(90, 90, 100),
            )
            if rng.random() < 0.2:
                hover = _accent_near_ring(ctx.look.ring, rng, 0.0)
                segment.toggles.append(
                    Toggle(card, hover, max(1, round(2 * scale)), round(8 * scale))
                )
            x += card_w + 16 * scale
        y += card_h + 16 * scale


def _draw_sheet(
    draw: ImageDraw.ImageDraw, body: Box, scale: float, state: tuple
) -> None:
    rng, segment = state
    x0, y0, x1, y1 = body
    cell_w, cell_h = 80 * scale, 22 * scale
    draw.rectangle((x0, y0, x1, y0 + cell_h), fill=(33, 115, 70))
    x = x0
    while x < x1:
        draw.line((x, y0, x, y1), fill=(218, 218, 218), width=1)
        x += cell_w
    y = y0
    while y < y1:
        draw.line((x0, y, x1, y), fill=(218, 218, 218), width=1)
        y += cell_h
    col, row = rng.randint(0, 3), rng.randint(2, 8)
    cell = (
        x0 + col * cell_w,
        y0 + row * cell_h,
        x0 + (col + 1) * cell_w,
        y0 + (row + 1) * cell_h,
    )
    if cell[2] < x1 and cell[3] < y1:
        segment.toggles.append(Toggle(cell, (16, 124, 65), max(1, round(2 * scale)), 0))


def _draw_text_lines(
    draw: ImageDraw.ImageDraw, body: Box, scale: float, state: tuple
) -> None:
    rng, accent, dark = state
    x0, y0, x1, y1 = body
    y = y0 + 50 * scale
    while y < y1 - 14 * scale:
        x = x0 + 10 * scale
        while x < x1 - 60 * scale:
            word = rng.uniform(20, 90) * scale
            roll = rng.random()
            colour = (
                accent
                if roll < 0.15
                else _random_colour(rng, (0.3, 0.9))
                if dark and roll < 0.6
                else ((200, 200, 200) if dark else (60, 60, 60))
            )
            draw.rectangle((x, y, min(x1, x + word), y + 7 * scale), fill=colour)
            x += word + 8 * scale
        y += 20 * scale


def _draw_share(
    draw: ImageDraw.ImageDraw, area: Box, ctx: _Context, rng: random.Random
) -> None:
    """Someone's shared screen: a slide with outlined boxes in bright colours."""
    scale = ctx.screen.scale
    slide = _fit(area, 16 / 9)
    x0, y0, x1, y1 = slide
    light = rng.random() < 0.7
    draw.rectangle(slide, fill=(255, 255, 255) if light else (24, 30, 48))
    w, h = x1 - x0, y1 - y0
    draw.rectangle(
        (x0 + w * 0.06, y0 + h * 0.07, x0 + w * 0.6, y0 + h * 0.13),
        fill=(40, 40, 40) if light else (230, 230, 230),
    )
    for k in range(rng.randint(2, 5)):
        y = y0 + h * (0.22 + 0.08 * k)
        draw.rectangle(
            (x0 + w * 0.08, y, x0 + w * rng.uniform(0.3, 0.55), y + h * 0.025),
            fill=(110, 110, 110),
        )
    boxes = rng.randint(0, 4)
    box_w = w * rng.uniform(0.12, 0.25)
    box_h = box_w / rng.uniform(1.2, 1.9)
    near = rng.random() < 0.3
    colour = (
        _accent_near_ring(ctx.look.ring, rng, 0.0)
        if near
        else _random_colour(rng, (0.6, 1.0))
    )
    for k in range(boxes):
        left = x0 + w * 0.6 + (k % 2) * (box_w + w * 0.02)
        top = y0 + h * 0.25 + (k // 2) * (box_h + h * 0.04)
        if left + box_w < x1 and top + box_h < y1:
            draw.rounded_rectangle(
                (left, top, left + box_w, top + box_h),
                radius=round(6 * scale),
                outline=colour,
                width=max(1, round(rng.choice((1, 2, 3)) * scale)),
            )


def browser_encode(image: Image.Image, spec: MeetingSpec) -> bytes:
    """Shrink to the sampler's long edge and encode as the browser would."""
    return browser_jpeg(browser_shrink(image, spec), spec)


def browser_shrink(image: Image.Image, spec: MeetingSpec) -> Image.Image:
    size = _out_size(Screen(image.width, image.height, 1.0), spec.long_edge)
    if spec.resize == "point":
        return _point_resize(image, size)
    return image.resize(size, Image.Resampling.BILINEAR)


def browser_jpeg(small: Image.Image, spec: MeetingSpec) -> bytes:
    buffer = io.BytesIO()
    # 4:2:0 chroma, as Chrome's encoder uses.
    small.save(buffer, format="JPEG", quality=spec.quality, subsampling=2)
    return buffer.getvalue()


# The sampler's change rule (frontend/src/lib/capture/screenStudy/changeRule.ts).
GRID = (36, 64)
CHANGE_LEVELS = 4
KEEP_ALIVE_TICKS = 15


def change_grid(small: Image.Image) -> np.ndarray:
    """Red, green and blue averages over a 64 x 36 grid of blocks, rounded."""
    pixels = np.asarray(small, dtype=np.float64)
    height, width = pixels.shape[:2]
    rows = np.minimum(GRID[0] - 1, (np.arange(height) * GRID[0]) // height)
    cols = np.minimum(GRID[1] - 1, (np.arange(width) * GRID[1]) // width)
    cell = (rows[:, None] * GRID[1] + cols[None, :]).ravel()
    counts = np.bincount(cell, minlength=GRID[0] * GRID[1])
    means = [
        np.bincount(cell, weights=pixels[..., c].ravel(), minlength=counts.size)
        / counts
        for c in range(3)
    ]
    return np.floor(np.stack(means, axis=1) + 0.5)


class ChangeRule:
    """Keeps a frame when any channel of any block moved more than four levels
    from the last frame kept, or 15 ticks after it."""

    def __init__(self) -> None:
        self.grid: np.ndarray | None = None
        self.tick = 0

    def keep(self, small: Image.Image, tick: int) -> bool:
        grid = change_grid(small)
        if (
            self.grid is None
            or tick - self.tick >= KEEP_ALIVE_TICKS
            or np.abs(grid - self.grid).max() > CHANGE_LEVELS
        ):
            self.grid, self.tick = grid, tick
            return True
        return False


def _point_resize(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Bilinear sampling with no prefilter: one sample per output pixel."""
    src = np.asarray(image, dtype=np.float32)
    height, width = src.shape[:2]
    xs = (np.arange(size[0]) + 0.5) * width / size[0] - 0.5
    ys = (np.arange(size[1]) + 0.5) * height / size[1] - 0.5
    x0 = np.clip(np.floor(xs).astype(int), 0, width - 2)
    y0 = np.clip(np.floor(ys).astype(int), 0, height - 2)
    fx = np.clip(xs - x0, 0, 1)[None, :, None]
    fy = np.clip(ys - y0, 0, 1)[:, None, None]
    top = src[y0][:, x0] * (1 - fx) + src[y0][:, x0 + 1] * fx
    bottom = src[y0 + 1][:, x0] * (1 - fx) + src[y0 + 1][:, x0 + 1] * fx
    out = top * (1 - fy) + bottom * fy
    return Image.fromarray(np.clip(out + 0.5, 0, 255).astype(np.uint8), "RGB")


def decode(jpeg: bytes) -> np.ndarray:
    """A frame as the detector reads it: RGB, uint8, height by width by 3."""
    with Image.open(io.BytesIO(jpeg)) as image:
        return np.asarray(image.convert("RGB"))


def meeting(spec: MeetingSpec) -> list[SyntheticFrame]:
    return list(MeetingRenderer(spec).frames())
