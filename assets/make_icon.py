"""Draw the application icon.

Kept as a script rather than a checked-in binary alone, so the mark can be
retuned without a paint program:

    .venv\\Scripts\\python.exe assets\\make_icon.py

Writes assets/icon.ico (the sizes Windows asks for) and assets/icon.png.

The mark is a stack of wallpapers seen slightly from the side: two cards behind,
one bright card in front. It has to survive 16x16 in a taskbar, so it is three
shapes and one gradient — no arrows, no text, nothing that turns to mush.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent
SIZE = 1024                      # drawn big, downsampled for every real size
SS = 2                           # extra supersampling on top of that

# Matches app/theme.py's accent.
ACCENT_TOP = (108, 156, 255)
ACCENT_BOTTOM = (86, 92, 255)
CARD_BACK_1 = (58, 66, 92)
CARD_BACK_2 = (78, 89, 124)
SHEEN = (255, 255, 255)


def _gradient(size: int, top: tuple, bottom: tuple) -> Image.Image:
    grad = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / max(size - 1, 1)
        grad.putpixel((0, y), tuple(
            round(a + (b - a) * t) for a, b in zip(top, bottom)))
    return grad.resize((size, size))


def _rounded_mask(size: int, box: tuple[float, float, float, float],
                  radius: float) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=radius, fill=255)
    return mask


def draw(size: int) -> Image.Image:
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    unit = size / 100.0

    # Two slivers peeking out behind, each narrower and dimmer than the one in
    # front of it — that is what reads as "a pile of wallpapers". They stay thin
    # so the picture itself keeps almost the whole square at 16x16.
    for (x0, y0, x1, y1), colour, alpha in (
            ((23, 2, 77, 24), CARD_BACK_1, 200),      # narrowest, furthest back
            ((13, 9, 87, 30), CARD_BACK_2, 235)):
        box = (x0 * unit, y0 * unit, x1 * unit, y1 * unit)
        layer = Image.new("RGBA", (size, size), colour + (alpha,))
        canvas = Image.alpha_composite(
            canvas, Image.composite(
                layer, Image.new("RGBA", (size, size), (0, 0, 0, 0)),
                _rounded_mask(size, box, 6 * unit)))

    # The front card carries the colour.
    front = (4 * unit, 16 * unit, 96 * unit, 96 * unit)
    gradient = _gradient(size, ACCENT_TOP, ACCENT_BOTTOM).convert("RGBA")
    canvas = Image.alpha_composite(
        canvas, Image.composite(
            gradient, Image.new("RGBA", (size, size), (0, 0, 0, 0)),
            _rounded_mask(size, front, 13 * unit)))

    # A diagonal sheen, clipped to the front card: enough to read as a picture
    # rather than a plain tile, and it survives being shrunk.
    sheen = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(sheen).polygon(
        [(4 * unit, 82 * unit), (42 * unit, 40 * unit),
         (66 * unit, 64 * unit), (96 * unit, 34 * unit),
         (96 * unit, 96 * unit), (4 * unit, 96 * unit)],
        fill=SHEEN + (58,))
    canvas = Image.alpha_composite(
        canvas, Image.composite(
            sheen, Image.new("RGBA", (size, size), (0, 0, 0, 0)),
            _rounded_mask(size, front, 13 * unit)))

    # One bright dot, the "sun" every wallpaper seems to have.
    dot = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(dot).ellipse(
        (66 * unit, 26 * unit, 84 * unit, 44 * unit), fill=SHEEN + (240,))
    canvas = Image.alpha_composite(
        canvas, Image.composite(
            dot, Image.new("RGBA", (size, size), (0, 0, 0, 0)),
            _rounded_mask(size, front, 13 * unit)))
    return canvas


def main() -> None:
    master = draw(SIZE * SS).resize((SIZE, SIZE), Image.LANCZOS)
    master.save(OUT / "icon.png")
    master.save(OUT / "icon.ico",
                sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])

    # A contact sheet of the real sizes, to check the small ones by eye.
    sizes = (16, 24, 32, 48, 64, 128, 256)
    sheet = Image.new("RGBA", (sum(sizes) + 20 * len(sizes), 280), (22, 24, 29, 255))
    x = 10
    for s in sizes:
        sheet.alpha_composite(master.resize((s, s), Image.LANCZOS), (x, 140 - s // 2))
        x += s + 20
    sheet.save(OUT / "icon_preview.png")
    print(f"wrote {OUT / 'icon.ico'}, {OUT / 'icon.png'}, {OUT / 'icon_preview.png'}")


if __name__ == "__main__":
    main()
