"""Exact-unit image handling and the 1080x1080 graphic.

Two hard rules from the spec are enforced here:

  * The photo must come from the advertised unit's own listing. Stock photography,
    a similar unit, and AI-generated vehicles are all forbidden. If the exact-unit
    image cannot be verified, production stops - there is no fallback image.
  * The final graphic is exactly 1080x1080.
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from .core import Unit, get_logger, money_str

# Every image for a unit is served under .../inventory/<listing-id>/... on the
# dealer CDN. That path segment is what ties a photo to its own listing, and is the
# strongest available evidence that a photo is of the exact advertised unit.
CDN_HOST = "cdnmedia.endeavorsuite.com"
CDN_UNIT_RE = re.compile(r"/inventory/(\d+)/", re.I)
LISTING_ID_RE = re.compile(r"-(\d+)i/?$", re.I)


class ImageError(RuntimeError):
    """Raised when no verifiable exact-unit image is available."""


def listing_id(unit: Unit) -> str:
    """The numeric listing id embedded in the inventory URL (…-12266444i)."""
    match = LISTING_ID_RE.search(unit.inventory_url or "")
    return match.group(1) if match else ""


def image_belongs_to_unit(url: str, unit: Unit) -> bool:
    """True only when the CDN path carries this unit's listing id."""
    if CDN_HOST not in (urlparse(url).netloc or ""):
        return False
    found = CDN_UNIT_RE.search(urlparse(url).path)
    uid = listing_id(unit)
    return bool(found and uid and found.group(1) == uid)


def looks_generated_or_placeholder(url: str, patterns: list[str]) -> str:
    """Returns the matching pattern, or '' when the filename looks legitimate."""
    name = Path(urlparse(url).path).name.lower()
    for pattern in patterns:
        if pattern.lower() in name:
            return pattern
    return ""


def choose_exact_unit_image(unit: Unit, config: dict) -> tuple[str, list[str]]:
    """Picks the best verifiable photo of this exact unit.

    Returns (url, rejection_notes). Raises ImageError when nothing qualifies -
    deliberately, because the alternative is substituting a different motorcycle.
    """
    log = get_logger()
    cfg = config["image"]
    rejected: list[str] = []
    candidates: list[str] = []

    for url in unit.image_urls:
        if not image_belongs_to_unit(url, unit):
            rejected.append(f"{Path(urlparse(url).path).name}: not under this unit's "
                            f"listing id ({listing_id(unit) or 'unknown'})")
            continue
        hit = looks_generated_or_placeholder(url, cfg.get("reject_filename_patterns", []))
        if hit:
            rejected.append(f"{Path(urlparse(url).path).name}: filename matches "
                            f"'{hit}' - looks generated or placeholder, not a unit photo")
            continue
        candidates.append(url)

    if not candidates:
        raise ImageError(
            "No verifiable photo of the exact unit was found on its listing.\n"
            + ("\n".join(f"  - {r}" for r in rejected) if rejected
               else "  - the listing exposed no images at all")
        )

    # Prefer a real photograph over generated artwork: '-first.jpg' style names are
    # the dealer's primary gallery image.
    candidates.sort(key=lambda u: (0 if "first" in u.lower() else 1, len(u)))
    chosen = candidates[0]
    log.info("Exact-unit image: %s", Path(urlparse(chosen).path).name)
    if rejected:
        log.debug("Rejected %d candidate image(s).", len(rejected))
    return chosen, rejected


def download_image(url: str, dest_stem: Path, config: dict) -> Path:
    """Downloads to dest_stem.<ext> and checks it is a usable photograph."""
    import requests
    from PIL import Image

    log = get_logger()
    headers = {"User-Agent": config["inventory"]["user_agent"],
               "Referer": config["business"]["website"]}
    response = requests.get(url, headers=headers, timeout=60)
    if response.status_code != 200:
        raise ImageError(f"Downloading the unit photo failed: HTTP "
                         f"{response.status_code} for {url}")

    ext = (Path(urlparse(url).path).suffix or ".jpg").lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        ext = ".jpg"
    dest = dest_stem.with_suffix(ext)
    dest.write_bytes(response.content)

    try:
        with Image.open(dest) as img:
            img.verify()
        with Image.open(dest) as img:
            width, height = img.size
    except Exception as exc:
        dest.unlink(missing_ok=True)
        raise ImageError(f"The downloaded file is not a readable image: {exc}") from exc

    cfg = config["image"]
    if width < cfg["min_source_width"] or height < cfg["min_source_height"]:
        raise ImageError(
            f"The unit photo is only {width}x{height}, below the "
            f"{cfg['min_source_width']}x{cfg['min_source_height']} minimum. "
            f"A larger photo is needed for a 1080x1080 graphic."
        )

    log.info("Downloaded source image %s (%dx%d)", dest.name, width, height)
    return dest


def image_size(path: Path) -> tuple[int, int]:
    from PIL import Image
    with Image.open(path) as img:
        return img.size


def is_exact_square(path: Path, config: dict) -> bool:
    want = (config["image"]["output_width"], config["image"]["output_height"])
    return image_size(path) == want


# ------------------------------------------------------------ fallback composer

def _load_font(config: dict, size: int):
    from PIL import ImageFont
    for candidate in config["graphic"].get("font_candidates", []):
        try:
            return ImageFont.truetype(candidate, size)
        except Exception:
            continue
    return ImageFont.load_default()


def compose_graphic(unit: Unit, source_image: Path, dest: Path, config: dict) -> Path:
    """Builds a 1080x1080 graphic from the real unit photo and real listing prices.

    This runs only when production.mode = 'produce'. Every number on the graphic
    comes from the listing; nothing is invented. The logo is composited from
    assets/brand/logo.png - if that file is absent the caller is told, rather than
    a substitute being drawn.
    """
    from PIL import Image, ImageDraw

    cfg = config["graphic"]
    icfg = config["image"]
    W, H = icfg["output_width"], icfg["output_height"]
    margin = int(W * cfg["margin_pct"] / 100)

    canvas = Image.new("RGB", (W, H), cfg["background"])
    draw = ImageDraw.Draw(canvas)

    # Unit photo, aspect-preserved, in the upper two thirds.
    photo_box_h = int(H * 0.56)
    with Image.open(source_image) as src:
        photo = src.convert("RGB")
        scale = min((W - 2 * margin) / photo.width, photo_box_h / photo.height)
        new_size = (max(1, int(photo.width * scale)), max(1, int(photo.height * scale)))
        photo = photo.resize(new_size, Image.LANCZOS)
        canvas.paste(photo, ((W - photo.width) // 2, margin + int(H * 0.06)))

    title_font = _load_font(config, 58)
    price_font = _load_font(config, 76)
    small_font = _load_font(config, 34)

    y = margin + int(H * 0.06) + photo_box_h + 10
    draw.text((margin, y), unit.identity.upper(), font=title_font, fill=cfg["text_color"])
    y += 78

    if unit.sale_price is not None:
        draw.text((margin, y), money_str(unit.sale_price), font=price_font,
                  fill=cfg["accent_color"])
    if unit.savings is not None:
        savings_text = f"SAVE {money_str(unit.savings)}"
        draw.text((margin + 420, y + 18), savings_text, font=small_font,
                  fill=cfg["text_color"])
    y += 96

    footer = f"Call or Text {config['business']['phone']}   |   {config['business']['ship_line']}"
    draw.text((margin, H - margin - 40), footer, font=small_font, fill=cfg["text_color"])

    logo_path = (Path(config["graphic"]["logo_path"]))
    if not logo_path.is_absolute():
        from .core import REPO_ROOT
        logo_path = REPO_ROOT / logo_path
    if logo_path.exists():
        with Image.open(logo_path) as logo:
            logo = logo.convert("RGBA")
            target_w = int(W * cfg["logo_width_pct"] / 100)
            ratio = target_w / logo.width
            logo = logo.resize((target_w, int(logo.height * ratio)), Image.LANCZOS)
            canvas.paste(logo, (W - target_w - margin, margin // 2), logo)
    else:
        raise ImageError(
            f"Branding asset missing: {logo_path}\n"
            f"Supply a transparent PNG of the International Motorsports logo "
            f"(about 1000px wide) at that path, or set production.mode to "
            f"'validate_only' so the graphic is supplied instead."
        )

    canvas.save(dest, "PNG")
    get_logger().info("Composed graphic %s (%dx%d)", dest.name, W, H)
    return dest
