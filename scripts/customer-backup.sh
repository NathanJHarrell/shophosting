#!/bin/bash
# Customer Manual Backup Script
# Creates a backup for a single customer to the manual backups repository
# Usage: customer-backup.sh <customer_id> <backup_type: db|files|both>

set -euo pipefail

# Arguments
CUSTOMER_ID="${1:-}"
BACKUP_TYPE="${2:-both}"

if [ -z "$CUSTOMER_ID" ]; then
    echo "Usage: $0 <customer_id> <backup_type: db|files|both>"
    exit 1
fi

# Configuration
RESTIC_REPOSITORY="sftp:sh-backup@15.204.249.219:/home/sh-backup/manual-backups"
RESTIC_PASSWORD_FILE="/opt/shophosting/.manual-restic-password"
CUSTOMER_PATH="/var/customers/customer-${CUSTOMER_ID}"
DB_DUMP_DIR="/tmp/customer-backup-${CUSTOMER_ID}"
MAX_BACKUPS=5
TIMESTAMP=$(date +%Y-%m-%d-%H%M%S)

# Load environment variables safely (handle special characters in values)
ENV_FILE="/opt/shophosting/.env"
DB_HOST=$(grep -E "^DB_HOST=" "$ENV_FILE" | cut -d= -f2-)
DB_USER=$(grep -E "^DB_USER=" "$ENV_FILE" | cut -d= -f2-)
DB_PASSWORD=$(grep -E "^DB_PASSWORD=" "$ENV_FILE" | cut -d= -f2-)

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
    exit 1
}

# Validate customer directory exists
if [ ! -d "$CUSTOMER_PATH" ]; then
    error_exit "Customer directory not found: $CUSTOMER_PATH"
fi

log "Starting backup for customer $CUSTOMER_ID (type: $BACKUP_TYPE)"

# Prepare backup paths
BACKUP_PATHS=()
BACKUP_TAGS=("customer-${CUSTOMER_ID}" "manual" "$BACKUP_TYPE" "$TIMESTAMP")

# Handle database backup
if [ "$BACKUP_TYPE" = "db" ] || [ "$BACKUP_TYPE" = "both" ]; then
    log "Dumping customer database..."
    mkdir -p "$DB_DUMP_DIR"
    rm -f "$DB_DUMP_DIR"/*.sql

    # Customer database runs in Docker container
    CONTAINER_NAME="customer-${CUSTOMER_ID}-db"
    CUSTOMER_DB="customer_${CUSTOMER_ID}"

    # Get MySQL root password from docker-compose.yml
    COMPOSE_FILE="${CUSTOMER_PATH}/docker-compose.yml"
    if [ ! -f "$COMPOSE_FILE" ]; then
        error_exit "Docker compose file not found: $COMPOSE_FILE"
    fi

    MYSQL_ROOT_PASSWORD=$(grep -A1 "MYSQL_ROOT_PASSWORD:" "$COMPOSE_FILE" | head -1 | sed 's/.*: *"\?\([^"]*\)"\?/\1/' | tr -d ' "')

    # Check if container is running
    if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        error_exit "Database container not running: $CONTAINER_NAME"
    fi

    # Dump the database from inside the container
    docker exec "$CONTAINER_NAME" mysqldump \
        -u root \
        -p"${MYSQL_ROOT_PASSWORD}" \
        --single-transaction \
        "$CUSTOMER_DB" > "$DB_DUMP_DIR/${CUSTOMER_DB}.sql" 2>/dev/null \
        || error_exit "Failed to dump database $CUSTOMER_DB"

    log "Database dump complete: $(du -h "$DB_DUMP_DIR/${CUSTOMER_DB}.sql" | cut -f1)"
    BACKUP_PATHS+=("$DB_DUMP_DIR")
fi

# Handle files backup
if [ "$BACKUP_TYPE" = "files" ] || [ "$BACKUP_TYPE" = "both" ]; then
    log "Adding customer files to backup..."
    BACKUP_PATHS+=("$CUSTOMER_PATH")
fi

# Run restic backup
log "Running restic backup..."
TAG_ARGS=""
for tag in "${BACKUP_TAGS[@]}"; do
    TAG_ARGS="$TAG_ARGS --tag $tag"
done

restic backup $TAG_ARGS "${BACKUP_PATHS[@]}" \
    || error_exit "Restic backup failed"

# Get the snapshot ID of the backup we just created
SNAPSHOT_ID=$(restic snapshots --json --latest 1 --tag "customer-${CUSTOMER_ID}" --tag "manual" 2>/dev/null \
    | python3 -c "import sys,json; data=json.load(sys.stdin); print(data[0]['id'] if data else '')" 2>/dev/null)

log "Backup complete. Snapshot ID: $SNAPSHOT_ID"

# Enforce retention: keep only MAX_BACKUPS per customer
log "Enforcing retention policy (max $MAX_BACKUPS backups)..."
CUSTOMER_SNAPSHOTS=$(restic snapshots --json --tag "customer-${CUSTOMER_ID}" --tag "manual" 2>/dev/null \
    | python3 -c "import sys,json; data=json.load(sys.stdin); print(len(data))" 2>/dev/null || echo "0")

if [ "$CUSTOMER_SNAPSHOTS" -gt "$MAX_BACKUPS" ]; then
    log "Customer has $CUSTOMER_SNAPSHOTS backups, pruning to $MAX_BACKUPS..."
    restic forget --tag "customer-${CUSTOMER_ID}" --tag "manual" --keep-last "$MAX_BACKUPS" --prune \
        || log "Warning: Retention enforcement failed"
fi

# Cleanup
if [ -d "$DB_DUMP_DIR" ]; then
    rm -rf "$DB_DUMP_DIR"
fi

log "Backup completed successfully"
echo "SNAPSHOT_ID=$SNAPSHOT_ID"
exit 0
