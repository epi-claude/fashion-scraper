"""
catalog.py — Product records kept beside the images (R2 bucket or OUTPUT_DIR).

    products: handle → {
        display_name, added_at, source_url, sender,
        status: "active" | "archived",
        archived_at, archive_reason: "expired" | "sold_out" | "manual",
        restored_at,
    }
    meta: last_maintenance, maintenance_summary, processed_email_ids, notifications

Timestamps are ISO-8601 strings in UTC.
"""

import hashlib
import json
import logging
import os
import random
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Dict, List, Optional

import r2
from storage import OUTPUT_DIR

log = logging.getLogger(__name__)

LOCAL_FILE = OUTPUT_DIR / "products.json"
LOCAL_NAME_MAP = OUTPUT_DIR / "name_map.json"
# The R2 bucket is publicly readable, so keep the records (which include
# sender addresses) at a key nobody can guess.
R2_KEY = os.environ.get("CATALOG_KEY") or (
    "_meta/" + hashlib.sha256(r2.R2_SECRET_ACCESS_KEY.encode()).hexdigest()[:24] + "/products.json"
)

MAX_PROCESSED_IDS = 200
MAX_NOTIFICATIONS = 20

_lock = threading.RLock()

# Pool of female names and feminine adjectives for product aliases
_FEMALE_NAMES = [
    "Alessa", "Amara", "Aria", "Aurora", "Ava", "Bianca", "Camille",
    "Celeste", "Chloe", "Clara", "Daphne", "Elena", "Elise", "Emma",
    "Fiona", "Gabrielle", "Isla", "Ivy", "Jade", "Juliet", "Lara",
    "Layla", "Luna", "Mia", "Nadia", "Natalia", "Nina", "Olivia",
    "Petra", "Phoebe", "Rosa", "Sabrina", "Serena", "Simone", "Sofia",
    "Stella", "Valentina", "Vera", "Violet", "Zara", "Zoe",
]
_FEMALE_ADJECTIVES = [
    "Blossom", "Breeze", "Cashmere", "Chic", "Classic", "Crystal",
    "Dainty", "Delicate", "Dreamy", "Elegant", "Floral", "Graceful",
    "Golden", "Ivory", "Lace", "Luxe", "Midnight", "Misty", "Pearl",
    "Radiant", "Satin", "Sheer", "Silk", "Soft", "Velvet", "Whisper",
]


# ── Time helpers ─────────────────────────────────────────────────────────────
def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


# ── Load / save ──────────────────────────────────────────────────────────────
def load() -> dict:
    """Return the whole catalog. Raises if storage can't be read."""
    with _lock:
        if r2.is_configured():
            data = r2.get_json(R2_KEY)
        else:
            data = json.loads(LOCAL_FILE.read_text()) if LOCAL_FILE.exists() else None
        if data is None:
            data = _migrate_from_name_map()
        data.setdefault("products", {})
        data.setdefault("meta", {})
        return data


def save(data: dict) -> None:
    with _lock:
        if r2.is_configured():
            if not r2.put_json(R2_KEY, data):
                raise RuntimeError("Could not save product records to R2")
        else:
            tmp = LOCAL_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2))
            tmp.replace(LOCAL_FILE)


@contextmanager
def edit():
    """Load, let the caller modify, then save — under the in-process lock."""
    with _lock:
        data = load()
        yield data
        save(data)


def _migrate_from_name_map() -> dict:
    """First run: seed records from the old name_map.json aliases."""
    if r2.is_configured():
        name_map = r2.get_name_map()
    elif LOCAL_NAME_MAP.exists():
        name_map = json.loads(LOCAL_NAME_MAP.read_text())
    else:
        name_map = {}
    if name_map:
        log.info("Seeding product records from name_map.json (%d aliases)", len(name_map))
    return {
        "products": {h: {"display_name": n, "status": "active"} for h, n in name_map.items()},
        "meta": {},
    }


# ── Records ──────────────────────────────────────────────────────────────────
def new_display_name() -> str:
    return f"{random.choice(_FEMALE_NAMES)} {random.choice(_FEMALE_ADJECTIVES)}"


def sync(data: dict, stored: Dict[str, List[dict]]) -> bool:
    """
    Make records match what is actually in storage: add a record for any
    product folder without one (dated by its oldest image) and drop records
    whose images are gone. Returns True if anything changed.
    """
    products = data["products"]
    changed = False
    for handle, imgs in stored.items():
        rec = products.setdefault(handle, {})
        if not rec.get("display_name"):
            rec["display_name"] = new_display_name()
            changed = True
        if not rec.get("added_at"):
            rec["added_at"] = iso(min(i["modified"] for i in imgs))
            changed = True
        if not rec.get("status"):
            rec["status"] = "active"
            changed = True
    for handle in [h for h in products if h not in stored]:
        del products[handle]
        changed = True
    return changed


def archive(rec: dict, reason: str) -> None:
    rec.update(status="archived", archived_at=iso(now()), archive_reason=reason)


def restore(rec: dict) -> None:
    rec.update(status="active", archived_at=None, archive_reason=None, restored_at=iso(now()))


def active_since(rec: dict) -> Optional[datetime]:
    """When the age clock started: the later of added and last restored."""
    dates = [d for d in (parse(rec.get("added_at")), parse(rec.get("restored_at"))) if d]
    return max(dates) if dates else None


# ── Meta ─────────────────────────────────────────────────────────────────────
def mark_email_processed(email_id: str) -> bool:
    """Record *email_id*; return False if it was already processed (a webhook retry)."""
    with edit() as data:
        ids = data["meta"].setdefault("processed_email_ids", [])
        if email_id in ids:
            return False
        ids.append(email_id)
        del ids[:-MAX_PROCESSED_IDS]
        return True


def log_notification(entry: dict) -> None:
    try:
        with edit() as data:
            log_ = data["meta"].setdefault("notifications", [])
            log_.append({"at": iso(now()), **entry})
            del log_[:-MAX_NOTIFICATIONS]
    except Exception as exc:
        log.error("Could not record notification: %s", exc)
