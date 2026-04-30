"""Render the 1920x1080 stream background from static.jpg.

Composes a blurred, "cover"-cropped copy of the source as the backdrop, then
pastes the source image scaled to fit the canvas height in the center. Run once
during setup or whenever static.jpg changes; ffmpeg loops the result statically
so this never runs on the streaming path.

Usage:
    python -m rcr.tools.render_bg              # uses ./static.jpg → ./assets/stream_bg.png
    python -m rcr.tools.render_bg --src foo.jpg --out bar.png
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageFilter

CANVAS_W = 1920
CANVAS_H = 1080
BLUR_RADIUS = 40


def render(src_path: Path, out_path: Path) -> None:
    src = Image.open(src_path).convert("RGB")

    bg = _cover(src, CANVAS_W, CANVAS_H).filter(ImageFilter.GaussianBlur(BLUR_RADIUS))

    fg = _fit_height(src, CANVAS_H)
    fg_x = (CANVAS_W - fg.width) // 2

    canvas = Image.new("RGB", (CANVAS_W, CANVAS_H))
    canvas.paste(bg, (0, 0))
    canvas.paste(fg, (fg_x, 0))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, format="PNG", optimize=True)


def _cover(img: Image.Image, w: int, h: int) -> Image.Image:
    """Scale `img` to fully cover (w, h), then center-crop to exactly (w, h)."""
    src_ratio = img.width / img.height
    dst_ratio = w / h
    if src_ratio < dst_ratio:
        new_w = w
        new_h = round(w / src_ratio)
    else:
        new_h = h
        new_w = round(h * src_ratio)
    scaled = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    left = (new_w - w) // 2
    top = (new_h - h) // 2
    return scaled.crop((left, top, left + w, top + h))


def _fit_height(img: Image.Image, h: int) -> Image.Image:
    new_w = round(img.width * h / img.height)
    return img.resize((new_w, h), Image.Resampling.LANCZOS)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--src", default="static.jpg", type=Path)
    p.add_argument("--out", default="assets/stream_bg.png", type=Path)
    args = p.parse_args()

    render(args.src, args.out)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
