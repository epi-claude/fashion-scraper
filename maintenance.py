"""
maintenance.py — Daily cleanup so old products don't pile up.

  1. Sold out / removed on the store  → archive (hidden, restorable)
  2. Active for ARCHIVE_AFTER_DAYS    → archive
  3. Archived for DELETE_AFTER_DAYS   → delete images + record for good
  4. Archived as sold out, back in stock → restore

Archiving first means a bad day on the store's side can never delete anything
outright; there is always a DELETE_AFTER_DAYS window to restore by hand.
"""

import logging
import os
import threading
import time
from datetime import timedelta
from typing import Optional
from urllib.parse import urlparse

import requests

import catalog
import storage
from scraper import HEADERS, REQUEST_TIMEOUT

log = logging.getLogger(__name__)

ARCHIVE_AFTER_DAYS   = int(os.environ.get("ARCHIVE_AFTER_DAYS", "30"))
DELETE_AFTER_DAYS    = int(os.environ.get("DELETE_AFTER_DAYS", "30"))
MAINTENANCE_HOUR_UTC = int(os.environ.get("MAINTENANCE_HOUR_UTC", "10"))   # ≈ 6am New York
STORE_URL            = "https://www.stepsnewyork.com"
CHECK_DELAY          = 0.5   # seconds between store requests (polite crawling)


def check_available(handle: str, source_url: Optional[str]) -> Optional[bool]:
    """
    Ask the store whether *handle* can still be bought.
    True = in stock, False = sold out or removed, None = couldn't tell (leave it alone).
    """
    parsed = urlparse(source_url or "")
    base = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else STORE_URL
    try:
        # Shopify's .js endpoint includes an overall "available" flag (.json does not).
        resp = requests.get(f"{base}/products/{handle}.js", headers=HEADERS, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        log.warning("Availability check failed for %s: %s", handle, exc)
        return None
    if resp.status_code == 404:
        return False
    if resp.status_code != 200:
        log.warning("Availability check for %s returned %d", handle, resp.status_code)
        return None
    try:
        return bool(resp.json().get("available"))
    except ValueError:
        return None


def run() -> dict:
    """Run one cleanup pass and return a summary of what changed."""
    started = catalog.now()
    stored = storage.list_products()
    with catalog.edit() as data:
        catalog.sync(data, stored)
        records = {h: dict(r) for h, r in data["products"].items()}

    # Decide outside the lock — store checks take a while.
    decisions = {}   # handle → ("archive", reason) | ("restore",) | ("delete",)
    for handle, rec in records.items():
        if rec.get("status") == "archived":
            archived_at = catalog.parse(rec.get("archived_at")) or started
            if started - archived_at >= timedelta(days=DELETE_AFTER_DAYS):
                decisions[handle] = ("delete",)
            elif rec.get("archive_reason") == "sold_out":
                if check_available(handle, rec.get("source_url")) is True:
                    decisions[handle] = ("restore",)
                time.sleep(CHECK_DELAY)
            continue

        since = catalog.active_since(rec) or started
        if started - since >= timedelta(days=ARCHIVE_AFTER_DAYS):
            decisions[handle] = ("archive", "expired")
            continue
        if check_available(handle, rec.get("source_url")) is False:
            decisions[handle] = ("archive", "sold_out")
        time.sleep(CHECK_DELAY)

    summary = {"archived_sold_out": [], "archived_expired": [], "restored": [], "deleted": []}
    for handle, decision in list(decisions.items()):
        if decision[0] == "delete" and not storage.delete_product(handle):
            log.error("Cleanup: could not delete %s", handle)
            del decisions[handle]

    with catalog.edit() as data:
        products = data["products"]
        for handle, decision in decisions.items():
            rec = products.get(handle)
            if rec is None:
                continue
            if decision[0] == "delete":
                del products[handle]
                summary["deleted"].append(handle)
            elif decision[0] == "restore" and rec.get("status") == "archived":
                catalog.restore(rec)
                summary["restored"].append(handle)
            elif decision[0] == "archive" and rec.get("status") == "active":
                catalog.archive(rec, decision[1])
                summary[f"archived_{decision[1]}"].append(handle)
        data["meta"]["last_maintenance"] = catalog.iso(started)
        data["meta"]["maintenance_summary"] = summary

    log.info("Cleanup done in %.0fs: %s", (catalog.now() - started).total_seconds(),
             {k: len(v) for k, v in summary.items()})
    return summary


def _due() -> bool:
    last = catalog.parse(catalog.load()["meta"].get("last_maintenance"))
    now = catalog.now()
    if last is None or now - last > timedelta(hours=26):
        return True
    return now.hour >= MAINTENANCE_HOUR_UTC and last.date() < now.date()


def _loop() -> None:
    time.sleep(60)   # let the app finish starting
    while True:
        try:
            if _due():
                run()
        except Exception:
            log.exception("Scheduled cleanup failed")
        time.sleep(3600)


def start_scheduler() -> None:
    threading.Thread(target=_loop, name="maintenance", daemon=True).start()
    log.info("Daily cleanup scheduled (archive after %dd, delete %dd after archiving, ~%02d:00 UTC)",
             ARCHIVE_AFTER_DAYS, DELETE_AFTER_DAYS, MAINTENANCE_HOUR_UTC)
