import io
import os
import math
import logging
import tempfile
import requests
from PIL import Image, ImageDraw, ImageFont, ImageFilter

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

# ── Design constants ──────────────────────────────────────────────────────────
BG_COLOR        = (15, 15, 20)          # near-black
ACCENT          = (255, 165, 0)         # amber / auction orange
TEXT_LIGHT      = (240, 240, 240)
TEXT_DIM        = (160, 160, 160)
CARD_BG         = (28, 28, 35)
BORDER_COLOR    = (50, 50, 60)
THUMB_W         = 420
THUMB_H         = 315
COLS            = 3
PADDING         = 14
HEADER_H        = 120
FOOTER_H        = 60
MAX_PHOTOS      = 9                     # 3×3 grid


def download_image(url: str) -> Image.Image | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=12)
        resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content)).convert("RGB")
        return img
    except Exception as e:
        logger.warning(f"Cannot download {url}: {e}")
        return None


def fit_crop(img: Image.Image, w: int, h: int) -> Image.Image:
    """Resize + center-crop to exact dimensions."""
    ratio_w = w / img.width
    ratio_h = h / img.height
    ratio = max(ratio_w, ratio_h)
    new_w = int(img.width * ratio)
    new_h = int(img.height * ratio)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - w) // 2
    top  = (new_h - h) // 2
    return img.crop((left, top, left + w, top + h))


def rounded_rect_mask(w: int, h: int, radius: int = 10) -> Image.Image:
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=255)
    return mask


def create_collage(image_urls: list[str], lot_data: dict) -> str:
    photos_to_use = image_urls[:MAX_PHOTOS]
    images_raw = []
    for url in photos_to_use:
        img = download_image(url)
        if img:
            images_raw.append(img)

    if not images_raw:
        # Create placeholder
        placeholder = Image.new("RGB", (THUMB_W, THUMB_H), (40, 40, 50))
        draw = ImageDraw.Draw(placeholder)
        draw.text((THUMB_W // 2, THUMB_H // 2), "No Image", fill=TEXT_DIM, anchor="mm")
        images_raw = [placeholder]

    n = len(images_raw)
    cols = min(COLS, n)
    rows = math.ceil(n / cols)

    total_w = cols * THUMB_W + (cols + 1) * PADDING
    total_h = HEADER_H + rows * THUMB_H + (rows + 1) * PADDING + FOOTER_H

    canvas = Image.new("RGB", (total_w, total_h), BG_COLOR)
    draw = ImageDraw.Draw(canvas)

    # ── Header ───────────────────────────────────────────────────────────────
    # Accent bar at top
    draw.rectangle([0, 0, total_w, 4], fill=ACCENT)

    # Title
    title = lot_data.get("title", "Автомобіль")
    year  = lot_data.get("year", "")
    full_title = f"{year} {title}".strip() if year not in ("—", "", None) else title

    try:
        font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 26)
        font_med   = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 17)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
    except Exception:
        font_large = font_med = font_small = ImageFont.load_default()

    draw.text((PADDING + 2, 16), full_title, font=font_large, fill=TEXT_LIGHT)

    # Sub-line: VIN + LOT
    vin     = lot_data.get("vin", "—")
    lot_num = lot_data.get("lot_number", "—")
    sub = f"VIN: {vin}   |   Лот: {lot_num}"
    draw.text((PADDING + 2, 50), sub, font=font_med, fill=TEXT_DIM)

    # Bid + damage badges
    bid    = lot_data.get("current_bid", "—")
    damage = lot_data.get("damage", "—")
    badge_x = PADDING + 2
    badge_y = 78

    def badge(txt, x, y, color):
        bbox = draw.textbbox((0, 0), txt, font=font_small)
        bw = bbox[2] - bbox[0] + 20
        bh = bbox[3] - bbox[1] + 10
        draw.rounded_rectangle([x, y, x + bw, y + bh], radius=6, fill=color)
        draw.text((x + 10, y + 5), txt, font=font_small, fill=(10, 10, 10))
        return x + bw + 10

    badge_x = badge(f"💰 {bid}", badge_x, badge_y, ACCENT)
    if damage and damage not in ("—", "— / —"):
        badge(f"🔨 {damage}", badge_x, badge_y, (80, 80, 90))

    # ── Photo grid ───────────────────────────────────────────────────────────
    for idx, img in enumerate(images_raw):
        col = idx % cols
        row = idx // cols
        x = PADDING + col * (THUMB_W + PADDING)
        y = HEADER_H + PADDING + row * (THUMB_H + PADDING)

        thumb = fit_crop(img, THUMB_W, THUMB_H)

        # Rounded mask
        mask = rounded_rect_mask(THUMB_W, THUMB_H, radius=8)
        canvas.paste(thumb, (x, y), mask)

        # Subtle border
        border_layer = Image.new("RGBA", (THUMB_W, THUMB_H), (0, 0, 0, 0))
        bd = ImageDraw.Draw(border_layer)
        bd.rounded_rectangle([0, 0, THUMB_W - 1, THUMB_H - 1], radius=8,
                              outline=BORDER_COLOR, width=2)
        canvas.paste(border_layer, (x, y), border_layer)

        # Photo number badge
        num_txt = f"{idx + 1}/{n}"
        nb_bbox = draw.textbbox((0, 0), num_txt, font=font_small)
        nb_w = nb_bbox[2] - nb_bbox[0] + 12
        nb_h = nb_bbox[3] - nb_bbox[1] + 8
        draw.rounded_rectangle(
            [x + THUMB_W - nb_w - 6, y + 6, x + THUMB_W - 6, y + 6 + nb_h],
            radius=4, fill=(0, 0, 0, 180)
        )
        draw.text((x + THUMB_W - nb_w - 6 + 6, y + 6 + 4), num_txt,
                  font=font_small, fill=TEXT_DIM)

    # ── Footer ───────────────────────────────────────────────────────────────
    footer_y = total_h - FOOTER_H
    draw.rectangle([0, footer_y, total_w, footer_y + 1], fill=(40, 40, 50))

    info_parts = []
    for label, key in [("📍", "location"), ("⚙️", "engine"), ("🛣", "odometer"), ("🎨", "color")]:
        val = lot_data.get(key, "—")
        if val and val not in ("—", "", None):
            info_parts.append(f"{label} {val}")

    footer_text = "   ".join(info_parts)
    draw.text((PADDING, footer_y + (FOOTER_H - 16) // 2), footer_text,
              font=font_small, fill=TEXT_DIM)

    # ── Bottom accent ─────────────────────────────────────────────────────────
    draw.rectangle([0, total_h - 4, total_w, total_h], fill=ACCENT)

    # ── Save ─────────────────────────────────────────────────────────────────
    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    canvas.save(tmp.name, "JPEG", quality=92, optimize=True)
    tmp.close()
    return tmp.name
