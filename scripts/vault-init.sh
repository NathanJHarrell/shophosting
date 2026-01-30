#!/bin/bash
#
# HashiCorp Vault Initialization Script for ShopHosting.io
#
# This script:
# 1. Initializes Vault (first time only)
# 2. Saves root token and unseal keys securely
# 3. Unseals Vault
# 4. Enables the KV secrets engine
# 5. Creates the shophosting policy
# 6. Creates an AppRole for the application
#
# Run this script ONCE after deploying Vault
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VAULT_DIR="$SCRIPT_DIR/../vault"
SECRETS_FILE="$VAULT_DIR/.vault-secrets"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

export VAULT_ADDR=${VAULT_ADDR:-http://127.0.0.1:8200}

# Check if vault CLI is available
if ! command -v vault &> /dev/null; then
    log_error "Vault CLI not found. Install with:"
    log_error "  wget https://releases.hashicorp.com/vault/1.15.4/vault_1.15.4_linux_amd64.zip"
    log_error "  unzip vault_1.15.4_linux_amd64.zip && sudo mv vault /usr/local/bin/"
    exit 1
fi

# Check if Vault is running
if ! curl -s "$VAULT_ADDR/v1/sys/health" > /dev/null 2>&1; then
    log_error "Vault is not running at $VAULT_ADDR"
    log_error "Start Vault with: cd $VAULT_DIR && docker compose up -d"
    exit 1
fi

# Check initialization status
INIT_STATUS=$(curl -s "$VAULT_ADDR/v1/sys/init" | jq -r '.initialized')

if [[ "$INIT_STATUS" == "false" ]]; then
    log_info "Initializing Vault..."

    # Initialize with 5 key shares, 3 required to unseal
    INIT_RESPONSE=$(vault operator init -key-shares=5 -key-threshold=3 -format=json)

    # Save secrets securely
    echo "$INIT_RESPONSE" > "$SECRETS_FILE"
    chmod 600 "$SECRETS_FILE"
    log_info "Vault secrets saved to: $SECRETS_FILE"
    log_warn "IMPORTANT: Back up this file securely and delete it from the server!"

    # Extract root token
    ROOT_TOKEN=$(echo "$INIT_RESPONSE" | jq -r '.root_token')
    UNSEAL_KEY_1=$(echo "$INIT_RESPONSE" | jq -r '.unseal_keys_b64[0]')
    UNSEAL_KEY_2=$(echo "$INIT_RESPONSE" | jq -r '.unseal_keys_b64[1]')
    UNSEAL_KEY_3=$(echo "$INIT_RESPONSE" | jq -r '.unseal_keys_b64[2]')

    log_info "Unsealing Vault..."
    vault operator unseal "$UNSEAL_KEY_1"
    vault operator unseal "$UNSEAL_KEY_2"
    vault operator unseal "$UNSEAL_KEY_3"

    export VAULT_TOKEN="$ROOT_TOKEN"

else
    log_info "Vault is already initialized"

    # Check if sealed
    SEAL_STATUS=$(curl -s "$VAULT_ADDR/v1/sys/seal-status" | jq -r '.sealed')

    if [[ "$SEAL_STATUS" == "true" ]]; then
        log_warn "Vault is sealed. Unsealing with stored keys..."

        if [[ ! -f "$SECRETS_FILE" ]]; then
            log_error "Secrets file not found: $SECRETS_FILE"
            log_error "You need to unseal manually with your unseal keys"
            exit 1
        fi

        UNSEAL_KEY_1=$(jq -r '.unseal_keys_b64[0]' "$SECRETS_FILE")
        UNSEAL_KEY_2=$(jq -r '.unseal_keys_b64[1]' "$SECRETS_FILE")
        UNSEAL_KEY_3=$(jq -r '.unseal_keys_b64[2]' "$SECRETS_FILE")

        vault operator unseal "$UNSEAL_KEY_1"
        vault operator unseal "$UNSEAL_KEY_2"
        vault operator unseal "$UNSEAL_KEY_3"
    fi

    # Get root token
    if [[ -f "$SECRETS_FILE" ]]; then
        ROOT_TOKEN=$(jq -r '.root_token' "$SECRETS_FILE")
        export VAULT_TOKEN="$ROOT_TOKEN"
    else
        log_error "Cannot find root token. Set VAULT_TOKEN environment variable."
        exit 1
    fi
fi

log_info "Vault is initialized and unsealed"

# Enable KV secrets engine v2
log_info "Enabling KV secrets engine..."
vault secrets enable -path=secret kv-v2 2>/dev/null || log_warn "KV engine already enabled"

# Create shophosting policy
log_info "Creating shophosting policy..."
vault policy write shophosting "$VAULT_DIR/policies/shophosting-policy.hcl"

# Enable AppRole auth method
log_info "Enabling AppRole auth method..."
vault auth enable approle 2>/dev/null || log_warn "AppRole already enabled"

# Create AppRole for shophosting
log_info "Creating shophosting AppRole..."
vault write auth/approle/role/shophosting \
    token_policies="shophosting" \
    token_ttl=1h \
    token_max_ttl=4h \
    secret_id_ttl=0 \
    secret_id_num_uses=0

# Get Role ID and Secret ID
ROLE_ID=$(vault read -format=json auth/approle/role/shophosting/role-id | jq -r '.data.role_id')
SECRET_ID=$(vault write -format=json -f auth/approle/role/shophosting/secret-id | jq -r '.data.secret_id')

# Save AppRole credentials
APPROLE_FILE="$VAULT_DIR/.vault-approle"
cat > "$APPROLE_FILE" << EOF
# Vault AppRole credentials for ShopHosting.io
# Add these to your .env file

VAULT_ADDR=$VAULT_ADDR
VAULT_ROLE_ID=$ROLE_ID
VAULT_SECRET_ID=$SECRET_ID
EOF
chmod 600 "$APPROLE_FILE"

log_info ""
log_info "=============================================="
log_info "Vault setup complete!"
log_info "=============================================="
log_info ""
log_info "AppRole credentials saved to: $APPROLE_FILE"
log_info ""
log_info "Add to your .env file:"
echo "  VAULT_ADDR=$VAULT_ADDR"
echo "  VAULT_ROLE_ID=$ROLE_ID"
echo "  VAULT_SECRET_ID=$SECRET_ID"
log_info ""
log_info "To add secrets to Vault:"
echo "  export VAULT_TOKEN=$ROOT_TOKEN"
echo "  vault kv put secret/shophosting/database \\"
echo "    host=localhost \\"
echo "    user=shophosting_app \\"
echo "    password=your-password"
echo ""
echo "  vault kv put secret/shophosting/stripe \\"
echo "    secret_key=sk_live_xxx \\"
echo "    publishable_key=pk_live_xxx \\"
echo "    webhook_secret=whsec_xxx"
echo ""
echo "  vault kv put secret/shophosting/app \\"
echo "    secret_key=your-flask-secret-key"
log_info ""
log_warn "SECURITY REMINDER:"
log_warn "1. Back up $SECRETS_FILE to a secure location"
log_warn "2. Delete $SECRETS_FILE from the server"
log_warn "3. Store unseal keys with different people/locations"
