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
import logging
import os
from functools import lru_cache
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from . import assets

logger = logging.getLogger(__name__)

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


def _plate_dark_mark(im: Image.Image, pad: int = 12, radius: int = 10) -> Image.Image:
    """Put a dark logo on a white rounded plate so it reads on the dark poster.

    Brands ship their marks in the version made for light backgrounds (dark
    ink on white). Dropped straight onto the ticket's dark photo they'd be
    invisible, so give them the plate they were designed for.
    """
    w, h = im.size
    plate = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
    mask = Image.new("L", plate.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, plate.width, plate.height], radius=radius, fill=255
    )
    white = Image.new("RGBA", plate.size, (255, 255, 255, 255))
    plate.paste(white, (0, 0), mask)
    plate.paste(im, (pad, pad), im)
    return plate


def _load_brand_logo(name: str, target_h: int, max_w: int):
    """Load a main brand logo (runtime upload wins), cleaned up for dark use.

    Sized by height so the two marks share a baseline regardless of their
    aspect ratios, then capped by width so a very wide mark can't run off the
    card. A dark mark gets a white plate, which is measured as part of the
    final size rather than inflating it.
    """
    path = assets.brand_logo(name)
    if not path:
        return None
    try:
        im = _strip_flat_background(Image.open(path))
        bbox = im.getbbox()
        if bbox:
            im = im.crop(bbox)
        if _is_dark_on_light(im):
            im = _plate_dark_mark(im)
        w = int(im.width * target_h / im.height)
        h = target_h
        if w > max_w:
            w, h = max_w, max(1, int(im.height * max_w / im.width))
        return im.resize((max(1, w), max(1, h)), Image.LANCZOS)
    except Exception:  # noqa: BLE001 - a bad logo file falls back to the wordmark
        logger.exception("Could not load brand logo %s", name)
        return None


def _logo_or_wordmark(content, draw, cx, top):
    # Sized by height so both marks share a baseline.
    sof_logo = _load_brand_logo("logo", target_h=108, max_w=430)
    adr_logo = _load_brand_logo("adrenaline", target_h=96, max_w=330)

    if sof_logo and adr_logo:
        total_w = sof_logo.width + 40 + adr_logo.width
        start_x = cx - total_w // 2
        sof_y = top
        adr_y = top + (sof_logo.height - adr_logo.height) // 2
        content.paste(sof_logo, (start_x, sof_y), sof_logo)
        content.paste(adr_logo, (start_x + sof_logo.width + 40, adr_y), adr_logo)
    elif sof_logo:
        content.paste(sof_logo, (cx - sof_logo.width // 2, top), sof_logo)
    elif adr_logo:
        content.paste(adr_logo, (cx - adr_logo.width // 2, top), adr_logo)
    else:
        _center(draw, cx, top, "PROMOTORS SHOW", _font("bold", 72), WHITE)
        _center(draw, cx, top + 88, "Samarkand", _font("serif_bold", 60), RED)


def _load_sponsor_logos(max_n: int = 10) -> list:
    """Load partner/sponsor logos in filename order.

    Sources, highest priority first: logos uploaded by an admin through the
    bot (stored on the volume) and logos bundled in bot/assets/sponsors/.
    Unreadable files are skipped rather than breaking ticket generation.
    """
    logos = []
    for path in assets.sponsor_files():
        try:
            im = _strip_flat_background(Image.open(path))
            bbox = im.getbbox()
            if bbox:
                im = im.crop(bbox)
            logos.append(im)
        except Exception:  # noqa: BLE001 - one bad file must not break the ticket
            continue
        if len(logos) >= max_n:
            break
    return logos


def _darken_band(content: Image.Image, top: int, bottom: int, strength: int = 205) -> None:
    """Fade a horizontal band towards black, easing out at the bottom edge.

    Used behind the event branding so it stays legible over a bright or busy
    participant photo, without a hard line where the overlay ends.
    """
    top, bottom = max(top, 0), min(bottom, H)
    if bottom <= top:
        return
    h = bottom - top
    band = content.crop((X0, top, X1, bottom))
    mask = Image.new("L", (1, h))
    for i in range(h):
        t = i / max(h - 1, 1)
        # Full strength at the top, fading out over the last third.
        alpha = strength if t < 0.66 else int(strength * (1 - (t - 0.66) / 0.34))
        mask.putpixel((0, i), max(0, min(255, alpha)))
    black = Image.new("RGB", band.size, (0, 0, 0))
    content.paste(Image.composite(black, band, mask.resize(band.size)), (X0, top))


def _strip_flat_background(im: Image.Image, thresh: int = 40) -> Image.Image:
    """Make a logo's flat backdrop transparent so it sits cleanly on dark.

    Partner logos arrive with whatever background the brand's file happens to
    have — white, grey, or already transparent. Pasted as-is on the ticket they
    read as random white/grey blocks. Flood-filling inwards from the corners
    clears only the connected backdrop, so light details *inside* the mark
    (e.g. white text on a black bar) survive, unlike a global colour key.
    """
    im = im.convert("RGBA")
    w, h = im.size
    corners = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]

    # Already transparent at the edges → nothing to do.
    if all(im.getpixel(c)[3] < 16 for c in corners):
        return im

    # Only treat it as a flat backdrop if the corners agree with each other.
    opaque = [im.getpixel(c) for c in corners if im.getpixel(c)[3] > 200]
    if len(opaque) < 3:
        return im
    r0 = sum(p[0] for p in opaque) / len(opaque)
    g0 = sum(p[1] for p in opaque) / len(opaque)
    b0 = sum(p[2] for p in opaque) / len(opaque)
    if any(
        abs(p[0] - r0) > thresh or abs(p[1] - g0) > thresh or abs(p[2] - b0) > thresh
        for p in opaque
    ):
        return im

    original = im.copy()
    try:
        for corner in corners:
            if im.getpixel(corner)[3] > 200:
                ImageDraw.floodfill(im, corner, (0, 0, 0, 0), thresh=thresh)
    except Exception:  # noqa: BLE001 - keep the original on any PIL hiccup
        return original

    # A mark drawn in dark ink (e.g. black lettering on a white plate) would
    # vanish against the black header band once its backdrop is gone. In that
    # case keep the original, so it renders as its own light block — which is
    # exactly how such logos appear on the event's promo artwork.
    if _is_dark_on_light(im):
        return original
    return im


def _is_dark_on_light(im: Image.Image, cutoff: int = 105) -> bool:
    """True if what remains after clearing the backdrop is mostly dark ink."""
    small = im.resize((48, 48), Image.LANCZOS)
    lum, count = 0, 0
    for r, g, b, a in small.getdata():
        if a > 128:
            lum += 0.299 * r + 0.587 * g + 0.114 * b
            count += 1
    if count < 40:  # almost nothing left — treat as unusable, keep the original
        return True
    return (lum / count) < cutoff


def _fit_row(logos, max_bar_w, gap, max_w, start_h=88, min_h=30):
    """Largest height at which this row of logos still fits the card width."""
    def _scale(target_h):
        out = []
        for im in logos:
            w = int(im.width * target_h / im.height)
            h = target_h
            if w > max_w:
                w, h = max_w, int(im.height * max_w / im.width)
            out.append((max(1, w), max(1, h)))
        return out

    target_h = start_h
    while target_h > min_h:
        sizes = _scale(target_h)
        if sum(w for w, _ in sizes) + gap * (len(sizes) - 1) <= max_bar_w:
            break
        target_h -= 4
    return target_h, _scale(target_h)


# Below this a logo stops being readable on a phone screen, so the strip wraps
# to a second row instead of shrinking everything further.
_MIN_LOGO_H = 56


def _draw_sponsor_strip(content, draw, cx, y, logos, max_bar_w=None):
    """Draw the partner logos as a solid header band across the top.

    The band is painted black edge to edge so the logos always read the same,
    whatever photo happens to be behind them — this mirrors the header strip on
    the event's own promo artwork, and avoids the logos looking like stray
    stamps floating over the car photo.

    Logos in a row scale down together so the row always fits the card. With
    enough partners a single row would shrink every mark to an illegible
    sliver, so the strip wraps onto a second row instead. Returns the y just
    below the band (or the input y when there are no logos).
    """
    if not logos:
        return y

    max_bar_w = max_bar_w or (W - 2 * MARGIN - 60)
    gap, max_w, pad_y, row_gap = 40, 230, 26, 20

    rows = [list(logos)]
    height, _ = _fit_row(rows[0], max_bar_w, gap, max_w)
    if height < _MIN_LOGO_H and len(logos) > 2:
        # Split into two balanced rows, keeping the filename order left-to-right,
        # top-to-bottom — so 1_… stays first and 4_… still gets shown.
        half = (len(logos) + 1) // 2
        rows = [list(logos[:half]), list(logos[half:])]

    drawn = []
    for row in rows:
        _, sizes = _fit_row(row, max_bar_w, gap, max_w)
        drawn.append([im.resize(sz, Image.LANCZOS) for im, sz in zip(row, sizes)])

    row_heights = [max(s.height for s in row) for row in drawn]
    band_h = sum(row_heights) + row_gap * (len(drawn) - 1) + pad_y * 2

    # Solid band, full card width, flush with the top edge.
    draw.rectangle([X0, y, X1, y + band_h], fill=(0, 0, 0))

    row_y = y + pad_y
    for scaled, row_h in zip(drawn, row_heights):
        row_w = sum(s.width for s in scaled) + gap * (len(scaled) - 1)
        x = cx - row_w // 2
        for i, s in enumerate(scaled):
            content.paste(s, (x, row_y + (row_h - s.height) // 2), s)
            x += s.width
            if i < len(scaled) - 1:
                div_x = x + gap // 2
                draw.line(
                    [(div_x, row_y + 6), (div_x, row_y + row_h - 6)],
                    fill=(78, 78, 88), width=2,
                )
                x += gap
        row_y += row_h + row_gap

    return y + band_h


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

    # --- partner logo strip across the very top, then the event branding ---
    strip_bottom = _draw_sponsor_strip(content, draw, W // 2, Y0, _load_sponsor_logos())
    has_strip = strip_bottom > Y0
    logo_top = (strip_bottom + 46) if has_strip else (Y0 + 96)

    # The event branding sits over the car photo, which can be bright and busy.
    # Fade the band it occupies to near-black so the marks always read.
    _darken_band(content, Y0 if not has_strip else strip_bottom, logo_top + 190)
    _logo_or_wordmark(content, draw, W // 2, logo_top)

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
