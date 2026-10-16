# CLAUDE.md — Steps NY Fashion Scraper

## What this project does

Automated pipeline that listens for inbound emails via a **Resend webhook**, extracts Steps New York product URLs from the email body, scrapes all product images from the Shopify store, stores them in Cloudflare R2, and emails the receivers (and a confirmation to the sender) with a link to the portfolio viewer. A daily job archives sold-out / old products and later deletes them.

The app is branded as **MARO.SHOPPING** in emails and the portfolio UI.

## Architecture

```
Resend inbound email
  → POST /webhook/resend  (Svix signature checked)
    → background task: extract URLs → scrape images → record products
      → "New arrivals" email to NOTIFY_EMAILS + confirmation to the sender
Daily (≈ MAINTENANCE_HOUR_UTC): sold out / 30 days old → archive; archived 30 days → delete
```

**Deployed on Railway** (GitHub repo → auto-deploy; config in `railway.toml` / `Procfile`). Environment variables are set in the Railway dashboard → service → **Variables**. The systemd units (`fashion-scraper.service`, `cloudflared.service`) are from the old self-hosted setup and are not in use.

## Live deployment and email addresses

- **Portfolio:** https://fashion-scraper-production-b9b6.up.railway.app (passcode protected)
- **Links are emailed to:** `fashion@notify.e-dmm.com` (Resend inbound on `notify.e-dmm.com` — not yet confirmed in the Resend dashboard)
- **Notifications from:** `MARO.SHOPPING <noreply@notify.e-dmm.com>`
- **"New arrivals" to:** `fashion@notify.e-dmm.com`, `pjariwala@episolve.com` (`NOTIFY_EMAILS`)
- **"Received" confirmation to:** the sender of the links (skipped if the sender is a receiver and got the arrivals email)

History of changes: `CHANGELOG.md`. Plain-language overview: `README.md`.

## Portfolio UI behaviour (portfolio.html)

- Never re-renders on its own. A 60s background poll only shows a "✦ N new products — tap to show" pill; user actions refresh while keeping scroll position and loaded pages (`refresh()` / `applyData(d, true)`).
- Products ordered by `added_at` newest first (date groups in the sidebar), A–Z toggle stored in localStorage. "✦ New" = added since the previous visit (`maro_last_seen`).
- **Portfolio / 📦 Archived** tabs sit at the top of the main area (visible on mobile without the menu), with a highlighted rule banner. Products within 7 days of archiving show "Archives in N days" in red. Archived cards have a ↺ restore button.
- A 401 from any API call shows the passcode screen.

## Key files

| File | Purpose |
|------|---------|
| `main.py` | FastAPI app — routes, passcode middleware, webhook handler, inbound email flow |
| `scraper.py` | Scraping logic — Shopify JSON API → ld+json → HTML img fallback |
| `storage.py` | One interface over R2 / local `OUTPUT_DIR` (list, read, delete images) |
| `catalog.py` | Product records (`products.json`): alias, added date, sender, active/archived; plus meta (processed email IDs, notification log) |
| `notify.py` | Notification emails via Resend (arrivals + sender confirmation) |
| `maintenance.py` | Daily cleanup job (sold-out check via `/products/<handle>.js`, archive, delete) |
| `r2.py` | Cloudflare R2 (S3) helpers |
| `portfolio.html` | Static portfolio viewer (served at `/` and `/portfolio`) |
| `setup-services.sh` | Registers the Resend inbound webhook for the Railway URL |
| `CHANGELOG.md` | Dated record of changes |

## Environment variables

| Variable | Default | Notes |
|----------|---------|-------|
| `RESEND_API_KEY` | — | Required for fetching inbound emails and sending notifications |
| `RESEND_WEBHOOK_SECRET` | — | `whsec_…` signing secret from Resend → Webhooks. If unset, signatures are not checked (warning logged) |
| `NOTIFY_EMAILS` | `fashion@notify.e-dmm.com,pjariwala@episolve.com` | Comma-separated receivers of "new arrivals" emails (falls back to legacy `NOTIFY_EMAIL`) |
| `FROM_EMAIL` | `MARO.SHOPPING <noreply@notify.e-dmm.com>` | Must be on a Resend-verified domain. Inbound mail from this address is ignored (loop guard) |
| `PORTFOLIO_BASE_URL` | `https://$RAILWAY_PUBLIC_DOMAIN` | Public URL used for links/thumbnails in emails. No emails are sent if neither is set |
| `PORTFOLIO_PASSCODE` | — | If set, required (via cookie) for every API and `/images` request |
| `ALLOWED_SENDERS` | — | Optional allowlist, e.g. `a@b.com,@trusted.com`. Empty = anyone may send links |
| `ARCHIVE_AFTER_DAYS` | `30` | Active products are archived this long after arriving / being restored |
| `DELETE_AFTER_DAYS` | `30` | Archived products are deleted for good this long after archiving |
| `MAINTENANCE_HOUR_UTC` | `10` | Hour the daily cleanup runs (≈ 6am New York) |
| `R2_ENDPOINT_URL` | — | `https://<account_id>.r2.cloudflarestorage.com` |
| `R2_ACCESS_KEY_ID` | — | R2 API token key ID |
| `R2_SECRET_ACCESS_KEY` | — | R2 API token secret (also seeds the unguessable `products.json` key) |
| `R2_BUCKET_NAME` | — | R2 bucket name |
| `R2_PUBLIC_URL` | — | Public URL base for serving images (e.g. `https://pub-xxx.r2.dev`) |
| `OUTPUT_DIR` | `downloads` | Local image dir (used when R2 isn't configured; temp only when it is) |

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
| POST | `/api/unlock` | Check / submit passcode (sets auth cookie) |
| GET | `/api/images` | Products (newest first, with status) + images (JSON) |
| GET | `/api/download/folder/{product}` | Download product folder as ZIP |
| POST | `/api/download/selected` | Download selected images as ZIP |
| DELETE | `/api/delete/image` | Delete a single image |
| DELETE | `/api/delete/images` | Delete several images (`{"files": ["product/file", …]}`) |
| DELETE | `/api/delete/folder/{product}` | Delete an entire product folder |
| POST | `/api/archive/{product}` / `/api/restore/{product}` | Hide / un-hide a product |
| POST | `/api/maintenance/run` | Run the daily cleanup now |
| POST | `/api/notify/{product}` | Re-send the arrivals email for one product |
| Static | `/images/...` | Serve locally stored images |

Everything except `/`, `/portfolio`, `/health`, `/webhook/resend` and `/api/unlock` requires the passcode cookie when `PORTFOLIO_PASSCODE` is set.

## Running locally

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
RESEND_API_KEY=re_xxx uvicorn main:app --reload --port 8000
```

`portfolio.html` is read relative to the working directory, so run from the project root. Without R2 settings, data goes to `OUTPUT_DIR` (`downloads/`), including `products.json`. Leave `RESEND_WEBHOOK_SECRET` unset to post test webhooks by hand.

## Deploying / restarting

Push to the GitHub repo connected to Railway — it redeploys automatically. Logs: Railway dashboard → service → **Deployments** → **View logs**.

## Security notes

- Product handles and filenames are validated (`storage.valid_name`) before any read or delete, so paths can't escape a product folder.
- Webhook requests are verified with Resend's Svix signature when `RESEND_WEBHOOK_SECRET` is set; duplicate deliveries are ignored by email ID.
- The passcode is enforced server-side (HMAC cookie), not just in the page.
- The R2 bucket is public: `products.json` is stored under an unguessable `_meta/<hash>/` key because it contains sender addresses. The legacy `name_map.json` is still at the bucket root (alias → handle).
- No reply is sent to emails without product links, so auto-replies can't start a mail loop.
