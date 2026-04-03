# Steps NY Image Scraper — Deployment Guide

## Files

| File | Purpose |
|---|---|
| `main.py` | FastAPI app — receives Resend webhook, dispatches background tasks |
| `scraper.py` | URL extraction, page scraping, image downloading |
| `requirements.txt` | Python dependencies |
| `fashion-scraper.service` | systemd unit for production auto-start |

---

## 1. Server Setup (Ubuntu / Debian)

```bash
# 1. Install Python 3.10+ if not present
sudo apt update && sudo apt install -y python3.10 python3.10-venv python3-pip

# 2. Create project directory
sudo mkdir -p /opt/fashion-scraper
sudo chown $USER /opt/fashion-scraper

# 3. Copy project files
cp main.py scraper.py requirements.txt /opt/fashion-scraper/
cd /opt/fashion-scraper

# 4. Create a virtual environment and install dependencies
python3.10 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 2. Run Manually (dev / testing)

```bash
cd /opt/fashion-scraper
source venv/bin/activate

# Single worker, auto-reload on code changes
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Test the health endpoint:
```bash
curl http://localhost:8000/health
# → {"status":"ok"}
```

Simulate a webhook locally:
```bash
curl -X POST http://localhost:8000/webhook/resend \
  -H "Content-Type: application/json" \
  -d '{
    "subject": "New arrivals",
    "from": "noreply@stepsnewyork.com",
    "text": "Check out https://www.stepsnewyork.com/collections/lst_all_dresses/products/kiah-maxi-dress and https://www.stepsnewyork.com/products/marisol-maxi-dress",
    "html": ""
  }'
```

Downloaded images will appear in `./downloads/<product-handle>/`.

---

## 3. Run as a systemd Service (production)

```bash
# Copy the service file
sudo cp fashion-scraper.service /etc/systemd/system/

# Edit WorkingDirectory / User if needed
sudo nano /etc/systemd/system/fashion-scraper.service

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable fashion-scraper
sudo systemctl start fashion-scraper

# Check status / logs
sudo systemctl status fashion-scraper
sudo journalctl -u fashion-scraper -f
```

---

## 4. Expose to the Internet

Resend needs a **public HTTPS URL** to deliver webhooks.

### Option A – Nginx reverse proxy (recommended)

```nginx
server {
    listen 443 ssl;
    server_name scraper.yourdomain.com;

    ssl_certificate     /etc/letsencrypt/live/scraper.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/scraper.yourdomain.com/privkey.pem;

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_read_timeout 30s;
    }
}
```

Get a free TLS cert:
```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d scraper.yourdomain.com
```

### Option B – Quick tunnel for testing (no domain needed)

```bash
# Using ngrok
ngrok http 8000
# Paste the https://xxxx.ngrok.io URL into Resend dashboard
```

---

## 5. Configure Resend Inbound Webhook

1. Log in to [resend.com](https://resend.com) → **Inbound** → **Create Webhook**.
2. Set the **Endpoint URL** to:
   ```
   https://scraper.yourdomain.com/webhook/resend
   ```
3. Choose the domain / email address that will receive the forwarded emails.
4. Save. Resend will POST the full parsed email JSON to your endpoint whenever a matching email arrives.

---

## 6. Downloaded Image Structure

```
downloads/
└── nike-air-max-90/
│   ├── image_01.jpg   ← front view
│   ├── image_02.jpg   ← back view
│   └── image_03.jpg   ← side view
└── adidas-samba/
    ├── image_01.jpg
    └── image_02.jpg
```

---

## 7. Scraping Strategy (priority order)

| # | Source | What it gives you |
|---|---|---|
| 1 | `window.ShopifyAnalytics.meta` JS object | Full product JSON incl. all media (front, back, side) at master resolution |
| 2 | `<script type="application/ld+json">` | Product schema `image[]` array |
| 3 | `<img>` tag scan | Shopify CDN URLs from the rendered HTML |

All image URLs have Shopify's size suffix (e.g. `_480x`, `_800x`) stripped so you always get the **full-resolution master** image.

---

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| `downloads/` is empty | Check `journalctl -u fashion-scraper` for scraper errors; confirm the URL matches the pattern |
| Resend shows delivery failures | Ensure your endpoint returns `200` within 10 s (it does — processing is async) |
| Only 1–2 images saved | The store may use a JS-rendered gallery; try fetching `https://stepsnewyork.com/products/<handle>.json` directly |
| Rate-limited by the store | Increase `DOWNLOAD_DELAY` in `scraper.py` |

