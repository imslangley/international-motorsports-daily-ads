"""Reads the live International Motorsports inventory.

The site returns 403 to plain HTTP clients (verified with requests: 403 on both the
search page and a unit page), so a real browser is required. Playwright driving the
installed Chrome is the tested-working combination.

Nothing here fabricates data. A field the listing does not show comes back empty and
the validator treats that as a blocker.
"""
from __future__ import annotations

import html
import re

from .core import Unit, get_logger, parse_money, utcnow_iso

# Pulls one row per unit out of the search results page. Grouping is by the unit's
# own URL because the markup nests several presentational wrappers per card, and
# selecting on those returns the same unit repeatedly.
SEARCH_JS = r"""
() => {
  const out = [];
  const seen = new Set();
  document.querySelectorAll('a[href*="/inventory/"]').forEach(a => {
    const url = (a.href || '').split('?')[0];
    if (!/\d+i\/?$/.test(url) || seen.has(url)) return;

    let card = a;
    for (let i = 0; i < 10 && card; i++) {
      if (card.querySelector && card.querySelector('.result-saleprice')) break;
      card = card.parentElement;
    }
    if (!card || !card.querySelector('.result-saleprice')) return;
    seen.add(url);

    const text = sel => {
      const n = card.querySelector(sel);
      return n ? n.innerText.trim().replace(/\s+/g, ' ') : '';
    };
    const images = [...card.querySelectorAll('img')]
      .map(i => i.currentSrc || i.src || i.getAttribute('data-src') || '')
      .concat([...card.querySelectorAll('a[href*="cdnmedia"]')].map(x => x.href))
      .filter(s => s && s.startsWith('http') && /cdnmedia\.endeavorsuite\.com/.test(s));

    out.push({
      inventory_url: url,
      title_raw: text('h2, h3, [class*=title], [class*=name]'),
      sale_raw: text('.result-saleprice, .display-price-box'),
      save_raw: text('.sale-regular-price, .muted-price-box'),
      image_urls: [...new Set(images)].slice(0, 8),
    });
  });
  return out;
}
"""

# The unit page publishes schema.org Product JSON-LD, which is authoritative and far
# steadier than reading rendered text: it carries the name, brand, sku (the stock
# number), mpn (the listing id), the offer price, the in-stock flag, and the exact
# unit's image list. Breadcrumbs and the Carfax badge request fill the last gaps.
DETAIL_JS = r"""
() => {
  const ld = [...document.querySelectorAll('script[type="application/ld+json"]')]
    .map(s => s.textContent);

  const crumbs = [...document.querySelectorAll('[class*=breadcrumb] a, [class*=breadcrumb] li')]
    .map(e => e.innerText.trim()).filter(Boolean);

  // The Carfax badge request carries the VIN even when the page never prints it.
  let vin = '';
  const m = document.documentElement.innerHTML.match(/[?&]Vin=([A-HJ-NPR-Z0-9]{17})/i);
  if (m) vin = m[1];

  const domImages = [...document.querySelectorAll('img, a[href*="cdnmedia"]')]
    .map(e => e.currentSrc || e.src || e.href || '')
    .filter(s => /cdnmedia\.endeavorsuite\.com/i.test(s) && /\.(jpe?g|png|webp)/i.test(s));

  return {
    ld_json: ld,
    breadcrumbs: [...new Set(crumbs)],
    vin: vin,
    page_title: document.title || '',
    sale_raw: (document.querySelector('.result-saleprice, [class*=saleprice]') || {}).innerText || '',
    save_raw: (document.querySelector('.sale-regular-price, [class*=regular-price]') || {}).innerText || '',
    dom_images: [...new Set(domImages)].slice(0, 12),
  };
}
"""


class InventoryError(RuntimeError):
    """Raised when the live inventory cannot be read at all."""


def _parse_title(title: str) -> tuple[str, str, str]:
    """'2024LXPMV Agusta' -> ('2024', 'MV Agusta', 'LXP').

    The search card concatenates year, model and make without separators, so this is
    only a hint. The detail page is authoritative and overrides whatever is here.
    """
    if not title:
        return "", "", ""
    cleaned = re.sub(r"\s+", " ", title).strip()
    year_match = re.match(r"^((?:19|20)\d{2})\s*(.*)$", cleaned)
    if not year_match:
        return "", "", cleaned
    return year_match.group(1), "", year_match.group(2).strip()


def _apply_prices(unit: Unit, sale_raw: str, save_raw: str) -> None:
    sale = parse_money(sale_raw)
    savings = parse_money(save_raw)
    if sale is not None:
        unit.sale_price = sale
    if savings is not None:
        unit.savings = savings
    if unit.sale_price is not None and unit.savings is not None:
        unit.regular_price = unit.sale_price + unit.savings


def fetch_units(config: dict, limit: int | None = None, detail: bool = True) -> list[Unit]:
    """Returns units in the site's own order, highest discount first.

    `detail` visits each unit page for stock number, VIN, colour and full images.
    That is a page load per unit, so callers pass a small limit.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - environment problem, not logic
        raise InventoryError(
            "Playwright is not installed. Run:\n"
            "  python -m pip install -r requirements.txt\n"
            "  python -m playwright install chromium"
        ) from exc

    log = get_logger()
    inv = config["inventory"]
    limit = limit or inv.get("max_units", 60)
    units: list[Unit] = []

    with sync_playwright() as pw:
        launch: dict = {"headless": bool(inv.get("headless", False))}
        if inv.get("browser_channel"):
            launch["channel"] = inv["browser_channel"]
        try:
            browser = pw.chromium.launch(**launch)
        except Exception as exc:
            raise InventoryError(
                f"Could not start the browser ({launch}).\n"
                f"If Chrome is not installed, set inventory.browser_channel to null in "
                f"config/config.json to use the bundled Chromium.\n{exc}"
            ) from exc

        try:
            ctx = browser.new_context(
                user_agent=inv["user_agent"],
                locale=inv.get("locale", "en-CA"),
                viewport={"width": 1440, "height": 1600},
            )
            page = ctx.new_page()

            # The site rate-limits bursts with a 403. A daily run will normally sail
            # through, but a retried or re-run job can trip it, so back off and retry
            # rather than failing the morning's ad.
            attempts = int(inv.get("retry_attempts", 4))
            backoff = int(inv.get("retry_backoff_ms", 20000))
            status = 0
            for attempt in range(1, attempts + 1):
                log.info("Loading inventory%s: %s",
                         f" (attempt {attempt}/{attempts})" if attempt > 1 else "",
                         inv["search_url"])
                response = page.goto(inv["search_url"], wait_until="domcontentloaded",
                                     timeout=inv["nav_timeout_ms"])
                status = response.status if response else 0
                if status == 200:
                    break
                if attempt < attempts:
                    wait_ms = backoff * attempt
                    log.warning("HTTP %s from the inventory page - backing off %ds.",
                                status, wait_ms // 1000)
                    page.wait_for_timeout(wait_ms)

            if status != 200:
                raise InventoryError(
                    f"The inventory page returned HTTP {status} after {attempts} "
                    f"attempts. The site rate-limits automated access - wait a few "
                    f"minutes and retry. If it persists, raise "
                    f"inventory.retry_backoff_ms in config/config.json."
                )

            try:
                # state="attached", not the default "visible": the results markup
                # renders several view variants (grid/list) and hides all but one,
                # so the price nodes are in the DOM while most are display:none.
                page.wait_for_selector(".result-saleprice", state="attached",
                                       timeout=inv["results_timeout_ms"])
            except Exception as exc:
                raise InventoryError(
                    "The inventory page loaded but no priced results appeared within "
                    f"{inv['results_timeout_ms']}ms. The site markup may have changed - "
                    "check the '.result-saleprice' selector in src/ims_ads/inventory.py."
                ) from exc

            # Scroll so lazy-loaded images resolve to real URLs.
            for _ in range(int(inv.get("scroll_passes", 6))):
                page.mouse.wheel(0, 2500)
                page.wait_for_timeout(500)
            page.wait_for_timeout(int(inv.get("settle_ms", 3000)))

            rows = page.evaluate(SEARCH_JS)
            log.info("Found %d priced unit(s) on the search page.", len(rows))

            for row in rows[:limit]:
                year, make, model = _parse_title(row.get("title_raw", ""))
                unit = Unit(
                    inventory_url=row["inventory_url"],
                    year=year, make=make, model=model,
                    title_raw=row.get("title_raw", ""),
                    image_urls=list(row.get("image_urls") or []),
                    scraped_at=utcnow_iso(),
                )
                _apply_prices(unit, row.get("sale_raw", ""), row.get("save_raw", ""))
                units.append(unit)

            if detail:
                for unit in units:
                    try:
                        enrich_unit(browser, unit, inv)
                    except Exception as exc:
                        # A unit we cannot fully read is left incomplete on purpose;
                        # validation rejects it rather than the run dying here.
                        log.warning("Could not read detail for %s: %s",
                                    unit.inventory_url, exc)
        finally:
            browser.close()

    return units


def _find_product(ld_blobs: list[str]) -> dict:
    """Returns the schema.org Product object, or {} when the page has none."""
    import json
    for blob in ld_blobs or []:
        try:
            data = json.loads(blob)
        except (json.JSONDecodeError, TypeError):
            continue
        for item in (data if isinstance(data, list) else [data]):
            if isinstance(item, dict) and item.get("@type") == "Product":
                return item
    return {}


def _absolute(url: str) -> str:
    """The JSON-LD image list is protocol-relative (//cdnmedia...)."""
    if url.startswith("//"):
        return "https:" + url
    return url


def enrich_unit(browser, unit: Unit, inv: dict) -> None:
    """Visits the unit's own page and fills in the authoritative fields.

    The detail page overrides the search card: the search markup concatenates year,
    model and make without separators ('2024LXPMV Agusta'), so those values are only
    ever a hint.

    Each unit is opened in a FRESH browser context. That is not tidiness - the site
    only emits the schema.org Product JSON-LD on a clean session. Reusing the context
    that loaded the search page returns a variant with no structured data at all, and
    every field silently comes back empty.
    """
    log = get_logger()
    log.debug("Reading unit page: %s", unit.inventory_url)

    ctx = browser.new_context(
        user_agent=inv["user_agent"],
        locale=inv.get("locale", "en-CA"),
        viewport={"width": 1440, "height": 1200},
    )
    try:
        page = ctx.new_page()
        page.goto(unit.inventory_url, wait_until="domcontentloaded",
                  timeout=inv["nav_timeout_ms"])
        try:
            page.wait_for_selector('script[type="application/ld+json"]',
                                   state="attached",
                                   timeout=int(inv.get("detail_timeout_ms", 20000)))
        except Exception:
            log.debug("  no JSON-LD on %s; falling back to DOM extraction",
                      unit.inventory_url)
        page.wait_for_timeout(int(inv.get("settle_ms", 3000)))
        data = page.evaluate(DETAIL_JS)
    finally:
        ctx.close()

    if data.get("vin"):
        unit.vin = data["vin"]

    product = _find_product(data.get("ld_json"))
    log.debug("  JSON-LD product: %s | sku=%r", bool(product), product.get("sku"))
    if product:
        # The feed carries HTML entities in brand and model names
        # ('Zero&#8482; Motorcycles'), which must not reach a caption or a filename.
        name = html.unescape((product.get("name") or "")).strip()
        brand = product.get("brand") or {}
        make = (brand.get("name") if isinstance(brand, dict) else str(brand)) or ""
        make = html.unescape(make)

        if make:
            unit.make = make.strip()
        if product.get("sku"):
            unit.stock_number = html.unescape(str(product["sku"])).strip()
        if product.get("url"):
            unit.inventory_url = product["url"]

        year_match = re.match(r"^((?:19|20)\d{2})\b\s*(.*)$", name)
        if year_match:
            unit.year = year_match.group(1)
            remainder = year_match.group(2)
            if unit.make and remainder.lower().startswith(unit.make.lower()):
                remainder = remainder[len(unit.make):].strip()
            if remainder:
                unit.model = remainder
        unit.title_raw = name or unit.title_raw

        offers = product.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        if isinstance(offers, dict):
            price = offers.get("price")
            if price not in (None, ""):
                try:
                    unit.sale_price = float(str(price).replace(",", ""))
                except ValueError:
                    pass
            unit.availability = str(offers.get("availability") or "")

        images = [_absolute(u) for u in (product.get("image") or []) if u]
        if images:
            unit.image_urls = list(dict.fromkeys(images))

    # Trim: whatever the stock number says beyond the year and model - but only when
    # that leftover is actually a trim name. Many stock numbers are pure serials
    # ('784658'), and recording one as the trim would put it in the ad copy.
    if unit.stock_number and not unit.trim:
        leftover = unit.stock_number
        for token in (unit.year, unit.model or ""):
            if token:
                leftover = re.sub(re.escape(token), " ", leftover, flags=re.I)
        leftover = re.sub(r"\s+", " ", leftover).strip(" -_")
        is_wordy = bool(re.search(r"[A-Za-z]{3,}", leftover))
        if leftover and is_wordy and leftover.lower() != (unit.model or "").lower():
            unit.trim = leftover.title()

    # Breadcrumbs are the fallback for model when JSON-LD is absent.
    if not unit.model:
        crumbs = [c for c in (data.get("breadcrumbs") or [])
                  if c.lower() not in {"home", "inventory"}]
        if crumbs:
            unit.model = html.unescape(crumbs[-1])

    _apply_prices(unit, data.get("sale_raw", ""), data.get("save_raw", ""))

    if not unit.image_urls:
        unit.image_urls = [u for u in (data.get("dom_images") or []) if u]
