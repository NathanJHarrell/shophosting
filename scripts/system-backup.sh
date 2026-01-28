#!/bin/bash
# System Backup Script
# Backs up application code, configs, and nginx sites to remote VPS using restic
# Usage: ./system-backup.sh [--tag custom_tag]

set -euo pipefail

# Load environment first (for DB credentials)
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

# Configuration - set AFTER load_env to avoid being overwritten
RESTIC_REPOSITORY="sftp:sh-backup@15.204.249.219:/home/sh-backup/system"
RESTIC_PASSWORD_FILE="/opt/shophosting/.system-restic-password"
BACKUP_LOG="/var/log/shophosting-system-backup.log"
SYSTEM_PATH="/opt/shophosting"
NGINX_PATH="/etc/nginx/sites-available"
RETENTION_DAYS=7

export RESTIC_REPOSITORY
export RESTIC_PASSWORD_FILE

# Custom tag from command line
# Usage: ./system-backup.sh [tag] or ./system-backup.sh --tag tag
CUSTOM_TAG=""
if [ "${1:-}" = "--tag" ] && [ -n "${2:-}" ]; then
    CUSTOM_TAG="$2"
elif [ -n "${1:-}" ] && [ "${1:0:2}" != "--" ]; then
    CUSTOM_TAG="$1"
fi
export HOME=/root
export XDG_CACHE_HOME=/root/.cache

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$BACKUP_LOG"
}

log "=========================================="
log "Starting system backup"
log "=========================================="

# Create temp directory for backup sources
TEMP_DIR=$(mktemp -d)
trap "rm -rf $TEMP_DIR" EXIT

# Create nginx config backup
mkdir -p "$TEMP_DIR/nginx"
if [ -d "$NGINX_PATH" ]; then
    cp -r "$NGINX_PATH"/* "$TEMP_DIR/nginx/" 2>/dev/null || log "Warning: Could not backup nginx configs"
fi

# Create database dump
DB_DUMP="$TEMP_DIR/shophosting_db.sql"
if [ -n "${DB_PASSWORD:-}" ]; then
    mysqldump -h "${DB_HOST:-localhost}" \
        -u "${DB_USER:-shophosting_app}" \
        -p"${DB_PASSWORD}" \
        --single-transaction \
        --routines \
        --triggers \
        shophosting_db > "$DB_DUMP" 2>/dev/null && \
        log "Database dump complete: $(du -h "$DB_DUMP" | cut -f1)" || \
        log "Warning: Could not backup database"
else
    log "Warning: DB_PASSWORD not set, skipping database dump"
fi

# Build tags
TAGS=("system" "app")
if [ -n "$CUSTOM_TAG" ]; then
    TAGS+=("$CUSTOM_TAG")
fi
TAG_ARGS=""
for tag in "${TAGS[@]}"; do
    TAG_ARGS="$TAG_ARGS --tag $tag"
done

# Run restic backup
log "Backing up system files..."
restic backup \
    $TAG_ARGS \
    --tag "$(date +%Y-%m-%d)" \
    "$SYSTEM_PATH" \
    "$TEMP_DIR" \
    2>&1 | tee -a "$BACKUP_LOG"

if [ ${PIPESTATUS[0]} -eq 0 ]; then
    log "System backup completed successfully"
    
    # Get snapshot ID
    SNAPSHOT_ID=$(restic snapshots --json --latest 2>/dev/null | python3 -c "import sys,json;print(json.load(sys.stdin)[0]['id'])" 2>/dev/null || echo "unknown")
    log "Snapshot ID: $SNAPSHOT_ID"
    
    # Apply retention
    log "Applying retention policy (keeping ${RETENTION_DAYS} daily snapshots)..."
    restic forget \
        --tag system \
        --keep-last "$RETENTION_DAYS" \
        --prune \
        2>&1 | tee -a "$BACKUP_LOG" || log "Warning: Retention policy had issues"
else
    log "ERROR: System backup failed"
    exit 1
fi

log "System backup process completed"
exit 0
