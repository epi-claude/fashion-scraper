"""
Steps New York – Resend Inbound Webhook Image Scraper + Portfolio
"""

import asyncio
import base64
import hashlib
import hmac
import io
import json
import logging
import os
import time
import zipfile
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import List

import requests as http_requests
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import JSONResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from scraper import process_product_url, extract_product_urls, get_product_handle
from storage import OUTPUT_DIR
import catalog
import maintenance
import notify
import storage

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────────────────
RESEND_API_KEY        = os.environ.get("RESEND_API_KEY", "")
RESEND_WEBHOOK_SECRET = os.environ.get("RESEND_WEBHOOK_SECRET", "")
PORTFOLIO_PASSCODE    = os.environ.get("PORTFOLIO_PASSCODE", "")
# Optional allowlist: "a@b.com,@trusted-domain.com". Empty = accept any sender.
ALLOWED_SENDERS       = [s.strip().lower() for s in os.environ.get("ALLOWED_SENDERS", "").split(",") if s.strip()]

AUTH_COOKIE = "maro_auth"
OPEN_PATHS  = {"/", "/portfolio", "/health", "/webhook/resend", "/api/unlock"}

if not notify.PORTFOLIO_BASE_URL:
    log.warning("PORTFOLIO_BASE_URL is not set — notification emails will not be sent.")
if not RESEND_WEBHOOK_SECRET:
    log.warning("RESEND_WEBHOOK_SECRET is not set — webhook signatures are NOT being checked.")


# ── App ───────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    maintenance.start_scheduler()
    yield

app = FastAPI(title="Steps NY Image Scraper", version="5.0.0", lifespan=lifespan)
app.mount("/images", StaticFiles(directory=str(OUTPUT_DIR)), name="images")


# ── Passcode (enforced on the server, not just in the page) ──────────────────
def _auth_token() -> str:
    return hmac.new(PORTFOLIO_PASSCODE.encode(), b"maro-portfolio", hashlib.sha256).hexdigest()


def _is_unlocked(request: Request) -> bool:
    return not PORTFOLIO_PASSCODE or hmac.compare_digest(request.cookies.get(AUTH_COOKIE, ""), _auth_token())


@app.middleware("http")
async def require_passcode(request: Request, call_next):
    if request.url.path not in OPEN_PATHS and not _is_unlocked(request):
        return JSONResponse({"error": "Locked"}, status_code=401)
    return await call_next(request)


class UnlockRequest(BaseModel):
    passcode: str

@app.post("/api/unlock")
async def unlock(body: UnlockRequest, request: Request):
    """Empty passcode = "am I already unlocked?" check from the page."""
    if _is_unlocked(request):
        return JSONResponse({"ok": True})
    if body.passcode and hmac.compare_digest(body.passcode, PORTFOLIO_PASSCODE):
        resp = JSONResponse({"ok": True})
        https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
        resp.set_cookie(AUTH_COOKIE, _auth_token(), max_age=30 * 86400,
                        httponly=True, samesite="lax", secure=https)
        return resp
    if body.passcode:
        await asyncio.sleep(1)   # slow down guessing
    return JSONResponse({"ok": False}, status_code=401)


# ── Portfolio HTML ────────────────────────────────────────────────────────────
PORTFOLIO_HTML_PATH = Path("portfolio.html")

@app.get("/", response_class=HTMLResponse)
async def portfolio():
    return HTMLResponse(PORTFOLIO_HTML_PATH.read_text())

@app.get("/portfolio", response_class=HTMLResponse)
async def portfolio_alt():
    return HTMLResponse(PORTFOLIO_HTML_PATH.read_text())


def _bad_name(*names: str):
    if all(storage.valid_name(n) for n in names):
        return None
    return JSONResponse({"error": "Invalid name"}, status_code=400)


def product_item(handle: str, rec: dict, imgs: List[dict]) -> dict:
    """Summary of one product used by the page and the emails."""
    return {
        "handle": handle,
        "display_name": rec.get("display_name") or handle,
        "image_count": len(imgs),
        "thumb_url": imgs[0]["url"] if imgs else "",
    }


# ── Images API ────────────────────────────────────────────────────────────────
@app.get("/api/images")
def list_images():
    stored = storage.list_products()
    data = catalog.load()
    if catalog.sync(data, stored):
        with catalog.edit() as data:
            catalog.sync(data, stored)

    recs = data["products"]
    handles = sorted(stored, key=lambda h: recs[h].get("added_at", ""), reverse=True)

    products, images, folder_sizes = [], [], {}
    for idx, handle in enumerate(handles):
        rec, imgs = recs[handle], stored[handle]
        size = sum(i["size"] for i in imgs)
        folder_sizes[handle] = size
        item = product_item(handle, rec, imgs)
        item.update(
            added_at=rec.get("added_at"),
            status=rec.get("status", "active"),
            archive_reason=rec.get("archive_reason"),
            size=size,
        )
        if item["status"] == "archived":
            archived_at = catalog.parse(rec.get("archived_at"))
            if archived_at:
                item["delete_after"] = catalog.iso(archived_at + timedelta(days=maintenance.DELETE_AFTER_DAYS))
        else:
            since = catalog.active_since(rec)
            if since:
                item["archive_after"] = catalog.iso(since + timedelta(days=maintenance.ARCHIVE_AFTER_DAYS))
        products.append(item)
        for img in imgs:
            images.append({
                "product": handle,
                "filename": img["filename"],
                "product_index": idx,
                "url": img["url"],
                "size": img["size"],
            })

    notifications = data["meta"].get("notifications") or []
    active = [p["handle"] for p in products if p["status"] == "active"]
    return {
        "products": products,
        "images": images,
        "newest_product": active[0] if active else None,
        "display_names": {h: r.get("display_name", h) for h, r in recs.items()},
        "folder_sizes": folder_sizes,
        "total_size": sum(folder_sizes.values()),
        "last_notification": notifications[-1] if notifications else None,
        "last_maintenance": data["meta"].get("last_maintenance"),
        "settings": {
            "archive_after_days": maintenance.ARCHIVE_AFTER_DAYS,
            "delete_after_days": maintenance.DELETE_AFTER_DAYS,
        },
    }


# ── Download: single folder as ZIP ───────────────────────────────────────────
def _zip_response(entries, filename: str):
    """entries: iterable of (arcname, bytes)."""
    buf = io.BytesIO()
    added = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, data in entries:
            if data:
                zf.writestr(arcname, data)
                added += 1
    buf.seek(0)
    log.info("ZIP download: %s  images=%d", filename, added)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/download/folder/{product}")
def download_folder(product: str):
    if err := _bad_name(product):
        return err
    imgs = storage.list_products(product).get(product)
    if not imgs:
        return JSONResponse({"error": "Product not found"}, status_code=404)
    return _zip_response(
        ((i["filename"], storage.read_image(product, i["filename"])) for i in imgs),
        f"{product}.zip",
    )


# ── Download: selected images as ZIP ─────────────────────────────────────────
class SelectedFiles(BaseModel):
    files: List[str]  # list of "product/filename" strings

def _split_entries(entries: List[str]):
    for entry in entries:
        product, _, filename = entry.partition("/")
        if storage.valid_name(product) and storage.valid_name(filename):
            yield product, filename

@app.post("/api/download/selected")
def download_selected(body: SelectedFiles):
    return _zip_response(
        ((f"{p}/{f}", storage.read_image(p, f)) for p, f in _split_entries(body.files)),
        "maro-shopping-selection.zip",
    )


# ── Delete ───────────────────────────────────────────────────────────────────
class DeleteImage(BaseModel):
    product: str
    filename: str

@app.delete("/api/delete/image")
def delete_image(body: DeleteImage):
    if err := _bad_name(body.product, body.filename):
        return err
    if not storage.delete_image(body.product, body.filename):
        return JSONResponse({"error": "Delete failed"}, status_code=500)
    log.info("Deleted image: %s/%s", body.product, body.filename)
    return JSONResponse({"status": "deleted", "file": body.filename})


@app.delete("/api/delete/images")
def delete_images(body: SelectedFiles):
    deleted = [f"{p}/{f}" for p, f in _split_entries(body.files) if storage.delete_image(p, f)]
    log.info("Deleted %d selected image(s)", len(deleted))
    return JSONResponse({"status": "deleted", "deleted": deleted})


@app.delete("/api/delete/folder/{product}")
def delete_folder(product: str):
    if err := _bad_name(product):
        return err
    if not storage.has_product(product):
        return JSONResponse({"error": "Folder not found"}, status_code=404)
    if not storage.delete_product(product):
        return JSONResponse({"error": "Delete failed"}, status_code=500)
    with catalog.edit() as data:
        data["products"].pop(product, None)
    return JSONResponse({"status": "deleted", "folder": product})


# ── Archive / restore ────────────────────────────────────────────────────────
@app.post("/api/archive/{product}")
def archive_product(product: str):
    with catalog.edit() as data:
        rec = data["products"].get(product)
        if rec is None:
            return JSONResponse({"error": "Product not found"}, status_code=404)
        catalog.archive(rec, "manual")
    log.info("Archived: %s", product)
    return JSONResponse({"status": "archived", "product": product})


@app.post("/api/restore/{product}")
def restore_product(product: str):
    with catalog.edit() as data:
        rec = data["products"].get(product)
        if rec is None:
            return JSONResponse({"error": "Product not found"}, status_code=404)
        catalog.restore(rec)
    log.info("Restored: %s", product)
    return JSONResponse({"status": "active", "product": product})


@app.post("/api/maintenance/run")
def run_maintenance():
    """Run the daily cleanup now instead of waiting for the schedule."""
    return JSONResponse(maintenance.run())


# ── Manual notification trigger ──────────────────────────────────────────────
@app.post("/api/notify/{product}")
def manual_notify(product: str):
    """Re-send the arrivals email for one product (e.g. if the automatic one failed)."""
    rec = catalog.load()["products"].get(product)
    imgs = storage.list_products(product).get(product) if storage.valid_name(product) else None
    if rec is None or not imgs:
        return JSONResponse({"error": "Product not found"}, status_code=404)
    ok = notify.send_arrivals([product_item(product, rec, imgs)], [])
    return JSONResponse({"status": "sent" if ok else "failed", "product": product, "to": notify.NOTIFY_EMAILS},
                        status_code=200 if ok else 502)


# ── Inbound email ────────────────────────────────────────────────────────────
def verify_signature(headers, body: bytes) -> bool:
    """Check Resend's (Svix) webhook signature against RESEND_WEBHOOK_SECRET."""
    msg_id, timestamp, signatures = (headers.get(h, "") for h in ("svix-id", "svix-timestamp", "svix-signature"))
    if not (msg_id and timestamp and signatures):
        return False
    try:
        if abs(time.time() - int(timestamp)) > 300:
            return False
        key = base64.b64decode(RESEND_WEBHOOK_SECRET.removeprefix("whsec_"))
    except ValueError:
        return False
    signed = f"{msg_id}.{timestamp}.".encode() + body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    return any(
        version == "v1" and hmac.compare_digest(sig, expected)
        for version, _, sig in (part.partition(",") for part in signatures.split())
    )


def sender_allowed(sender: str) -> bool:
    if not ALLOWED_SENDERS:
        return True
    domain = "@" + sender.rpartition("@")[2]
    return any(entry in (sender, domain, domain[1:]) for entry in ALLOWED_SENDERS)


def fetch_email(email_id: str) -> dict:
    """Return the received email from Resend ({} on failure)."""
    if not RESEND_API_KEY:
        log.error("RESEND_API_KEY not set!")
        return {}
    headers = {"Authorization": f"Bearer {RESEND_API_KEY}"}
    for url in [
        f"https://api.resend.com/emails/receiving/{email_id}",
        f"https://api.resend.com/emails/{email_id}",
    ]:
        try:
            resp = http_requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                log.info("Fetched email %s — subject: %r", email_id, data.get("subject"))
                return data
            log.warning("Endpoint %s returned %d: %s", url, resp.status_code, resp.text)
        except Exception as exc:
            log.error("Error calling %s: %s", url, exc)
    return {}


def handle_email(payload: dict) -> None:
    data     = payload.get("data", {})
    email_id = data.get("email_id") or data.get("id")
    if not email_id:
        log.warning("No email_id in payload.")
        return
    if not catalog.mark_email_processed(email_id):
        log.info("Email %s already processed — ignoring duplicate webhook.", email_id)
        return

    log.info("Processing email_id: %s", email_id)
    email  = fetch_email(email_id)
    sender = notify.bare_address(email.get("from") or data.get("from"))
    if notify.is_own_address(sender):
        log.info("Ignoring our own notification email coming back in.")
        return
    if not sender_allowed(sender):
        log.warning("Ignoring email from %r — not in ALLOWED_SENDERS.", sender)
        return

    body = f"{email.get('text') or ''}\n{email.get('html') or ''}"
    urls = extract_product_urls(body)
    if not urls:
        # No reply here on purpose: auto-replies would otherwise bounce back and forth.
        log.info("No Steps NY URLs found in email from %r.", sender)
        return
    log.info("Found %d product URL(s) from %r.", len(urls), sender)

    added, existing, failed = [], [], []
    for url in urls:
        handle = get_product_handle(url)
        try:
            if not storage.valid_name(handle):
                raise ValueError(f"unexpected product handle {handle!r}")
            before = catalog.load()["products"].get(handle)
            was_active = bool(before) and before.get("status") == "active" and storage.has_product(handle)

            process_product_url(url, OUTPUT_DIR)
            imgs = storage.list_products(handle).get(handle)
            if not imgs:
                failed.append(url)
                continue

            with catalog.edit() as cat:
                rec = cat["products"].setdefault(handle, {})
                rec.setdefault("display_name", catalog.new_display_name())
                rec.update(source_url=url, sender=sender)
                if not was_active:
                    # New, or re-sent after being archived: treat as a fresh arrival.
                    rec.update(added_at=catalog.iso(catalog.now()), status="active",
                               archived_at=None, archive_reason=None, restored_at=None)
            (existing if was_active else added).append(product_item(handle, rec, imgs))
        except Exception as exc:
            log.error("Failed processing %s: %s", url, exc, exc_info=True)
            failed.append(url)

    if added:
        notify.send_arrivals(added, failed)
    # The sender already gets the arrivals email if they're a receiver — don't send twice.
    if sender and not (added and notify.is_receiver(sender)):
        notify.send_confirmation(sender, added, existing, failed)


# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/webhook/resend")
async def resend_webhook(request: Request, background_tasks: BackgroundTasks):
    body = await request.body()
    if RESEND_WEBHOOK_SECRET and not verify_signature(request.headers, body):
        log.warning("Rejected webhook with a bad or missing signature.")
        return JSONResponse({"error": "Invalid signature"}, status_code=401)
    try:
        payload = json.loads(body)
    except ValueError:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    event_type = payload.get("type", "unknown")
    log.info("Webhook received — type: %r", event_type)

    if event_type == "email.received":
        background_tasks.add_task(handle_email, payload)
    else:
        log.info("Ignoring event type: %s", event_type)

    return JSONResponse({"status": "accepted"}, status_code=200)
