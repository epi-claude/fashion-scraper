#!/bin/bash
# Run this script once to install and start both services
# Usage: bash setup-services.sh YOUR_RESEND_API_KEY

set -e

RESEND_API_KEY=$1
PROJECT_DIR="/home/parimal/projects/claude/fashion-scraper"

if [ -z "$RESEND_API_KEY" ]; then
  echo "ERROR: Please provide your Resend API key"
  echo "Usage: bash setup-services.sh YOUR_RESEND_API_KEY"
  exit 1
fi

echo "==> Step 1: Installing fashion-scraper systemd service..."
sudo cp "$PROJECT_DIR/fashion-scraper.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable fashion-scraper
sudo systemctl start fashion-scraper
echo "    fashion-scraper service started."

echo ""
echo "==> Step 2: Installing cloudflared systemd service..."
sudo cp "$PROJECT_DIR/cloudflared.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable cloudflared
sudo systemctl start cloudflared
echo "    cloudflared service started."

echo ""
echo "==> Step 3: Waiting 10 seconds for tunnel URL to be assigned..."
sleep 10

echo ""
echo "==> Step 4: Fetching your tunnel URL from cloudflared logs..."
TUNNEL_URL=$(sudo journalctl -u cloudflared --no-pager | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1)

if [ -z "$TUNNEL_URL" ]; then
  echo "    Could not auto-detect tunnel URL."
  echo "    Run: sudo journalctl -u cloudflared | grep trycloudflare"
  echo "    Then register manually with Resend."
  exit 1
fi

echo "    Tunnel URL: $TUNNEL_URL"

echo ""
echo "==> Step 5: Registering webhook with Resend..."
RESPONSE=$(curl -s -X POST https://api.resend.com/webhooks \
  -H "Authorization: Bearer $RESEND_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"endpoint\": \"${TUNNEL_URL}/webhook/resend\",
    \"events\": [\"email.received\"]
  }")

echo "    Resend response: $RESPONSE"

echo ""
echo "==> Step 6: Health check..."
HEALTH=$(curl -s "${TUNNEL_URL}/health")
echo "    Health: $HEALTH"

echo ""
echo "============================================"
echo " All done! Full automation is now active."
echo " Emails to fashion@notify.e-dmm.com"
echo " will trigger image downloads to:"
echo " $PROJECT_DIR/downloads/"
echo "============================================"
echo ""
echo "Useful commands:"
echo "  sudo systemctl status fashion-scraper"
echo "  sudo systemctl status cloudflared"
echo "  sudo journalctl -u fashion-scraper -f"
echo "  ls -lh $PROJECT_DIR/downloads/"

