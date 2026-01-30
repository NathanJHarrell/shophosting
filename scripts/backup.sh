#!/bin/bash
# ShopHosting.io Daily Backup Script
# Backs up all customer data to remote VPS using restic

set -euo pipefail

# Configuration
CONFIG_FILE="/opt/shophosting/scripts/restic-backup-config.sh"
if [ -f "$CONFIG_FILE" ]; then
    # shellcheck source=/opt/shophosting/scripts/restic-backup-config.sh
    source "$CONFIG_FILE"
else
    RESTIC_REPOSITORY="sftp:sh-backup@15.204.249.219:/home/sh-backup/backups"
    RESTIC_PASSWORD_FILE="/root/.restic-password"
    RETENTION_DAYS=30
fi
BACKUP_LOG="/var/log/shophosting-backup.log"
DB_DUMP_DIR="/tmp/shophosting-db-dumps"

# Load environment variables from .env file (without sourcing as shell script)
load_env() {
    if [ -f /opt/shophosting/.env ]; then
        while IFS= read -r line || [ -n "$line" ]; do
            # Skip comments and empty lines
            if [[ "$line" =~ ^#.*$ ]] || [[ -z "${line// }" ]]; then
                continue
            fi
            # Export the variable if it looks like KEY=value
            if [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*=.*$ ]]; then
                export "$line"
            fi
        done < /opt/shophosting/.env
    fi
}

load_env

# Export for restic
export RESTIC_REPOSITORY
export RESTIC_PASSWORD_FILE

# Logging function
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$BACKUP_LOG"
}

# Error handler
error_exit() {
    log "ERROR: $1"
    exit 1
}

log "=========================================="
log "Starting ShopHosting.io backup"
log "=========================================="

# Step 1: Create database dumps directory
log "Creating database dump directory..."
mkdir -p "$DB_DUMP_DIR"
rm -f "$DB_DUMP_DIR"/*.sql

# Step 2: Dump master database
log "Dumping master database (shophosting_db)..."
mysqldump -h "${DB_HOST:-localhost}" \
    -u "${DB_USER:-shophosting_app}" \
    -p"${DB_PASSWORD}" \
    --single-transaction \
    --routines \
    --triggers \
    shophosting_db > "$DB_DUMP_DIR/shophosting_db.sql" \
    || error_exit "Failed to dump master database"

log "Master database dump complete: $(du -h "$DB_DUMP_DIR/shophosting_db.sql" | cut -f1)"

# Step 2.5: Dump OpenProject database (if running)
OPENPROJECT_BACKUP_SCRIPT="/opt/shophosting/openproject/scripts/backup-db.sh"
if [ -x "$OPENPROJECT_BACKUP_SCRIPT" ]; then
    log "Dumping OpenProject database..."
    "$OPENPROJECT_BACKUP_SCRIPT" 2>&1 | tee -a "$BACKUP_LOG" || log "Warning: OpenProject backup failed (container may not be running)"
else
    log "OpenProject backup script not found, skipping..."
fi

# Step 2.6: Dump Wiki.js database (if running)
if docker ps --format '{{.Names}}' | grep -q "shophosting-wikijs-db"; then
    log "Dumping Wiki.js database..."
    docker exec shophosting-wikijs-db pg_dump -U wikijs wikijs > "$DB_DUMP_DIR/wikijs.sql" \
        || log "Warning: Wiki.js backup failed"
    log "Wiki.js database dump complete: $(du -h "$DB_DUMP_DIR/wikijs.sql" | cut -f1)"
else
    log "Wiki.js container not running, skipping database dump..."
fi

# Step 3: Dump all customer databases
log "Dumping customer databases..."
for db in $(mysql -h "${DB_HOST:-localhost}" -u "${DB_USER:-shophosting_app}" -p"${DB_PASSWORD}" -N -e "SHOW DATABASES LIKE 'customer_%'"); do
    log "  Dumping $db..."
    mysqldump -h "${DB_HOST:-localhost}" \
        -u "${DB_USER:-shophosting_app}" \
        -p"${DB_PASSWORD}" \
        --single-transaction \
        "$db" > "$DB_DUMP_DIR/${db}.sql" \
        || log "  Warning: Failed to dump $db (may not have access)"
done

# Step 4: Run restic backup
log "Starting restic backup..."
restic backup \
    --verbose \
    --tag "daily" \
    --tag "$(date +%Y-%m-%d)" \
    "$DB_DUMP_DIR" \
    /var/customers \
    /etc/nginx/sites-available \
    /etc/letsencrypt \
    /opt/shophosting/.env \
    /opt/shophosting/openproject/data \
    /opt/shophosting/wikijs/.env \
    2>&1 | tee -a "$BACKUP_LOG" \
    || error_exit "Restic backup failed"

log "Backup complete"

# Step 5: Apply retention policy
log "Applying retention policy (keeping ${RETENTION_DAYS} daily snapshots)..."
restic forget \
    --keep-daily "$RETENTION_DAYS" \
    --prune \
    2>&1 | tee -a "$BACKUP_LOG" \
    || log "Warning: Retention policy application failed"

# Step 6: Verify repository integrity (weekly on Sundays)
if [ "$(date +%u)" -eq 7 ]; then
    log "Running weekly repository check..."
    restic check 2>&1 | tee -a "$BACKUP_LOG" || log "Warning: Repository check found issues"
fi

# Step 7: Cleanup
log "Cleaning up temporary files..."
rm -rf "$DB_DUMP_DIR"

# Step 8: Report
SNAPSHOT_COUNT=$(restic snapshots --json | grep -c '"time"' || echo "0")
log "=========================================="
log "Backup completed successfully"
log "Total snapshots in repository: $SNAPSHOT_COUNT"
log "=========================================="

exit 0
