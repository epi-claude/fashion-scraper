# MARO.SHOPPING — Steps NY Image Portfolio

Email Steps New York product links → the photos are collected automatically → everyone is notified → view and download them in a private portfolio. Old and sold-out products are cleaned up on their own.

**Portfolio:** https://fashion-scraper-production-b9b6.up.railway.app (passcode protected)

See `CHANGELOG.md` for what changed and when, and `CLAUDE.md` for the technical reference (all settings and API routes).

---

## How it works (plain language)

| Role | Address |
|---|---|
| Send product links **to** | `fashion@notify.e-dmm.com` |
| Notification emails come **from** | MARO.SHOPPING `noreply@notify.e-dmm.com` |
| "New arrivals" email goes **to** | `fashion@notify.e-dmm.com`, `pjariwala@episolve.com` |
| "Received" confirmation goes **to** | whoever sent the links |

1. **Send the links.** Email one or more `https://www.stepsnewyork.com/products/...` links to the address above. Subject and wording don't matter.
2. **Photos are collected** (about 1–2 minutes). Every photo of each product is saved at full quality, under a made-up name (e.g. "Ava Silk") for privacy.
3. **Two emails go out.** "New arrivals" (with a picture of each product and an *Open Portfolio* button) to the receivers, and a "Received: 2 added, 1 failed…" confirmation to the sender. A sender who is already a receiver only gets the first one.
4. **View and download.** Newest products are at the top, marked ✦ New. Download one photo, a selection, or a whole product as a ZIP. If something new arrives while you browse, a "✦ new product — tap to show" button appears; the page never jumps.
5. **Automatic cleanup** (every morning, ~6am New York time):
   - Sold out on Steps NY, or 30 days old → moves to the **📦 Archived** tab (hidden, not deleted). The last week shows "Archives in X days" in red.
   - Archived for 30 days → deleted for good. Tap **↺** to restore before then.
   - Archived because sold out, but back in stock → restored automatically.

Emails without Steps NY links are ignored (no reply). Re-sending a product refreshes its photos; if it was archived, it comes back as new.

---

## Hosting (Railway)

The app runs on **Railway**, connected to this GitHub repo. **Every push to `main` deploys automatically.**

- **Settings:** Railway → `fashion-scraper` service → **Variables** (full list in `CLAUDE.md`). The ones you must have: `RESEND_API_KEY`, `RESEND_WEBHOOK_SECRET`, `PORTFOLIO_BASE_URL`, `PORTFOLIO_PASSCODE`, and the five `R2_*` storage settings.
- **Logs:** Railway → service → **Deployments** → **View logs**. Healthy startup shows `Daily cleanup scheduled …`, then `Cleanup done …` about a minute later.
- **Images** are stored in Cloudflare **R2** (bucket set by `R2_BUCKET_NAME`).
- **Email** (receiving and sending) goes through **Resend**, domain `notify.e-dmm.com`. The inbound webhook points at `<portfolio URL>/webhook/resend`; `setup-services.sh` registers it.

The `fashion-scraper.service` / `cloudflared.service` systemd files are from the earlier self-hosted setup and are no longer used.

---

## Running locally

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Without `R2_*` settings, images are stored in `./downloads/<product>/` and product records in `./downloads/products.json`. Without `PORTFOLIO_PASSCODE` the portfolio is open. Without `PORTFOLIO_BASE_URL` no emails are sent.

Health check: `curl http://localhost:8000/health` → `{"status":"ok"}`

The webhook only accepts requests signed by Resend when `RESEND_WEBHOOK_SECRET` is set, so to try it by hand locally, leave that variable unset.

---

## Scraping strategy (priority order)

| # | Source | What it gives you |
|---|---|---|
| 1 | Shopify JSON API `/products/<handle>.json` | Every product image at full resolution |
| 2 | `<script type="application/ld+json">` | Product schema `image[]` array |
| 3 | `<img>` tag scan | Shopify CDN URLs from the HTML |

Shopify size suffixes (e.g. `_480x`) are stripped so you always get the full-resolution image. The daily sold-out check uses `/products/<handle>.js` (404 = removed, `"available": false` = sold out).

---

## Troubleshooting

| Symptom | Check |
|---|---|
| No "New arrivals" email | Portfolio bottom bar shows "✉ Email sent / ⚠ Email failed" — hover for the reason. Also check Railway logs and Resend → Emails. `PORTFOLIO_BASE_URL` must be set. |
| Email sent but nothing in the portfolio | Railway logs for `Rejected webhook` (wrong `RESEND_WEBHOOK_SECRET`) or `No Steps NY URLs found` |
| A product disappeared | Look in the **📦 Archived** tab — it was sold out or older than 30 days. Tap ↺ to restore. |
| Need the cleanup to run now | `POST /api/maintenance/run` (while logged in to the portfolio) |
| Re-send a notification | `POST /api/notify/<product-handle>` |
| Rate-limited by the store | Increase `DOWNLOAD_DELAY` in `scraper.py` / `CHECK_DELAY` in `maintenance.py` |
