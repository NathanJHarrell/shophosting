#!/bin/bash
# Customer Restore Script
# Restores a customer's site from a backup snapshot
# Usage: customer-restore.sh <customer_id> <snapshot_id> <restore_type: db|files|both> <source: manual|daily>

set -euo pipefail

# Arguments
CUSTOMER_ID="${1:-}"
SNAPSHOT_ID="${2:-}"
RESTORE_TYPE="${3:-both}"
SOURCE="${4:-manual}"

if [ -z "$CUSTOMER_ID" ] || [ -z "$SNAPSHOT_ID" ]; then
    echo "Usage: $0 <customer_id> <snapshot_id> <restore_type: db|files|both> <source: manual|daily>"
    exit 1
fi

# Configuration based on source
if [ "$SOURCE" = "manual" ]; then
    RESTIC_REPOSITORY="sftp:sh-backup@15.204.249.219:/home/sh-backup/manual-backups"
    RESTIC_PASSWORD_FILE="/opt/shophosting/.manual-restic-password"
else
    RESTIC_REPOSITORY="sftp:sh-backup@15.204.249.219:/home/sh-backup/backups"
    RESTIC_PASSWORD_FILE="/root/.restic-password"
fi

CUSTOMER_PATH="/var/customers/customer-${CUSTOMER_ID}"
RESTORE_DIR="/tmp/restore-customer-${CUSTOMER_ID}-$(date +%s)"
MAINTENANCE_FILE="${CUSTOMER_PATH}/.maintenance"

# Load environment
source /opt/shophosting/.env

# Export for restic
export RESTIC_REPOSITORY
export RESTIC_PASSWORD_FILE
export HOME=/root
export XDG_CACHE_HOME=/root/.cache

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

error_exit() {
    log "ERROR: $1"
    # Always try to disable maintenance mode on error
    rm -f "$MAINTENANCE_FILE" 2>/dev/null || true
    exit 1
}

cleanup() {
    log "Cleaning up..."
    rm -rf "$RESTORE_DIR" 2>/dev/null || true
    rm -f "$MAINTENANCE_FILE" 2>/dev/null || true
}

trap cleanup EXIT

# Validate customer directory exists
if [ ! -d "$CUSTOMER_PATH" ]; then
    error_exit "Customer directory not found: $CUSTOMER_PATH"
fi

# Verify snapshot exists and belongs to this customer
log "Verifying snapshot $SNAPSHOT_ID..."
if [ "$SOURCE" = "manual" ]; then
    # For manual backups, verify customer tag
    SNAPSHOT_INFO=$(restic snapshots --json "$SNAPSHOT_ID" 2>/dev/null | python3 -c "
import sys, json
data = json.load(sys.stdin)
if data:
    tags = data[0].get('tags', [])
    if 'customer-${CUSTOMER_ID}' in tags:
        print('valid')
    else:
        print('invalid')
else:
    print('notfound')
" 2>/dev/null)
else
    # For daily backups, verify snapshot contains customer path
    SNAPSHOT_INFO=$(restic ls "$SNAPSHOT_ID" 2>/dev/null | grep -q "/var/customers/customer-${CUSTOMER_ID}" && echo "valid" || echo "invalid")
fi

if [ "$SNAPSHOT_INFO" != "valid" ]; then
    error_exit "Snapshot $SNAPSHOT_ID not found or does not belong to customer $CUSTOMER_ID"
fi

log "Starting restore for customer $CUSTOMER_ID from snapshot $SNAPSHOT_ID (type: $RESTORE_TYPE)"

# Enable maintenance mode
log "Enabling maintenance mode..."
touch "$MAINTENANCE_FILE"

# Stop customer containers
log "Stopping customer containers..."
cd "$CUSTOMER_PATH"
docker compose down 2>/dev/null || log "Warning: Could not stop containers (may not be running)"

# Create restore directory
mkdir -p "$RESTORE_DIR"

# Restore files if requested
if [ "$RESTORE_TYPE" = "files" ] || [ "$RESTORE_TYPE" = "both" ]; then
    log "Restoring customer files..."

    restic restore "$SNAPSHOT_ID" \
        --target "$RESTORE_DIR" \
        --include "/var/customers/customer-${CUSTOMER_ID}" \
        || error_exit "File restore failed"

    # Determine what to restore (wordpress or volumes/files)
    if [ -d "$RESTORE_DIR/var/customers/customer-${CUSTOMER_ID}/wordpress" ]; then
        RESTORED_FILES="$RESTORE_DIR/var/customers/customer-${CUSTOMER_ID}/wordpress"
        TARGET_FILES="$CUSTOMER_PATH/wordpress"
    elif [ -d "$RESTORE_DIR/var/customers/customer-${CUSTOMER_ID}/volumes/files" ]; then
        RESTORED_FILES="$RESTORE_DIR/var/customers/customer-${CUSTOMER_ID}/volumes/files"
        TARGET_FILES="$CUSTOMER_PATH/volumes/files"
    else
        log "Warning: No files found in snapshot for customer path"
        RESTORED_FILES=""
    fi

    if [ -n "$RESTORED_FILES" ] && [ -d "$RESTORED_FILES" ]; then
        # Backup current files
        BACKUP_SUFFIX=$(date +%Y%m%d%H%M%S)
        if [ -d "$TARGET_FILES" ]; then
            mv "$TARGET_FILES" "${TARGET_FILES}.pre-restore-${BACKUP_SUFFIX}"
        fi

        # Move restored files into place
        mv "$RESTORED_FILES" "$TARGET_FILES"
        log "Files restored successfully"

        # Cleanup old backup after successful restore
        rm -rf "${TARGET_FILES}.pre-restore-${BACKUP_SUFFIX}" 2>/dev/null || true
    fi
fi

# Restore database if requested
if [ "$RESTORE_TYPE" = "db" ] || [ "$RESTORE_TYPE" = "both" ]; then
    log "Restoring customer database..."

    CUSTOMER_DB="customer_${CUSTOMER_ID}"

    # For manual backups, SQL is in /tmp/customer-backup-ID/
    # For daily backups, SQL is in /tmp/shophosting-db-dumps/
    if [ "$SOURCE" = "manual" ]; then
        SQL_PATH="/tmp/customer-backup-${CUSTOMER_ID}/${CUSTOMER_DB}.sql"
    else
        SQL_PATH="/tmp/shophosting-db-dumps/${CUSTOMER_DB}.sql"
    fi

    # Restore SQL dump from snapshot
    restic restore "$SNAPSHOT_ID" \
        --target "$RESTORE_DIR" \
        --include "$SQL_PATH" \
        || log "Warning: Could not restore database dump"

    RESTORED_SQL="$RESTORE_DIR$SQL_PATH"

    if [ -f "$RESTORED_SQL" ]; then
        log "Importing database from $RESTORED_SQL..."
        mysql -h "${DB_HOST:-localhost}" \
            -u "${DB_USER:-shophosting_app}" \
            -p"${DB_PASSWORD}" \
            "$CUSTOMER_DB" < "$RESTORED_SQL" \
            || error_exit "Database import failed"
        log "Database restored successfully"
    else
        log "Warning: No database dump found in snapshot"
    fi
fi

# Start customer containers
log "Starting customer containers..."
cd "$CUSTOMER_PATH"
docker compose up -d || error_exit "Failed to start containers"

# Wait for containers to be ready
log "Waiting for containers to be ready..."
sleep 10

# Disable maintenance mode (handled by trap, but do explicitly)
rm -f "$MAINTENANCE_FILE"

log "Restore completed successfully"
exit 0
