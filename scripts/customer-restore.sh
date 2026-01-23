#!/bin/bash
# Customer Restore Script
# Restores customer data from restic snapshots
# Usage: ./customer-restore.sh <customer_id> <snapshot_id> <target> [--force]
#
# target options: db, files, all

set -euo pipefail

if [ -z "$1" ] || [ -z "$2" ] || [ -z "$3" ]; then
    echo "Usage: $0 <customer_id> <snapshot_id> <db|files|all>"
    echo ""
    echo "Examples:"
    echo "  $0 6 f724d41e db        # Restore only database from snapshot"
    echo "  $0 6 f724d41e files     # Restore only files from snapshot"
    echo "  $0 6 f724d41e all       # Restore both database and files"
    exit 1
fi

CUSTOMER_ID="$1"
SNAPSHOT_ID="$2"
TARGET="$3"
FORCE_FLAG="${4:-}"

CUSTOMER_DIR="/var/customers/customer-${CUSTOMER_ID}"

if [ ! -d "$CUSTOMER_DIR" ]; then
    echo "ERROR: Customer directory not found: $CUSTOMER_DIR"
    exit 1
fi

# Configuration
RESTIC_REPOSITORY="sftp:sh-backup@15.204.249.219:/home/sh-backup/backups"
RESTIC_PASSWORD_FILE="/root/.restic-password"
RESTORE_DIR="/tmp/shophosting-restore-${CUSTOMER_ID}-${SNAPSHOT_ID}"
BACKUP_LOG="/var/log/shophosting-customer-restore.log"

export RESTIC_REPOSITORY
export RESTIC_PASSWORD_FILE
export HOME=/root
export XDG_CACHE_HOME=/root/.cache

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [Customer ${CUSTOMER_ID} Restore from ${SNAPSHOT_ID}] $1" | tee -a "$BACKUP_LOG"
}

log "=========================================="
log "Starting restore for customer ${CUSTOMER_ID}"
log "Target: ${TARGET}"
log "Snapshot: ${SNAPSHOT_ID}"
log "=========================================="

# Validate target
if [[ ! "$TARGET" =~ ^(db|files|all)$ ]]; then
    log "ERROR: Invalid target: ${TARGET}. Must be db, files, or all"
    exit 1
fi

# Detect platform
if [ -f "${CUSTOMER_DIR}/.platform" ]; then
    PLATFORM=$(cat "${CUSTOMER_DIR}/.platform")
else
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

# Create restore directory
mkdir -p "$RESTORE_DIR"

# Check if snapshot exists
log "Verifying snapshot..."
SNAPSHOT_CHECK=$(restic find --tag customer-"${CUSTOMER_ID}" --json 2>/dev/null | grep -o "\"id\":\"${SNAPSHOT_ID}\"" || true)
if [ -z "$SNAPSHOT_CHECK" ]; then
    log "ERROR: Snapshot ${SNAPSHOT_ID} not found for customer ${CUSTOMER_ID}"
    rm -rf "$RESTORE_DIR"
    exit 1
fi
log "Snapshot verified"

# Restore database
if [[ "$TARGET" == "db" ]] || [[ "$TARGET" == "all" ]]; then
    log "Restoring database..."
    
    DB_DUMP_PATH="${RESTORE_DIR}/database.sql"
    
    restic dump "${SNAPSHOT_ID}" --tag customer-"${CUSTOMER_ID}" --path "/tmp/shophosting-customer-backup-${CUSTOMER_ID}/database.sql" > "$DB_DUMP_PATH" 2>/dev/null || \
    restic dump "${SNAPSHOT_ID}" --tag customer-"${CUSTOMER_ID}" --path "/var/customers/customer-${CUSTOMER_ID}/database.sql" > "$DB_DUMP_PATH" 2>/dev/null || \
    restic dump "${SNAPSHOT_ID}" --tag customer-"${CUSTOMER_ID}" > "$DB_DUMP_PATH" 2>/dev/null
    
    if [ ! -s "$DB_DUMP_PATH" ]; then
        log "Warning: No database backup found in snapshot"
    else
        log "Database dump found: $(du -h "$DB_DUMP_PATH" | cut -f1)"
        
        # Restore based on platform
        case "$PLATFORM" in
            woocommerce)
                DB_CONTAINER="${CUSTOMER_ID}-wordpress"
                if docker ps --format '{{.Names}}' | grep -q "^${DB_CONTAINER}$"; then
                    log "Dropping and recreating database..."
                    docker exec "$DB_CONTAINER" mysql -u root -p"${MYSQL_ROOT_PASSWORD:-rootpassword}" -e "DROP DATABASE IF EXISTS wordpress" 2>/dev/null || \
                    docker exec "$DB_CONTAINER" mysql -u root -prootpassword -e "DROP DATABASE IF EXISTS wordpress" 2>/dev/null
                    docker exec "$DB_CONTAINER" mysql -u root -p"${MYSQL_ROOT_PASSWORD:-rootpassword}" -e "CREATE DATABASE wordpress CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci" 2>/dev/null || \
                    docker exec "$DB_CONTAINER" mysql -u root -prootpassword -e "CREATE DATABASE wordpress CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci" 2>/dev/null
                    
                    log "Importing database..."
                    cat "$DB_DUMP_PATH" | docker exec -i "$DB_CONTAINER" mysql -u root -p"${MYSQL_ROOT_PASSWORD:-rootpassword}" wordpress 2>/dev/null || \
                    cat "$DB_DUMP_PATH" | docker exec -i "$DB_CONTAINER" mysql -u root -prootpassword wordpress 2>/dev/null
                    
                    log "Database restored successfully"
                else
                    log "ERROR: Database container not found"
                    rm -rf "$RESTORE_DIR"
                    exit 1
                fi
                ;;
            magento)
                DB_CONTAINER="${CUSTOMER_ID}-mysql"
                if docker ps --format '{{.Names}}' | grep -q "^${DB_CONTAINER}$"; then
                    log "Dropping and recreating database..."
                    docker exec "$DB_CONTAINER" mysql -u root -p"${MYSQL_ROOT_PASSWORD:-rootpassword}" -e "DROP DATABASE IF EXISTS magento" 2>/dev/null
                    docker exec "$DB_CONTAINER" mysql -u root -p"${MYSQL_ROOT_PASSWORD:-rootpassword}" -e "CREATE DATABASE magento CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci" 2>/dev/null
                    
                    log "Importing database..."
                    cat "$DB_DUMP_PATH" | docker exec -i "$DB_CONTAINER" mysql -u root -p"${MYSQL_ROOT_PASSWORD:-rootpassword}" magento 2>/dev/null
                    
                    log "Database restored successfully"
                else
                    log "ERROR: Database container not found"
                    rm -rf "$RESTORE_DIR"
                    exit 1
                fi
                ;;
            *)
                log "ERROR: Unknown platform, cannot restore database"
                rm -rf "$RESTORE_DIR"
                exit 1
                ;;
        esac
    fi
fi

# Restore files
if [[ "$TARGET" == "files" ]] || [[ "$TARGET" == "all" ]]; then
    log "Restoring files..."
    
    # Stop containers first
    log "Stopping customer containers..."
    cd "$CUSTOMER_DIR"
    docker compose down 2>/dev/null || docker-compose down 2>/dev/null || true
    
    # Restore files from snapshot
    log "Extracting files from snapshot..."
    restic restore "${SNAPSHOT_ID}" --tag customer-"${CUSTOMER_ID}" --target "${RESTORE_DIR}/files" --include "/var/customers/customer-${CUSTOMER_ID}" 2>/dev/null || \
    restic restore "${SNAPSHOT_ID}" --target "${RESTORE_DIR}/files" 2>/dev/null
    
    if [ -d "${RESTORE_DIR}/files/var/customers/customer-${CUSTOMER_ID}" ]; then
        # Copy files back
        log "Copying restored files..."
        rsync -a --delete "${RESTORE_DIR}/files/var/customers/customer-${CUSTOMER_ID}/" "${CUSTOMER_DIR}/" 2>/dev/null || \
        cp -a "${RESTORE_DIR}/files/var/customers/customer-${CUSTOMER_ID}/"* "${CUSTOMER_DIR}/" 2>/dev/null || \
        true
        
        # Ensure proper permissions
        if [ -d "${CUSTOMER_DIR}/wordpress" ]; then
            chown -R 33:33 "${CUSTOMER_DIR}/wordpress" 2>/dev/null || true
        fi
        
        log "Files restored successfully"
    else
        log "Warning: No customer files found in snapshot"
    fi
    
    # Restart containers
    log "Restarting customer containers..."
    cd "$CUSTOMER_DIR"
    docker compose up -d 2>/dev/null || docker-compose up -d 2>/dev/null || true
fi

# Cleanup
log "Cleaning up temporary files..."
rm -rf "$RESTORE_DIR"

log "=========================================="
log "Restore completed successfully"
log "Target restored: ${TARGET}"
log "=========================================="

exit 0
