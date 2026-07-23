"""Generate a shareable, Instagram-Stories-format (1080x1920) participant ticket.

Rendered with Pillow using DejaVu fonts (Cyrillic + Latin). Returns PNG bytes.
Design goal: dark, premium, automotive; big registration number; QR + a clear
"post to Stories and tag us" call to action.
"""
from __future__ import annotations

import io
import os
from functools import lru_cache
from typing import Optional

import qrcode
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1080, 1920

# Palette
BG_TOP = (10, 10, 16)
BG_BOTTOM = (20, 6, 10)
RED = (214, 34, 44)
RED_SOFT = (170, 30, 40)
WHITE = (245, 245, 248)
MUTED = (150, 150, 160)
CARD = (24, 24, 32)

_FONT_CANDIDATES = {
    "bold": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    ],
    "regular": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    ],
    "serif_bold": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSerif-Bold.ttf",
    ],
}

# Optional project-bundled fonts win if present (e.g. a nicer display face).
_ASSET_FONTS = os.path.join(os.path.dirname(__file__), "..", "assets", "fonts")


def _find_font(kind: str) -> Optional[str]:
    bundled = {
        "bold": "display.ttf",
        "regular": "text.ttf",
        "serif_bold": "script.ttf",
    }.get(kind)
    if bundled:
        p = os.path.join(_ASSET_FONTS, bundled)
        if os.path.exists(p):
            return p
    for p in _FONT_CANDIDATES.get(kind, []):
        if os.path.exists(p):
            return p
    return None


@lru_cache(maxsize=64)
def _font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    path = _find_font(kind)
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default()


# --- localized copy ---
_COPY = {
    "ru": {
        "participant": "УЧАСТНИК",
        "tagline": "Я — участник!",
        "entry": "Заезд · 11 сентября · 10:00–19:00",
        "plate": "ГОС. НОМЕР",
        "direction": "НАПРАВЛЕНИЕ",
        "cta": "Опубликуй в Stories и отметь нас",
        "scan": "Наведи камеру →",
    },
    "uz": {
        "participant": "ISHTIROKCHI",
        "tagline": "Men ishtirokchiman!",
        "entry": "Kirish · 11-sentyabr · 10:00–19:00",
        "plate": "DAVLAT RAQAMI",
        "direction": "YO‘NALISH",
        "cta": "Storiesda ulashing va bizni belgilang",
        "scan": "Kamerani to‘g‘rilang →",
    },
}


def _gradient_bg() -> Image.Image:
    base = Image.new("RGB", (W, H), BG_TOP)
    d = ImageDraw.Draw(base)
    top, bottom = BG_TOP, BG_BOTTOM
    for y in range(H):
        t = y / (H - 1)
        color = (
            int(top[0] + (bottom[0] - top[0]) * t),
            int(top[1] + (bottom[1] - top[1]) * t),
            int(top[2] + (bottom[2] - top[2]) * t),
        )
        d.line([(0, y), (W, y)], fill=color)
    return base


def _radial_glow(center, radius, color, alpha) -> Image.Image:
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(glow)
    d.ellipse(
        [center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius],
        fill=color + (alpha,),
    )
    return glow.filter(ImageFilter.GaussianBlur(radius // 2))


def _text_spaced(draw, xy, text, font, fill, spacing=0, anchor_center=False):
    """Draw text with extra letter spacing. Returns total width."""
    widths = [draw.textlength(ch, font=font) for ch in text]
    total = sum(widths) + spacing * (len(text) - 1 if text else 0)
    x, y = xy
    if anchor_center:
        x -= total / 2
    for ch, w in zip(text, widths):
        draw.text((x, y), ch, font=font, fill=fill)
        x += w + spacing
    return total


def _center(draw, cx, y, text, font, fill):
    w = draw.textlength(text, font=font)
    draw.text((cx - w / 2, y), text, font=font, fill=fill)


def _wrap(draw, text, font, max_w):
    words = text.split()
    lines, cur = [], ""
    for word in words:
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _fit_font(draw, text, kind, start_size, max_w, min_size=60):
    size = start_size
    while size > min_size and draw.textlength(text, font=_font(kind, size)) > max_w:
        size -= 8
    return _font(kind, size)


def _qr_image(data: str, box: int = 10) -> Image.Image:
    qr = qrcode.QRCode(border=1, box_size=box, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(data)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").convert("RGB")


def _chip(draw, cx, y, label, value, lang):
    """Centered rounded chip with a small label above the value."""
    val_font = _font("bold", 46)
    lab_font = _font("regular", 26)
    vw = draw.textlength(value, font=val_font)
    lw = draw.textlength(label, font=lab_font)
    w = int(max(vw, lw) + 80)
    h = 130
    x0 = cx - w // 2
    draw.rounded_rectangle([x0, y, x0 + w, y + h], radius=24, fill=CARD, outline=(60, 60, 72), width=2)
    _center(draw, cx, y + 20, label, lab_font, MUTED)
    _center(draw, cx, y + 58, value, val_font, WHITE)
    return w


def generate_ticket(
    *,
    number: int,
    plate: str,
    direction: str,
    lang: str = "ru",
    instagram_handle: str = "",
    qr_url: str = "",
) -> bytes:
    copy = _COPY.get(lang, _COPY["ru"])

    img = _gradient_bg().convert("RGBA")
    # Red glow behind the number + a cooler glow up top.
    img.alpha_composite(_radial_glow((W // 2, 760), 520, RED, 90))
    img.alpha_composite(_radial_glow((W // 2, 120), 420, (40, 60, 120), 40))
    draw = ImageDraw.Draw(img)

    # Diagonal accent streaks
    for i, off in enumerate((-40, 20)):
        draw.line([(0, 1500 + off), (W, 1360 + off)], fill=(RED if i == 0 else (40, 40, 52)), width=6)

    # --- Brand wordmark ---
    _center(draw, W // 2, 150, "PROMOTORS SHOW", _font("bold", 78), WHITE)
    _center(draw, W // 2, 248, "Samarkand", _font("serif_bold", 64), RED)
    draw.rounded_rectangle([W // 2 - 60, 340, W // 2 + 60, 348], radius=4, fill=RED)

    # --- Participant label + number ---
    _text_spaced(draw, (W // 2, 430), copy["participant"], _font("regular", 40), MUTED,
                 spacing=14, anchor_center=True)

    num = f"№{number}"
    num_font = _fit_font(draw, num, "bold", 340, W - 160, min_size=140)
    # red drop-shadow for depth
    nw = draw.textlength(num, font=num_font)
    ny = 500 + (340 - num_font.size) // 2  # keep vertically centred as it shrinks
    draw.text((W / 2 - nw / 2 + 6, ny + 6), num, font=num_font, fill=(90, 12, 18))
    draw.text((W / 2 - nw / 2, ny), num, font=num_font, fill=WHITE)

    _center(draw, W // 2, 900, copy["tagline"], _font("bold", 58), RED)

    # --- chips: plate + direction ---
    gap = 40
    # measure to place two chips centered as a pair
    tmp = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    val_font = _font("bold", 46)
    lab_font = _font("regular", 26)
    w1 = int(max(tmp.textlength(plate or "—", font=val_font),
                 tmp.textlength(copy["plate"], font=lab_font)) + 80)
    w2 = int(max(tmp.textlength(direction or "—", font=val_font),
                 tmp.textlength(copy["direction"], font=lab_font)) + 80)
    total = w1 + w2 + gap
    c1 = W // 2 - total // 2 + w1 // 2
    c2 = c1 + w1 // 2 + gap + w2 // 2
    _chip(draw, c1, 1030, copy["plate"], plate or "—", lang)
    _chip(draw, c2, 1030, copy["direction"], direction or "—", lang)

    # --- entry info ---
    _center(draw, W // 2, 1210, copy["entry"], _font("regular", 40), WHITE)

    # --- QR + CTA block ---
    qr_data = qr_url or "https://t.me/fooderaexpo"
    qr = _qr_image(qr_data).resize((300, 300), Image.NEAREST)
    qr_card = Image.new("RGB", (340, 340), (255, 255, 255))
    qr_card.paste(qr, (20, 20))
    qr_x, qr_y = 90, 1440
    # white rounded backing
    draw.rounded_rectangle([qr_x - 6, qr_y - 6, qr_x + 346, qr_y + 346], radius=28, fill=WHITE)
    img.paste(qr_card, (qr_x, qr_y))

    cta_x = qr_x + 400
    cta_w = W - cta_x - 55
    cta_font = _font("bold", 46)
    y = 1455
    for line in _wrap(draw, copy["cta"], cta_font, cta_w):
        draw.text((cta_x, y), line, font=cta_font, fill=WHITE)
        y += 58
    if instagram_handle:
        handle = instagram_handle if instagram_handle.startswith("@") else f"@{instagram_handle}"
        hfont = _fit_font(draw, handle, "bold", 50, cta_w, min_size=30)
        draw.text((cta_x, y + 10), handle, font=hfont, fill=RED)
        y += 10 + hfont.size
    draw.text((cta_x, min(y + 16, 1740)), copy["scan"], font=_font("regular", 30), fill=MUTED)

    # --- footer ---
    _center(draw, W // 2, 1850, "PROMOTORS SHOW · SAMARKAND", _font("regular", 28), MUTED)

    out = io.BytesIO()
    img.convert("RGB").save(out, format="PNG")
    return out.getvalue()
