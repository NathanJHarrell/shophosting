#!/bin/bash
# Customer Backup Script
# Backs up a specific customer's data to remote VPS using restic
# Usage: ./customer-backup.sh <customer_id>

set -euo pipefail

if [ -z "$1" ]; then
    echo "Usage: $0 <customer_id>"
    exit 1
fi

CUSTOMER_ID="$1"
CUSTOMER_DIR="/var/customers/customer-${CUSTOMER_ID}"

if [ ! -d "$CUSTOMER_DIR" ]; then
    echo "ERROR: Customer directory not found: $CUSTOMER_DIR"
    exit 1
fi

# Configuration
RESTIC_REPOSITORY="sftp:sh-backup@15.204.249.219:/home/sh-backup/backups"
RESTIC_PASSWORD_FILE="/root/.restic-password"
BACKUP_LOG="/var/log/shophosting-customer-backup.log"
DB_DUMP_DIR="/tmp/shophosting-customer-backup-${CUSTOMER_ID}"
RETENTION_DAYS=14

# Load environment
load_env() {
    if [ -f /opt/shophosting/.env ]; then
        while IFS= read -r line || [ -n "$line" ]; do
            if [[ "$line" =~ ^#.*$ ]] || [[ -z "${line// }" ]]; then
                continue
            fi
            if [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*=.*$ ]]; then
                export "$line"
            fi
        done < /opt/shophosting/.env
    fi
}

load_env

export RESTIC_REPOSITORY
export RESTIC_PASSWORD_FILE
export HOME=/root
export XDG_CACHE_HOME=/root/.cache

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [Customer ${CUSTOMER_ID}] $1" | tee -a "$BACKUP_LOG"
}

log "=========================================="
log "Starting backup for customer ${CUSTOMER_ID}"
log "=========================================="

# Create temp directory
mkdir -p "$DB_DUMP_DIR"

# Detect platform and get database info
if [ -f "${CUSTOMER_DIR}/.platform" ]; then
    PLATFORM=$(cat "${CUSTOMER_DIR}/.platform")
else
    # Try to detect from docker-compose.yml
    if [ -f "${CUSTOMER_DIR}/docker-compose.yml" ]; then
        if grep -q "wordpress" "${CUSTOMER_DIR}/docker-compose.yml"; then
            PLATFORM="woocommerce"
        elif grep -q "magento" "${CUSTOMER_DIR}/docker-compose.yml"; then
            PLATFORM="magento"
        else
            PLATFORM="unknown"
        fi
    else
        PLATFORM="unknown"
    fi
fi

log "Detected platform: ${PLATFORM}"

# Backup database based on platform
case "$PLATFORM" in
    woocommerce)
        # WordPress uses MySQL
        log "Backing up WordPress database..."
        DB_CONTAINER="${CUSTOMER_ID}-wordpress"
        if docker ps --format '{{.Names}}' | grep -q "^${DB_CONTAINER}$"; then
            docker exec "$DB_CONTAINER" mysqldump -u root -p"${MYSQL_ROOT_PASSWORD:-rootpassword}" wordpress > "${DB_DUMP_DIR}/database.sql" 2>/dev/null || \
            docker exec "$DB_CONTAINER" mysqldump -u root -prootpassword wordpress > "${DB_DUMP_DIR}/database.sql" 2>/dev/null || \
            log "Warning: Could not backup database"
        else
            log "Warning: Database container not found"
        fi
        ;;
    magento)
        # Magento uses MySQL
        log "Backing up Magento database..."
        DB_CONTAINER="${CUSTOMER_ID}-mysql"
        if docker ps --format '{{.Names}}' | grep -q "^${DB_CONTAINER}$"; then
            docker exec "$DB_CONTAINER" mysqldump -u root -p"${MYSQL_ROOT_PASSWORD:-rootpassword}" magento > "${DB_DUMP_DIR}/database.sql" 2>/dev/null || \
            log "Warning: Could not backup database"
        else
            log "Warning: Database container not found"
        fi
        ;;
    *)
        log "Unknown platform, skipping database backup"
        ;;
esac

# Run restic backup
log "Starting restic backup..."
CUSTOMER_TAG="customer-${CUSTOMER_ID}"
CUSTOMER_BACKUP_DIRS="${DB_DUMP_DIR} ${CUSTOMER_DIR}"

restic backup \
    --tag "customer" \
    --tag "$CUSTOMER_TAG" \
    --tag "manual" \
    --tag "$(date +%Y-%m-%d-%H%M%S)" \
    $CUSTOMER_BACKUP_DIRS \
    2>&1 | tee -a "$BACKUP_LOG"

if [ ${PIPESTATUS[0]} -eq 0 ]; then
    log "Backup completed successfully"
    
    # Get snapshot ID
    SNAPSHOT_ID=$(restic snapshots --json --tag "$CUSTOMER_TAG" --latest 2>/dev/null | python3 -c "import sys,json;print(json.load(sys.stdin)[0]['id'])" 2>/dev/null || echo "unknown")
    log "Snapshot ID: $SNAPSHOT_ID"
    
    # Apply retention for this customer's snapshots
    log "Applying retention policy (keeping ${RETENTION_DAYS} snapshots)..."
    restic forget \
        --tag "$CUSTOMER_TAG" \
        --keep-last "$RETENTION_DAYS" \
        --prune \
        2>&1 | tee -a "$BACKUP_LOG" || log "Warning: Retention policy application had issues"
else
    log "ERROR: Backup failed"
fi

# Cleanup
log "Cleaning up temporary files..."
rm -rf "$DB_DUMP_DIR"

log "Backup process completed"
exit 0
