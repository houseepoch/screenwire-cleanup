#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


ROOT = Path(__file__).resolve().parents[1]
BUILD_DIR = ROOT / "build"
ICON_SET_DIR = BUILD_DIR / "icons"


def make_canvas(size: int) -> Image.Image:
    canvas = Image.new("RGBA", (size, size), (7, 11, 20, 255))
    base = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(base)
    inset = int(size * 0.08)
    draw.rounded_rectangle(
        (inset, inset, size - inset, size - inset),
        radius=int(size * 0.23),
        fill=(18, 26, 44, 255),
    )
    glow = base.filter(ImageFilter.GaussianBlur(radius=size * 0.06))
    canvas = Image.alpha_composite(canvas, glow)
    canvas = Image.alpha_composite(canvas, base)

    highlight = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    highlight_draw = ImageDraw.Draw(highlight)
    highlight_draw.ellipse(
        (int(size * 0.18), int(size * 0.10), int(size * 0.82), int(size * 0.52)),
        fill=(146, 228, 255, 60),
    )
    canvas = Image.alpha_composite(canvas, highlight.filter(ImageFilter.GaussianBlur(radius=size * 0.12)))

    accent = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    accent_draw = ImageDraw.Draw(accent)
    accent_draw.rounded_rectangle(
        (int(size * 0.16), int(size * 0.16), int(size * 0.84), int(size * 0.84)),
        radius=int(size * 0.19),
        outline=(146, 228, 255, 215),
        width=max(8, size // 48),
    )
    canvas = Image.alpha_composite(canvas, accent)

    play_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    play_draw = ImageDraw.Draw(play_layer)
    circle_bbox = (int(size * 0.28), int(size * 0.28), int(size * 0.72), int(size * 0.72))
    play_draw.ellipse(circle_bbox, fill=(9, 15, 24, 235), outline=(255, 255, 255, 210), width=max(6, size // 64))
    triangle = [
      (int(size * 0.46), int(size * 0.40)),
      (int(size * 0.46), int(size * 0.60)),
      (int(size * 0.61), int(size * 0.50)),
    ]
    play_draw.polygon(triangle, fill=(150, 230, 255, 255))
    canvas = Image.alpha_composite(canvas, play_layer)
    return canvas


def save_icons() -> None:
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    ICON_SET_DIR.mkdir(parents=True, exist_ok=True)

    master = make_canvas(1024)
    master.save(BUILD_DIR / "icon.png")
    master.save(BUILD_DIR / "icon.icns")
    master.save(
        BUILD_DIR / "icon.ico",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )

    for size in (16, 24, 32, 48, 64, 96, 128, 256, 512):
        master.resize((size, size), Image.Resampling.LANCZOS).save(ICON_SET_DIR / f"{size}x{size}.png")


if __name__ == "__main__":
    save_icons()
