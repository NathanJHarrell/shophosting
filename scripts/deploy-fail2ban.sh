#!/bin/bash
#
# deploy-fail2ban.sh - Deploy fail2ban configuration for ShopHosting
#
# This script installs fail2ban (if not present) and deploys the
# ShopHosting jail configuration for SSH protection.
#
# Usage: sudo ./scripts/deploy-fail2ban.sh
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}ShopHosting Fail2ban Deployment${NC}"
echo "================================="
echo

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Error: This script must be run as root (use sudo)${NC}"
    exit 1
fi

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_SOURCE="${SCRIPT_DIR}/../configs/fail2ban-jail.local"
CONFIG_DEST="/etc/fail2ban/jail.local"

# Check if config source exists
if [ ! -f "$CONFIG_SOURCE" ]; then
    echo -e "${RED}Error: Configuration file not found: $CONFIG_SOURCE${NC}"
    exit 1
fi

# Install fail2ban if not present
if ! command -v fail2ban-client &> /dev/null; then
    echo -e "${YELLOW}Installing fail2ban...${NC}"
    apt-get update
    apt-get install -y fail2ban
    echo -e "${GREEN}fail2ban installed successfully${NC}"
else
    echo -e "${GREEN}fail2ban is already installed${NC}"
fi

# Backup existing config if present
if [ -f "$CONFIG_DEST" ]; then
    BACKUP="${CONFIG_DEST}.backup.$(date +%Y%m%d_%H%M%S)"
    echo -e "${YELLOW}Backing up existing config to: $BACKUP${NC}"
    cp "$CONFIG_DEST" "$BACKUP"
fi

# Deploy configuration
echo "Deploying fail2ban configuration..."
cp "$CONFIG_SOURCE" "$CONFIG_DEST"
chmod 644 "$CONFIG_DEST"

# Validate configuration
echo "Validating configuration..."
if fail2ban-client -t &> /dev/null; then
    echo -e "${GREEN}Configuration is valid${NC}"
else
    echo -e "${RED}Configuration validation failed!${NC}"
    if [ -n "$BACKUP" ]; then
        echo "Restoring backup..."
        cp "$BACKUP" "$CONFIG_DEST"
    fi
    exit 1
fi

# Restart fail2ban
echo "Restarting fail2ban service..."
systemctl restart fail2ban

# Wait for service to start
sleep 2

# Check status
if systemctl is-active --quiet fail2ban; then
    echo -e "${GREEN}fail2ban is running${NC}"
else
    echo -e "${RED}fail2ban failed to start!${NC}"
    systemctl status fail2ban
    exit 1
fi

# Show active jails
echo
echo "Active jails:"
fail2ban-client status

echo
echo -e "${GREEN}Deployment complete!${NC}"
echo
echo "Useful commands:"
echo "  fail2ban-client status           - Show all jails"
echo "  fail2ban-client status sshd      - Show SSH jail status"
echo "  fail2ban-client unban <IP>       - Unban an IP address"
echo "  tail -f /var/log/fail2ban.log    - Watch fail2ban logs"
