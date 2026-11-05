"""Render a small PNG receipt on the control plane. This is not sandbox code."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def text_card(title: str, body: str, path: Path) -> None:
    """Write a paper-colored card with wrapped text."""
    image = Image.new("RGB", (1100, 640), "#f4efe6")
    draw = ImageDraw.Draw(image)
    draw.rectangle((24, 24, 1075, 615), outline="#1a1714", width=4)
    font = _font(28)
    body_font = _font(22)
    draw.text((48, 48), title[:80], fill="#9d2c2c", font=font)
    y = 110
    for line in _wrap(body, 78)[:16]:
        draw.text((48, y), line, fill="#1a1714", font=body_font)
        y += 30
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG")


def _font(size: int) -> ImageFont.ImageFont:
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ):
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def _wrap(text: str, width: int) -> list[str]:
    words = text.replace("\n", " ").split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = word if not current else f"{current} {word}"
        if len(trial) > width:
            if current:
                lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    return lines
