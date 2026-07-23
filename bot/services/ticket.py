"""Generate a cinematic, shareable participant ticket (1080x1920, Stories format).

Movie-ticket aesthetic: a cinematic poster (the participant's own car photo,
colour-graded) on top, a perforated tear line, and a ticket "stub" with the
details + QR below. Returns PNG bytes. Rendered with Pillow (DejaVu fonts,
Cyrillic + Latin). An optional real logo (bot/assets/logo.png) is composited on
top of the poster; otherwise a typographic wordmark is drawn.
"""
from __future__ import annotations

import io
import os
from functools import lru_cache
from typing import Optional

import qrcode
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

W, H = 1080, 1920
MARGIN = 34
X0, Y0, X1, Y1 = MARGIN, MARGIN, W - MARGIN, H - MARGIN
TEAR_Y = 1140          # poster above, stub below
CORNER = 46

# Palette
RED = (214, 34, 44)
WHITE = (244, 244, 248)
MUTED = (150, 150, 162)
STUB = (18, 17, 22)
STUB_LINE = (44, 43, 52)
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
    "ru": {
        "ticket": "PROMOTORS SHOW · ORIGINAL TICKET",
        "participant": "УЧАСТНИК",
        "plate": "ГОС. НОМЕР",
        "direction": "НАПРАВЛЕНИЕ",
        "entry": "ЗАЕЗД",
        "entry_val": "11 сентября · 10:00–19:00",
        "cta": "Опубликуй в Stories и отметь нас",
        "footer": "SNAP · POST · TAG",
    },
    "uz": {
        "ticket": "PROMOTORS SHOW · ORIGINAL TICKET",
        "participant": "ISHTIROKCHI",
        "plate": "DAVLAT RAQAMI",
        "direction": "YO‘NALISH",
        "entry": "KIRISH",
        "entry_val": "11-sentyabr · 10:00–19:00",
        "cta": "Storiesda ulashing va bizni belgilang",
        "footer": "SNAP · POST · TAG",
    },
}


# ---------- drawing helpers ----------
def _text(draw, xy, text, font, fill):
    draw.text(xy, text, font=font, fill=fill)


def _center(draw, cx, y, text, font, fill):
    w = draw.textlength(text, font=font)
    draw.text((cx - w / 2, y), text, font=font, fill=fill)


def _spaced(draw, xy, text, font, fill, spacing, center_x=None):
    widths = [draw.textlength(c, font=font) for c in text]
    total = sum(widths) + spacing * max(len(text) - 1, 0)
    x, y = xy
    if center_x is not None:
        x = center_x - total / 2
    for c, w in zip(text, widths):
        draw.text((x, y), c, font=font, fill=fill)
        x += w + spacing
    return total


def _wrap(draw, text, font, max_w):
    words, lines, cur = text.split(), [], ""
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


def _fit(draw, text, kind, start, max_w, min_size=90):
    size = start
    while size > min_size and draw.textlength(text, font=_font(kind, size)) > max_w:
        size -= 8
    return _font(kind, size)


def _vgradient(w, h, stops):
    """stops: list of (pos 0..1, (r,g,b)). Returns an RGB image."""
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    stops = sorted(stops)
    for y in range(h):
        t = y / (h - 1)
        for i in range(len(stops) - 1):
            p0, c0 = stops[i]
            p1, c1 = stops[i + 1]
            if p0 <= t <= p1 or i == len(stops) - 2:
                f = 0 if p1 == p0 else (t - p0) / (p1 - p0)
                f = max(0.0, min(1.0, f))
                color = tuple(int(c0[k] + (c1[k] - c0[k]) * f) for k in range(3))
                break
        d.line([(0, y), (w, y)], fill=color)
    return img


def _cover(img, w, h):
    img = img.convert("RGB")
    src_r, dst_r = img.width / img.height, w / h
    if src_r > dst_r:
        nh = h
        nw = int(h * src_r)
    else:
        nw = w
        nh = int(w / src_r)
    img = img.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - w) // 2, (nh - h) // 2
    return img.crop((left, top, left + w, top + h))


def _cinematic(img):
    """Duotone colour-grade so any car photo looks like a movie poster."""
    gray = ImageOps.autocontrast(img.convert("L"), cutoff=1)
    duo = ImageOps.colorize(gray, black=DUO_SHADOW, mid=DUO_MID, white=DUO_HIGH)
    return Image.blend(img, duo, 0.82)


def _hero(w, h, photo_path):
    if photo_path and os.path.exists(photo_path):
        hero = _cinematic(_cover(Image.open(photo_path), w, h))
    else:
        # Placeholder cinematic sunset (production uses the participant's car).
        hero = _vgradient(w, h, [
            (0.0, (10, 10, 16)), (0.40, (60, 26, 24)),
            (0.60, (198, 74, 28)), (0.74, (150, 52, 26)), (1.0, (8, 6, 8)),
        ])
        glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse([w // 2 - 360, int(h * 0.55), w // 2 + 360, int(h * 0.9)],
                                     fill=(255, 150, 70, 120))
        hero = Image.alpha_composite(hero.convert("RGBA"), glow.filter(ImageFilter.GaussianBlur(90))).convert("RGB")
    # Darken top and bottom for legibility.
    ov = Image.new("L", (1, h))
    for y in range(h):
        t = y / (h - 1)
        top = max(0, int(150 * (1 - t / 0.35))) if t < 0.35 else 0
        bot = max(0, int(220 * ((t - 0.5) / 0.5))) if t > 0.5 else 0
        ov.putpixel((0, y), min(235, top + bot))
    mask = ov.resize((w, h))
    black = Image.new("RGB", (w, h), (0, 0, 0))
    return Image.composite(black, hero, mask)


def _qr(data, size):
    qr = qrcode.QRCode(border=1, box_size=10, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    return img.resize((size, size), Image.NEAREST)


def _logo_or_wordmark(content, draw, cx, top):
    if os.path.exists(_LOGO_PATH):
        try:
            logo = Image.open(_LOGO_PATH).convert("RGBA")
            target_w = 620
            ratio = target_w / logo.width
            logo = logo.resize((target_w, int(logo.height * ratio)), Image.LANCZOS)
            content.paste(logo, (cx - logo.width // 2, top), logo)
            return
        except Exception:  # noqa: BLE001 - fall back to the wordmark
            pass
    _center(draw, cx, top, "PROMOTORS SHOW", _font("bold", 72), WHITE)
    _center(draw, cx, top + 90, "Samarkand", _font("serif_bold", 60), RED)


# ---------- main ----------
def generate_ticket(
    *,
    number: int,
    plate: str,
    direction: str,
    lang: str = "ru",
    instagram_handle: str = "",
    qr_url: str = "",
    hero_image_path: Optional[str] = None,
) -> bytes:
    copy = _COPY.get(lang, _COPY["ru"])
    cw, ch = X1 - X0, TEAR_Y - Y0

    content = Image.new("RGB", (W, H), STUB)
    content.paste(_hero(cw, ch, hero_image_path), (X0, Y0))
    draw = ImageDraw.Draw(content)

    # --- top bar over the poster ---
    _spaced(draw, (X0 + 34, Y0 + 34), copy["ticket"], _font("regular", 22), (220, 220, 226), 2)
    _text(draw, (X1 - 110, Y0 + 32), "(2025)", _font("regular", 26), (220, 220, 226))

    # --- logo / wordmark ---
    _logo_or_wordmark(content, draw, W // 2, Y0 + 120)

    # --- participant + big number over poster bottom ---
    num = f"№{number}"
    nfont = _fit(draw, num, "bold", 236, cw - 120, min_size=120)
    nw = draw.textlength(num, font=nfont)
    ny = (TEAR_Y - 26) - nfont.size
    _spaced(draw, (0, ny - 58), copy["participant"], _font("regular", 38), (235, 235, 240), 15,
            center_x=W // 2)
    draw.text((W / 2 - nw / 2 + 5, ny + 5), num, font=nfont, fill=(0, 0, 0))
    draw.text((W / 2 - nw / 2, ny), num, font=nfont, fill=WHITE)

    # --- stub: detail rows ---
    lab_f, val_f = _font("regular", 30), _font("bold", 50)
    rows = [
        (copy["plate"], plate or "—"),
        (copy["direction"], direction or "—"),
        (copy["entry"], copy["entry_val"]),
    ]
    ry = TEAR_Y + 54
    for label, value in rows:
        _text(draw, (X0 + 40, ry), label, lab_f, MUTED)
        _text(draw, (X0 + 40, ry + 34), value, val_f, WHITE)
        ry += 106
        draw.line([(X0 + 40, ry - 18), (X1 - 40, ry - 18)], fill=STUB_LINE, width=2)

    # --- stub: QR + CTA ---
    qr_size = 210
    qr_y = ry + 20
    qr_url = qr_url or "https://t.me/fooderaexpo"
    card = Image.new("RGB", (qr_size + 28, qr_size + 28), WHITE)
    card.paste(_qr(qr_url, qr_size), (14, 14))
    content.paste(card, (X0 + 40, qr_y))

    cta_x = X0 + 40 + qr_size + 56
    cta_w = X1 - 40 - cta_x
    cy = qr_y + 4
    for line in _wrap(draw, copy["cta"], _font("bold", 42), cta_w):
        _text(draw, (cta_x, cy), line, _font("bold", 42), WHITE)
        cy += 52
    if instagram_handle:
        handle = instagram_handle if instagram_handle.startswith("@") else f"@{instagram_handle}"
        hf = _fit(draw, handle, "bold", 46, cta_w, min_size=28)
        _text(draw, (cta_x, cy + 8), handle, hf, RED)

    # --- footer ---
    _spaced(draw, (0, Y1 - 60), copy["footer"], _font("bold", 30), MUTED, 8, center_x=W // 2)

    # --- tear line: dashed line under the number is already the poster/stub seam;
    # draw perforation + notches via the mask below and a subtle seam line ---
    draw.line([(X0 + 40, TEAR_Y), (X1 - 40, TEAR_Y)], fill=(60, 58, 68), width=2)

    # --- ticket mask: rounded card, side notches, perforation holes ---
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
