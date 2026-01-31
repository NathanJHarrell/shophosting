#!/bin/bash
# Stripe Data Sync - runs daily to catch any missed webhook events
# Add to crontab: 0 4 * * * /opt/shophosting/scripts/cron/stripe_sync.sh

set -e

LOG_FILE="/opt/shophosting/logs/stripe_sync.log"
SCRIPT="/opt/shophosting/scripts/sync_stripe_data.py"

echo "$(date '+%Y-%m-%d %H:%M:%S') - Starting Stripe sync" >> "$LOG_FILE"

# Load environment and run sync
set -a
source /opt/shophosting/.env
set +a

cd /opt/shophosting/webapp
/opt/shophosting/webapp/venv/bin/python3 "$SCRIPT" --all >> "$LOG_FILE" 2>&1

echo "$(date '+%Y-%m-%d %H:%M:%S') - Stripe sync completed" >> "$LOG_FILE"
