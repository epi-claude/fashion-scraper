#!/bin/bash
# Register the Resend inbound webhook for the Railway deployment.
# Usage: bash setup-services.sh YOUR_RESEND_API_KEY [RAILWAY_URL]

set -e

RESEND_API_KEY=$1
RAILWAY_URL=${2:-"https://fashion-scraper-production-b9b6.up.railway.app"}

if [ -z "$RESEND_API_KEY" ]; then
  echo "ERROR: Please provide your Resend API key"
  echo "Usage: bash setup-services.sh YOUR_RESEND_API_KEY [RAILWAY_URL]"
  exit 1
fi

echo "==> Registering Resend inbound webhook..."
RESPONSE=$(curl -s -X POST https://api.resend.com/webhooks \
  -H "Authorization: Bearer $RESEND_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"endpoint\": \"${RAILWAY_URL}/webhook/resend\",
    \"events\": [\"email.received\"]
  }")

echo "    Resend response: $RESPONSE"

echo ""
echo "==> Health check..."
HEALTH=$(curl -s "${RAILWAY_URL}/health")
echo "    Health: $HEALTH"

echo ""
echo "============================================"
echo " Webhook registered for: ${RAILWAY_URL}"
echo " Inbound emails will trigger image downloads"
echo "============================================"
