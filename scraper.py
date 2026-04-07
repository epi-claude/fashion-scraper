"""
scraper.py – Core scraping logic for Steps New York (Shopify store).

Strategy (in priority order):
  1. Parse `window.ShopifyAnalytics.meta.product` from inline <script> tags
     → gives the canonical product JSON with all variant images.
  2. Parse <script type="application/ld+json"> blocks
     → gives image[] array from the Product schema.
  3. Fall back to <img> tag scanning inside known Shopify product containers.

All image URLs are upgraded to the highest available resolution by stripping
Shopify's size suffix (e.g. _480x, _800x) from the CDN path.
"""

import json
import logging
import re
import time
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

import r2

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
PRODUCT_URL_PATTERN = re.compile(
    r'https://www\.stepsnewyork\.com'
    r'(?:/collections/[^/\s"\']+)?'
    r'/products/[^/\s"\'<>]+'
)

# Shopify CDN size suffix – strip it to get the master image
SHOPIFY_SIZE_RE = re.compile(r'_(?:\d+x\d*|\d*x\d+)(?=\.[a-zA-Z]+(\?|$))')

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

REQUEST_TIMEOUT = 20          # seconds
DOWNLOAD_DELAY  = 0.5         # seconds between image downloads (polite crawling)


# ── URL helpers ───────────────────────────────────────────────────────────────
def extract_product_urls(text: str) -> List[str]:
    """Return a deduplicated list of Steps NY product URLs found in *text*."""
    found = PRODUCT_URL_PATTERN.findall(text)
    # Deduplicate while preserving order
    seen, unique = set(), []
    for url in found:
        # Normalise: strip trailing punctuation that email clients sometimes add
        url = url.rstrip(".,;:!?\"'")
        if url not in seen:
            seen.add(url)
            unique.append(url)
    log.debug("Extracted URLs: %s", unique)
    return unique


def get_product_handle(url: str) -> str:
    """
    Extract the Shopify product handle from a URL.
    e.g. https://…/products/nike-air-max-90  →  'nike-air-max-90'
    """
    path  = urlparse(url).path          # /products/foo  or  /collections/x/products/foo
    parts = path.rstrip("/").split("/")
    return parts[-1]                    # last segment is always the handle


def upgrade_image_url(url: str) -> str:
    """Remove Shopify size suffix to retrieve the full-resolution master image."""
    return SHOPIFY_SIZE_RE.sub("", url)


# ── Shopify JSON API ──────────────────────────────────────────────────────────
def _images_from_shopify_json_api(handle: str, base_url: str) -> List[str]:
    """
    Strategy 1 (primary): Hit Shopify's built-in product JSON endpoint.
    Every Shopify store exposes /products/<handle>.json — no JS rendering needed.
    Returns all images (front, back, side) at full master resolution.
    """
    api_url = f"{base_url}/products/{handle}.json"
    log.info("Fetching Shopify JSON API: %s", api_url)
    try:
        resp = requests.get(api_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.warning("Shopify JSON API failed: %s", exc)
        return []

    images: List[str] = []
    product = data.get("product", {})

    # images[] has every photo attached to the product
    for img in product.get("images", []):
        src = img.get("src", "")
        if src:
            images.append(upgrade_image_url(src))

    if images:
        log.debug("Shopify JSON API: found %d images", len(images))
    return images


def _images_from_ldjson(soup: BeautifulSoup) -> List[str]:
    """
    Strategy 2 (fallback): Parse <script type="application/ld+json"> Product schema.
    """
    images: List[str] = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue

        items = data if isinstance(data, list) else [data]
        for item in items:
            if item.get("@type") != "Product":
                continue
            raw_images = item.get("image", [])
            if isinstance(raw_images, str):
                raw_images = [raw_images]
            for url in raw_images:
                images.append(upgrade_image_url(url))
            if images:
                break
        if images:
            break

    if images:
        log.debug("ld+json fallback: found %d images", len(images))
    return images


def _images_from_html(soup: BeautifulSoup) -> List[str]:
    """
    Strategy 3 (last resort): Scan <img> tags for Shopify CDN URLs.
    """
    images: List[str] = []
    cdn_re = re.compile(r'//cdn\.shopify\.com/s/files/.+\.(jpg|jpeg|png|webp)', re.I)

    for img in soup.find_all("img"):
        for attr in ("src", "data-src", "data-zoom-image", "data-original"):
            val = img.get(attr, "")
            if cdn_re.search(val):
                if val.startswith("//"):
                    val = "https:" + val
                images.append(upgrade_image_url(val))
                break

    seen, unique = set(), []
    for url in images:
        if url not in seen:
            seen.add(url)
            unique.append(url)

    if unique:
        log.debug("HTML img fallback: found %d images", len(unique))
    return unique


# ── Downloader ────────────────────────────────────────────────────────────────
def download_image(url: str, dest_folder: Path, index: int) -> Optional[Path]:
    """Download a single image into dest_folder. Returns the saved path or None."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, stream=True)
        resp.raise_for_status()

        # Derive extension from Content-Type or URL
        content_type = resp.headers.get("Content-Type", "")
        if "jpeg" in content_type or "jpg" in content_type:
            ext = ".jpg"
        elif "png" in content_type:
            ext = ".png"
        elif "webp" in content_type:
            ext = ".webp"
        else:
            # Guess from URL
            url_path = urlparse(url).path
            ext = Path(url_path).suffix or ".jpg"

        filename = dest_folder / f"image_{index:02d}{ext}"
        with open(filename, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=8192):
                fh.write(chunk)

        log.info("  ✓ Saved %s (%d bytes)", filename.name, filename.stat().st_size)

        if r2.is_configured():
            r2.upload_file(filename, f"{dest_folder.name}/{filename.name}")

        return filename

    except requests.RequestException as exc:
        log.warning("  ✗ Could not download %s: %s", url, exc)
        return None


# ── Main entry point ──────────────────────────────────────────────────────────
def process_product_url(product_url: str, output_root: Path) -> None:
    """
    Full pipeline for one product URL:
      1. Try Shopify JSON API  → fastest, most complete
      2. Fallback: fetch HTML page → try ld+json, then img tags
    """
    handle = get_product_handle(product_url)
    dest   = output_root / handle
    dest.mkdir(parents=True, exist_ok=True)

    parsed   = urlparse(product_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    log.info("Processing product: %s  →  %s", handle, dest)

    # ── Strategy 1: Shopify JSON API (no JS rendering needed) ────────────────
    images = _images_from_shopify_json_api(handle, base_url)

    # ── Strategies 2 & 3: HTML page fallback ─────────────────────────────────
    if not images:
        log.info("JSON API returned no images, falling back to HTML scrape…")
        try:
            resp = requests.get(
                product_url,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )
            resp.raise_for_status()
            soup   = BeautifulSoup(resp.text, "html.parser")
            images = _images_from_ldjson(soup) or _images_from_html(soup)
        except requests.RequestException as exc:
            log.error("Could not fetch page %s: %s", product_url, exc)

    if not images:
        log.warning("No images found for %s — skipping.", product_url)
        return

    log.info("Found %d image(s) for '%s'. Downloading…", len(images), handle)

    for i, img_url in enumerate(images, start=1):
        if not img_url.startswith("http"):
            img_url = "https:" + img_url if img_url.startswith("//") else img_url
        download_image(img_url, dest, i)
        time.sleep(DOWNLOAD_DELAY)

    log.info("Done with '%s'. Images saved to: %s", handle, dest)

