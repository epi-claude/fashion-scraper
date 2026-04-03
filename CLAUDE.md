# CLAUDE.md — Steps NY Fashion Scraper

## What this project does

Automated pipeline that listens for inbound emails via a **Resend webhook**, extracts Steps New York product URLs from the email body, scrapes all product images from the Shopify store, saves them locally, and sends a notification email with a link to a portfolio viewer.

The app is branded as **MARO.SHOPPING** in emails and the portfolio UI.

## Architecture

```
Resend inbound email
  → POST /webhook/resend
    → background task: extract URLs → scrape images → send notification email
```

**Two services run on the host (systemd):**
- `fashion-scraper` — uvicorn/FastAPI on port 8000
- `cloudflared` — Cloudflare Tunnel exposing port 8000 publicly (dynamic `*.trycloudflare.com` URL)

## Key files

| File | Purpose |
|------|---------|
| `main.py` | FastAPI app — webhook handler, portfolio/download/delete routes, email notification |
| `scraper.py` | Scraping logic — Shopify JSON API → ld+json → HTML img fallback |
| `portfolio.html` | Static portfolio viewer (served at `/` and `/portfolio`) |
| `downloads/` | Image output dir — one subfolder per product handle |
| `fashion-scraper.service` | systemd unit for the FastAPI app |
| `cloudflared.service` | systemd unit for the Cloudflare tunnel |
| `setup-services.sh` | One-time setup: installs services, registers Resend webhook |

## Environment variables

| Variable | Default | Notes |
|----------|---------|-------|
| `RESEND_API_KEY` | — | Required for webhook auth and sending emails |
| `NOTIFY_EMAIL` | `pjariwala@episolve.com` | Recipient for new-product notifications |
| `PORTFOLIO_BASE_URL` | — | Fallback if tunnel URL can't be read from journald |

## Image scraping strategy (scraper.py)

1. **Shopify JSON API** (`/products/<handle>.json`) — primary, no JS rendering needed, returns all variant images at full resolution
2. **ld+json Product schema** — fallback from HTML page
3. **`<img>` tag scan** — last resort, scans Shopify CDN URLs from HTML

All image URLs are upgraded to full resolution by stripping Shopify size suffixes (e.g. `_480x`, `_800x`).

## API routes

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` or `/portfolio` | Portfolio HTML viewer |
| GET | `/health` | Health check |
| POST | `/webhook/resend` | Resend inbound webhook |
| GET | `/api/images` | List all downloaded images (JSON) |
| GET | `/api/download/folder/{product}` | Download product folder as ZIP |
| POST | `/api/download/selected` | Download selected images as ZIP |
| DELETE | `/api/delete/image` | Delete a single image |
| DELETE | `/api/delete/folder/{product}` | Delete an entire product folder |
| Static | `/images/...` | Serve downloaded images directly |

## Running locally

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
RESEND_API_KEY=re_xxx uvicorn main:app --reload --port 8000
```

## Deploying / restarting

```bash
sudo systemctl restart fashion-scraper
sudo journalctl -u fashion-scraper -f   # tail logs
```

## Security notes

- Path traversal is guarded on delete endpoints — all paths are resolved and checked against `OUTPUT_DIR` before deletion.
- No authentication on the webhook endpoint; relies on Resend's event type filtering (`email.received` only).
