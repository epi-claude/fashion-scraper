"""
storage.py — One interface over the two image stores: Cloudflare R2 when it is
configured, otherwise the local OUTPUT_DIR. Images live at <handle>/<filename>.
"""

import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import r2

log = logging.getLogger(__name__)

OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "downloads"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def valid_name(name: str) -> bool:
    """True for a safe product handle or image filename (no slashes, no '..')."""
    return bool(_NAME_RE.match(name or "")) and ".." not in name


def _is_image(filename: str) -> bool:
    return Path(filename).suffix.lower() in IMAGE_EXTS


# ── Listing ──────────────────────────────────────────────────────────────────
def list_products(handle: str = "") -> Dict[str, List[dict]]:
    """
    Return {handle: [image, …]} with images sorted by filename.
    Each image is {filename, url, size, modified}. Pass *handle* to list one product.
    """
    if r2.is_configured():
        return _list_r2(handle)
    return _list_local(handle)


def _list_r2(handle: str) -> Dict[str, List[dict]]:
    products: Dict[str, List[dict]] = {}
    for obj in r2.list_objects(prefix=f"{handle}/" if handle else ""):
        product, _, filename = obj["key"].partition("/")
        if not valid_name(product) or not valid_name(filename) or not _is_image(filename):
            continue
        products.setdefault(product, []).append({
            "filename": filename,
            "url": r2.object_url(obj["key"]),
            "size": obj["size"],
            "modified": obj["last_modified"],
        })
    for imgs in products.values():
        imgs.sort(key=lambda i: i["filename"])
    return products


def _list_local(handle: str) -> Dict[str, List[dict]]:
    folders = [OUTPUT_DIR / handle] if handle else list(OUTPUT_DIR.iterdir())
    products: Dict[str, List[dict]] = {}
    for folder in folders:
        if not folder.is_dir() or not valid_name(folder.name):
            continue
        imgs = []
        for img in sorted(folder.iterdir()):
            if img.is_file() and _is_image(img.name):
                st = img.stat()
                imgs.append({
                    "filename": img.name,
                    "url": f"/images/{folder.name}/{img.name}",
                    "size": st.st_size,
                    "modified": datetime.fromtimestamp(st.st_mtime, timezone.utc),
                })
        if imgs:
            products[folder.name] = imgs
    return products


def has_product(handle: str) -> bool:
    return bool(list_products(handle))


# ── Reading ──────────────────────────────────────────────────────────────────
def read_image(handle: str, filename: str) -> Optional[bytes]:
    if not (valid_name(handle) and valid_name(filename)):
        return None
    if r2.is_configured():
        return r2.get_object_bytes(f"{handle}/{filename}")
    path = OUTPUT_DIR / handle / filename
    return path.read_bytes() if path.is_file() else None


# ── Deleting ─────────────────────────────────────────────────────────────────
def delete_image(handle: str, filename: str) -> bool:
    if not (valid_name(handle) and valid_name(filename)):
        return False
    ok = True
    if r2.is_configured():
        ok = r2.delete_object(f"{handle}/{filename}")
    path = OUTPUT_DIR / handle / filename
    if path.is_file():
        path.unlink()
    elif not r2.is_configured():
        ok = False
    return ok


def delete_product(handle: str) -> bool:
    if not valid_name(handle):
        return False
    ok = True
    if r2.is_configured():
        ok = r2.delete_folder(handle)
    folder = OUTPUT_DIR / handle
    if folder.is_dir():
        shutil.rmtree(folder)
    log.info("Deleted product folder: %s", handle)
    return ok
