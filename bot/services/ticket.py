"""Generate a cinematic, shareable participant ticket (1080x1920, Stories format).

Minimal and emotional: the participant's own car photo as a cinematic poster,
the brand logo on top, a big registration number, a perforated ticket edge, and
one clean line of details + the event date. No QR / no marketing copy — the bot
sends the "share to Stories and tag us" line as a separate text message.

Returns PNG bytes. Rendered with Pillow (DejaVu fonts, Cyrillic + Latin). An
optional real logo (bot/assets/logo.png) is composited on the poster; otherwise
a typographic wordmark is drawn.
"""
from __future__ import annotations

import io
import os
from functools import lru_cache
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

W, H = 1080, 1920
MARGIN = 34
X0, Y0, X1, Y1 = MARGIN, MARGIN, W - MARGIN, H - MARGIN
TEAR_Y = 1560          # poster above, slim info stub below
CORNER = 46

RED = (214, 34, 44)
WHITE = (244, 244, 248)
MUTED = (168, 168, 178)
STUB = (17, 16, 21)
DUO_SHADOW = (12, 8, 14)
DUO_MID = (120, 42, 30)
DUO_HIGH = (255, 176, 96)

_HERE = os.path.dirname(__file__)
_ASSET_FONTS = os.path.join(_HERE, "..", "assets", "fonts")
_LOGO_PATH = os.path.join(_HERE, "..", "assets", "logo.png")

_FONT_CANDIDATES = {
    "bold": ["display.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
             "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"],
    "regular": ["text.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/dejavu/DejaVuSans.ttf"],
    "serif_bold": ["script.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
                   "/usr/share/fonts/dejavu/DejaVuSerif-Bold.ttf"],
}


def _find_font(kind: str) -> Optional[str]:
    for cand in _FONT_CANDIDATES.get(kind, []):
        path = cand if os.path.isabs(cand) else os.path.join(_ASSET_FONTS, cand)
        if os.path.exists(path):
            return path
    return None


@lru_cache(maxsize=64)
def _font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    path = _find_font(kind)
    return ImageFont.truetype(path, size) if path else ImageFont.load_default()


_COPY = {
    "ru": {"participant": "УЧАСТНИК", "date": "Заезд · 11 сентября 2026, 10:00", "place": "SOF EXPO · SAMARKAND"},
    "uz": {"participant": "ISHTIROKCHI", "date": "Kirish · 11-sentyabr 2026, 10:00", "place": "SOF EXPO · SAMARQAND"},
}


# ---------- drawing helpers ----------
def _center(draw, cx, y, text, font, fill):
    w = draw.textlength(text, font=font)
    draw.text((cx - w / 2, y), text, font=font, fill=fill)


def _spaced_center(draw, cx, y, text, font, fill, spacing):
    widths = [draw.textlength(c, font=font) for c in text]
    total = sum(widths) + spacing * max(len(text) - 1, 0)
    x = cx - total / 2
    for c, w in zip(text, widths):
        draw.text((x, y), c, font=font, fill=fill)
        x += w + spacing


def _fit_spaced_center(draw, cx, y, text, kind, start_size, max_w, fill, spacing=2, min_size=16):
    size = start_size
    while size > min_size:
        font = _font(kind, size)
        widths = [draw.textlength(c, font=font) for c in text]
        total = sum(widths) + spacing * max(len(text) - 1, 0)
        if total <= max_w:
            break
        size -= 2
    font = _font(kind, size)
    widths = [draw.textlength(c, font=font) for c in text]
    total = sum(widths) + spacing * max(len(text) - 1, 0)
    x = cx - total / 2
    for c, w in zip(text, widths):
        draw.text((x, y), c, font=font, fill=fill)
        x += w + spacing


def _fit(draw, text, kind, start, max_w, min_size=16):
    size = start
    while size > min_size and draw.textlength(text, font=_font(kind, size)) > max_w:
        size -= 2
    return _font(kind, size)


def _vgradient(w, h, stops):
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    stops = sorted(stops)
    for y in range(h):
        t = y / (h - 1)
        for i in range(len(stops) - 1):
            p0, c0 = stops[i]
            p1, c1 = stops[i + 1]
            if p0 <= t <= p1 or i == len(stops) - 2:
                f = 0 if p1 == p0 else max(0.0, min(1.0, (t - p0) / (p1 - p0)))
                color = tuple(int(c0[k] + (c1[k] - c0[k]) * f) for k in range(3))
                break
        d.line([(0, y), (w, y)], fill=color)
    return img


def _cover(img, w, h):
    img = img.convert("RGB")
    src_r, dst_r = img.width / img.height, w / h
    if src_r > dst_r:
        nw, nh = int(h * src_r), h
    else:
        nw, nh = w, int(w / src_r)
    img = img.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - w) // 2, (nh - h) // 2
    return img.crop((left, top, left + w, top + h))


def _cinematic(img):
    gray = ImageOps.autocontrast(img.convert("L"), cutoff=1)
    duo = ImageOps.colorize(gray, black=DUO_SHADOW, mid=DUO_MID, white=DUO_HIGH)
    return Image.blend(img, duo, 0.8)


def _hero(w, h, photo_path):
    hero = None
    if photo_path and os.path.exists(photo_path):
        try:
            hero = _cinematic(_cover(Image.open(photo_path), w, h))
        except Exception:  # noqa: BLE001 - a bad/corrupt photo falls back to the gradient
            hero = None
    if hero is None:
        hero = _vgradient(w, h, [
            (0.0, (10, 10, 16)), (0.42, (58, 26, 24)),
            (0.60, (196, 74, 28)), (0.75, (140, 50, 26)), (1.0, (8, 6, 8)),
        ])
        glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse([w // 2 - 360, int(h * 0.52), w // 2 + 360, int(h * 0.88)],
                                     fill=(255, 150, 70, 120))
        hero = Image.alpha_composite(hero.convert("RGBA"),
                                     glow.filter(ImageFilter.GaussianBlur(90))).convert("RGB")
    # Darken top (logo) and bottom (number) for legibility.
    ov = Image.new("L", (1, h))
    for y in range(h):
        t = y / (h - 1)
        top = max(0, int(160 * (1 - t / 0.30))) if t < 0.30 else 0
        bot = max(0, int(235 * ((t - 0.45) / 0.55))) if t > 0.45 else 0
        ov.putpixel((0, y), min(240, top + bot))
    black = Image.new("RGB", (w, h), (0, 0, 0))
    return Image.composite(black, hero, ov.resize((w, h)))


def _logo_or_wordmark(content, draw, cx, top):
    if os.path.exists(_LOGO_PATH):
        try:
            logo = Image.open(_LOGO_PATH).convert("RGBA")
            target_w = 640
            logo = logo.resize((target_w, int(logo.height * target_w / logo.width)), Image.LANCZOS)
            content.paste(logo, (cx - logo.width // 2, top), logo)
            return
        except Exception:  # noqa: BLE001
            pass
    _center(draw, cx, top, "PROMOTORS SHOW", _font("bold", 72), WHITE)
    _center(draw, cx, top + 88, "Samarkand", _font("serif_bold", 60), RED)


# ---------- main ----------
def generate_ticket(
    *,
    number: int,
    plate: str,
    direction: str,
    name: str = "",
    lang: str = "ru",
    hero_image_path: Optional[str] = None,
) -> bytes:
    copy = _COPY.get(lang, _COPY["ru"])
    cw, ch = X1 - X0, TEAR_Y - Y0

    content = Image.new("RGB", (W, H), STUB)
    content.paste(_hero(cw, ch, hero_image_path), (X0, Y0))
    draw = ImageDraw.Draw(content)

    # --- logo (top) ---
    _logo_or_wordmark(content, draw, W // 2, Y0 + 96)

    # --- participant label + big number (over the poster) ---
    num = f"№{number}"
    nfont = _fit(draw, num, "bold", 260, cw - 120, min_size=130)
    nw = draw.textlength(num, font=nfont)
    ny = (TEAR_Y - 60) - nfont.size
    _spaced_center(draw, W // 2, ny - 66, copy["participant"], _font("regular", 44), WHITE, 18)
    draw.text((W / 2 - nw / 2 + 5, ny + 5), num, font=nfont, fill=(0, 0, 0))
    draw.text((W / 2 - nw / 2, ny), num, font=nfont, fill=WHITE)

    # --- slim stub: name + details + date ---
    clean_name = name.strip()
    info = f"{plate}  •  {direction}".strip(" •")
    date_line = f"{copy['date']}  •  {copy['place']}"

    if clean_name:
        nfont_stub = _fit(draw, clean_name, "bold", 42, cw - 140, min_size=24)
        _center(draw, W // 2, TEAR_Y + 40, clean_name, nfont_stub, WHITE)

        ifont = _fit(draw, info, "bold", 34, cw - 140, min_size=20)
        _center(draw, W // 2, TEAR_Y + 110, info, ifont, WHITE)

        _fit_spaced_center(draw, W // 2, TEAR_Y + 180, date_line, "regular", 22, cw - 120, MUTED, spacing=1)
    else:
        ifont = _fit(draw, info, "bold", 44, cw - 140, min_size=24)
        _center(draw, W // 2, TEAR_Y + 60, info, ifont, WHITE)
        _fit_spaced_center(draw, W // 2, TEAR_Y + 150, date_line, "regular", 24, cw - 120, MUTED, spacing=1)

    # --- ticket mask: rounded card + tear notches + perforation ---
    mask = Image.new("L", (W, H), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([X0, Y0, X1, Y1], radius=CORNER, fill=255)
    r = 30
    md.ellipse([X0 - r, TEAR_Y - r, X0 + r, TEAR_Y + r], fill=0)
    md.ellipse([X1 - r, TEAR_Y - r, X1 + r, TEAR_Y + r], fill=0)
    for x in range(X0 + 44, X1 - 44, 42):
        md.ellipse([x - 7, TEAR_Y - 7, x + 7, TEAR_Y + 7], fill=0)

    bg = Image.new("RGB", (W, H), (0, 0, 0))
    bg.paste(content, (0, 0), mask)

    out = io.BytesIO()
    bg.save(out, format="PNG")
    return out.getvalue()
