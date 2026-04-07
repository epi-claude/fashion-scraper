"""
Steps New York – Resend Inbound Webhook Image Scraper + Portfolio
"""

import io
import json
import logging
import os
import random
import zipfile
from pathlib import Path
from typing import List, Optional

import requests as http_requests
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import JSONResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from scraper import process_product_url, extract_product_urls
import r2

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────────────────
OUTPUT_DIR         = Path(os.environ.get("OUTPUT_DIR", "downloads"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
RESEND_API_KEY     = os.environ.get("RESEND_API_KEY", "")
NOTIFY_EMAIL       = os.environ.get("NOTIFY_EMAIL", "pjariwala@episolve.com")
PORTFOLIO_BASE_URL = os.environ.get("PORTFOLIO_BASE_URL", "")
PORTFOLIO_PASSCODE = os.environ.get("PORTFOLIO_PASSCODE", "")
IMAGE_EXTS         = {".jpg", ".jpeg", ".png", ".webp"}

NAME_MAP_FILE = OUTPUT_DIR / "name_map.json"

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


def load_name_map() -> dict:
    if r2.is_configured():
        return r2.get_name_map()
    if NAME_MAP_FILE.exists():
        try:
            return json.loads(NAME_MAP_FILE.read_text())
        except Exception:
            pass
    return {}


def save_name_map(name_map: dict) -> None:
    if r2.is_configured():
        r2.put_name_map(name_map)
    else:
        NAME_MAP_FILE.write_text(json.dumps(name_map, indent=2))


def get_or_create_display_name(handle: str) -> str:
    """Return the alias for *handle*, creating one if it doesn't exist yet."""
    name_map = load_name_map()
    if handle in name_map:
        return name_map[handle]
    display_name = f"{random.choice(_FEMALE_NAMES)} {random.choice(_FEMALE_ADJECTIVES)}"
    name_map[handle] = display_name
    save_name_map(name_map)
    log.info("Assigned display name '%s' to handle '%s'", display_name, handle)
    return display_name

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="Steps NY Image Scraper", version="4.0.0")
app.mount("/images", StaticFiles(directory=str(OUTPUT_DIR)), name="images")

# ── Portfolio HTML ────────────────────────────────────────────────────────────
PORTFOLIO_HTML_PATH = Path("portfolio.html")

@app.get("/", response_class=HTMLResponse)
async def portfolio():
    return HTMLResponse(PORTFOLIO_HTML_PATH.read_text())

@app.get("/portfolio", response_class=HTMLResponse)
async def portfolio_alt():
    return HTMLResponse(PORTFOLIO_HTML_PATH.read_text())

# ── Images API ────────────────────────────────────────────────────────────────
@app.get("/api/images")
async def list_images():
    if r2.is_configured():
        return _list_images_r2()
    return _list_images_local()


def _list_images_local():
    folders = sorted(
        [f for f in OUTPUT_DIR.iterdir() if f.is_dir()],
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    newest_product = folders[0].name if folders else None
    images = []
    for folder_idx, folder in enumerate(folders):
        imgs = sorted([f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXTS])
        for img in imgs:
            images.append({
                "product": folder.name,
                "filename": img.name,
                "product_index": folder_idx,
                "url": f"/images/{folder.name}/{img.name}",
            })
    return {"images": images, "newest_product": newest_product, "display_names": load_name_map()}


def _list_images_r2():
    objects = r2.list_objects()
    # Filter to image files only, group by product (top-level folder)
    image_exts = IMAGE_EXTS
    product_objects: dict[str, list] = {}
    for obj in objects:
        parts = obj["key"].split("/", 1)
        if len(parts) != 2:
            continue
        product, filename = parts
        if Path(filename).suffix.lower() not in image_exts:
            continue
        product_objects.setdefault(product, []).append(obj)

    # Sort products newest-first by their most recent object modification time
    sorted_products = sorted(
        product_objects.keys(),
        key=lambda p: max(o["last_modified"] for o in product_objects[p]),
        reverse=True,
    )
    newest_product = sorted_products[0] if sorted_products else None

    images = []
    for folder_idx, product in enumerate(sorted_products):
        for obj in sorted(product_objects[product], key=lambda o: o["key"]):
            filename = obj["key"].split("/", 1)[1]
            images.append({
                "product": product,
                "filename": filename,
                "product_index": folder_idx,
                "url": r2.object_url(obj["key"]),
            })
    return {"images": images, "newest_product": newest_product, "display_names": load_name_map()}


# ── Passcode unlock ───────────────────────────────────────────────────────────
class UnlockRequest(BaseModel):
    passcode: str

@app.post("/api/unlock")
async def unlock(body: UnlockRequest):
    if not PORTFOLIO_PASSCODE:
        return JSONResponse({"ok": True})
    if body.passcode == PORTFOLIO_PASSCODE:
        return JSONResponse({"ok": True})
    return JSONResponse({"ok": False}, status_code=401)


# ── Download: single folder as ZIP ───────────────────────────────────────────
@app.get("/api/download/folder/{product}")
async def download_folder(product: str):
    if r2.is_configured():
        return _download_folder_r2(product)
    return _download_folder_local(product)


def _download_folder_local(product: str):
    folder = OUTPUT_DIR / product
    if not folder.exists() or not folder.is_dir():
        return JSONResponse({"error": "Product not found"}, status_code=404)
    imgs = [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXTS]
    if not imgs:
        return JSONResponse({"error": "No images found"}, status_code=404)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for img in sorted(imgs):
            zf.write(img, arcname=img.name)
    buf.seek(0)
    log.info("ZIP download (local): folder=%s  images=%d", product, len(imgs))
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{product}.zip"'},
    )


def _download_folder_r2(product: str):
    objects = r2.list_objects(prefix=f"{product}/")
    imgs = [o for o in objects if Path(o["key"].split("/", 1)[-1]).suffix.lower() in IMAGE_EXTS]
    if not imgs:
        return JSONResponse({"error": "Product not found"}, status_code=404)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for obj in sorted(imgs, key=lambda o: o["key"]):
            data = r2.get_object_bytes(obj["key"])
            if data:
                filename = obj["key"].split("/", 1)[1]
                zf.writestr(filename, data)
    buf.seek(0)
    log.info("ZIP download (R2): folder=%s  images=%d", product, len(imgs))
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{product}.zip"'},
    )


# ── Download: selected images as ZIP ─────────────────────────────────────────
class SelectedFiles(BaseModel):
    files: List[str]  # list of "product/filename" strings

@app.post("/api/download/selected")
async def download_selected(body: SelectedFiles):
    buf = io.BytesIO()
    added = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for entry in body.files:
            parts = entry.split("/", 1)
            if len(parts) != 2:
                continue
            product, filename = parts
            if r2.is_configured():
                data = r2.get_object_bytes(f"{product}/{filename}")
                if data:
                    zf.writestr(f"{product}/{filename}", data)
                    added += 1
            else:
                img_path = OUTPUT_DIR / product / filename
                if img_path.exists() and img_path.is_file():
                    zf.write(img_path, arcname=f"{product}/{filename}")
                    added += 1
    buf.seek(0)

    log.info("ZIP download: selected=%d files", added)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="steps-ny-selection.zip"'},
    )


# ── Delete: single image ─────────────────────────────────────────────────────
class DeleteImage(BaseModel):
    product: str
    filename: str

@app.delete("/api/delete/image")
async def delete_image(body: DeleteImage):
    if r2.is_configured():
        key = f"{body.product}/{body.filename}"
        if not r2.delete_object(key):
            return JSONResponse({"error": "Delete failed"}, status_code=500)
        # Also remove local copy if present
        img_path = OUTPUT_DIR / body.product / body.filename
        if img_path.exists():
            img_path.unlink()
        log.info("Deleted image (R2): %s", key)
        return JSONResponse({"status": "deleted", "file": body.filename})

    img_path = OUTPUT_DIR / body.product / body.filename
    if not img_path.exists() or not img_path.is_file():
        return JSONResponse({"error": "Image not found"}, status_code=404)
    try:
        img_path.resolve().relative_to(OUTPUT_DIR.resolve())
    except ValueError:
        return JSONResponse({"error": "Invalid path"}, status_code=400)
    img_path.unlink()
    log.info("Deleted image: %s/%s", body.product, body.filename)
    return JSONResponse({"status": "deleted", "file": body.filename})


# ── Delete: entire folder ─────────────────────────────────────────────────────
@app.delete("/api/delete/folder/{product}")
async def delete_folder(product: str):
    import shutil
    if r2.is_configured():
        r2.delete_folder(product)
        # Also clean up local copy if present
        folder = OUTPUT_DIR / product
        if folder.exists():
            shutil.rmtree(folder)
        log.info("Deleted folder (R2): %s", product)
        return JSONResponse({"status": "deleted", "folder": product})

    folder = OUTPUT_DIR / product
    if not folder.exists() or not folder.is_dir():
        return JSONResponse({"error": "Folder not found"}, status_code=404)
    try:
        folder.resolve().relative_to(OUTPUT_DIR.resolve())
    except ValueError:
        return JSONResponse({"error": "Invalid path"}, status_code=400)
    shutil.rmtree(folder)
    log.info("Deleted folder: %s", product)
    return JSONResponse({"status": "deleted", "folder": product})


# ── Send notification email ───────────────────────────────────────────────────
def send_notification_email(product_handle: str) -> None:
    if not RESEND_API_KEY:
        log.warning("RESEND_API_KEY not set — skipping notification email.")
        return

    base_url      = PORTFOLIO_BASE_URL
    portfolio_url = f"{base_url}/?product={product_handle}" if base_url else "/portfolio"
    product_label = product_handle.replace("-", " ").title()

    html_body = f"""
    <div style="font-family:Georgia,serif;max-width:600px;margin:0 auto;">
      <div style="background:#1a1a1a;padding:32px 40px;">
        <h1 style="color:#f5f0e8;font-weight:300;font-size:28px;letter-spacing:0.1em;margin:0;">MARO.SHOPPING</h1>
        <p style="color:#c4a992;font-size:10px;letter-spacing:0.22em;text-transform:uppercase;margin:6px 0 0;">Product Image Portfolio</p>
      </div>
      <div style="background:#8b6f5e;padding:14px 40px;">
        <span style="color:white;font-size:13px;letter-spacing:0.06em;">✦ &nbsp;New product images are ready for you</span>
      </div>
      <div style="background:#f5f0e8;padding:40px;">
        <p style="color:#6b6560;font-size:12px;text-transform:uppercase;letter-spacing:0.14em;margin:0 0 8px;">New arrival</p>
        <h2 style="font-size:28px;font-weight:400;color:#1a1a1a;margin:0 0 20px;font-family:Georgia,serif;">{product_label}</h2>
        <p style="color:#6b6560;font-size:14px;line-height:1.7;margin:0 0 12px;">
          Great news — product images for <strong>{product_label}</strong> have been automatically downloaded and added to your MARO.SHOPPING portfolio.
        </p>
        <p style="color:#6b6560;font-size:14px;line-height:1.7;margin:0 0 32px;">
          You can now view all images, filter by product, download individual photos, or grab the entire folder as a ZIP — perfect for creating your next social media post.
        </p>
        <a href="{portfolio_url}"
           style="display:inline-block;background:#1a1a1a;color:#f5f0e8;padding:15px 36px;
                  text-decoration:none;font-size:12px;letter-spacing:0.14em;text-transform:uppercase;">
          View Portfolio &amp; Download Images →
        </a>
      </div>
      <div style="background:#e8ddd0;padding:20px 40px;border-top:1px solid #d8cdc0;">
        <p style="color:#6b6560;font-size:12px;margin:0 0 6px;">Your portfolio is available at:</p>
        <a href="{portfolio_url}" style="color:#8b6f5e;font-size:12px;word-break:break-all;">{portfolio_url}</a>
      </div>
      <div style="background:#1a1a1a;padding:16px 40px;">
        <p style="color:#6b6560;font-size:11px;margin:0;letter-spacing:0.04em;">This is an automated notification from your MARO.SHOPPING image pipeline.</p>
      </div>
    </div>
    """

    try:
        resp = http_requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": "MARO.SHOPPING Portfolio <onboarding@resend.dev>",
                "to": [NOTIFY_EMAIL],
                "subject": f"New Product Ready: {product_label} — View & Download",
                "html": html_body,
            },
            timeout=15,
        )
        if resp.status_code in (200, 201):
            log.info("Notification email sent to %s for: %s", NOTIFY_EMAIL, product_handle)
        else:
            log.error("Notification email failed: %s %s", resp.status_code, resp.text)
    except Exception as exc:
        log.error("Error sending notification email: %s", exc)


# ── Fetch email body from Resend ──────────────────────────────────────────────
def fetch_email_body(email_id: str) -> str:
    if not RESEND_API_KEY:
        log.error("RESEND_API_KEY not set!")
        return ""

    headers = {
        "Authorization": f"Bearer {RESEND_API_KEY}",
        "Content-Type": "application/json",
    }
    for url in [
        f"https://api.resend.com/emails/receiving/{email_id}",
        f"https://api.resend.com/emails/{email_id}",
    ]:
        try:
            resp = http_requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                text_body = data.get("text", "") or ""
                html_body = data.get("html", "") or ""
                combined  = f"{text_body}\n{html_body}"
                log.info("Fetched email %s — subject: %r  body_length: %d",
                         email_id, data.get("subject"), len(combined))
                return combined
            log.warning("Endpoint %s returned %d: %s", url, resp.status_code, resp.text)
        except Exception as exc:
            log.error("Error calling %s: %s", url, exc)
    return ""


# ── Background task ───────────────────────────────────────────────────────────
def handle_email(payload: dict) -> None:
    data     = payload.get("data", {})
    email_id = data.get("email_id") or data.get("id")

    if not email_id:
        log.warning("No email_id in payload.")
        return

    log.info("Processing email_id: %s", email_id)
    body = fetch_email_body(email_id)
    if not body:
        log.warning("Empty body for email_id: %s", email_id)
        return

    urls = extract_product_urls(body)
    if not urls:
        log.info("No Steps NY URLs found.")
        return

    log.info("Found %d product URL(s).", len(urls))

    for url in urls:
        try:
            handle = url.rstrip("/").split("/")[-1]
            folder = OUTPUT_DIR / handle
            already_existed = folder.exists()

            process_product_url(url, OUTPUT_DIR)

            if not already_existed and folder.exists():
                get_or_create_display_name(handle)
                send_notification_email(handle)
        except Exception as exc:
            log.error("Failed processing %s: %s", url, exc, exc_info=True)


# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/webhook/resend")
async def resend_webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    event_type = payload.get("type", "unknown")
    log.info("Webhook received — type: %r", event_type)

    if event_type == "email.received":
        background_tasks.add_task(handle_email, payload)
    else:
        log.info("Ignoring event type: %s", event_type)

    return JSONResponse({"status": "accepted"}, status_code=200)

