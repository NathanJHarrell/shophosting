#!/bin/bash
# ShopHosting.io Directory Backup Script
# Backs up /opt/shophosting to restic

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
BACKUP_LOG="/var/log/shophosting-dir-backup.log"

export RESTIC_REPOSITORY
export RESTIC_PASSWORD_FILE

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$BACKUP_LOG"
}

error_exit() {
    log "ERROR: $1"
    exit 1
}

log "=========================================="
log "Starting ShopHosting.io directory backup"
log "=========================================="

log "Starting restic backup of /opt/shophosting..."
restic backup \
    --verbose \
    --tag "daily" \
    --tag "shophosting-dir" \
    --tag "$(date +%Y-%m-%d)" \
    /opt/shophosting \
    2>&1 | tee -a "$BACKUP_LOG" \
    || error_exit "Restic backup failed"

log "Backup complete"

log "Applying retention policy (keeping ${RETENTION_DAYS} daily snapshots)..."
restic forget \
    --keep-daily "$RETENTION_DAYS" \
    --tag "shophosting-dir" \
    --prune \
    2>&1 | tee -a "$BACKUP_LOG" \
    || log "Warning: Retention policy application failed"

if [ "$(date +%u)" -eq 7 ]; then
    log "Running weekly repository check..."
    restic check 2>&1 | tee -a "$BACKUP_LOG" || log "Warning: Repository check found issues"
fi

SNAPSHOT_COUNT=$(restic snapshots --tag "shophosting-dir" --json | grep -c '"time"' || echo "0")
log "=========================================="
log "Directory backup completed successfully"
log "Total shophosting-dir snapshots: $SNAPSHOT_COUNT"
log "=========================================="

exit 0
