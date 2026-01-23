#!/bin/bash
# System Restore Script
# Restores system files from restic snapshots
# Usage: ./system-restore.sh <snapshot_id|latest> [--app] [--nginx] [--db]

set -euo pipefail

if [ -z "$1" ]; then
    echo "Usage: $0 <snapshot_id|latest> [--app] [--nginx] [--db]"
    echo ""
    echo "Examples:"
    echo "  $0 latest                     # Restore everything"
    echo "  $0 abc12345 --app             # Restore only app code"
    echo "  $0 latest --nginx             # Restore only nginx configs"
    echo "  $0 latest --db                # Restore only database"
    exit 1
fi

SNAPSHOT_ID="$1"
RESTIC_REPOSITORY="sftp:sh-backup@15.204.249.219:/home/sh-backup/system"
RESTIC_PASSWORD_FILE="/opt/shophosting/.system-restic-password"
BACKUP_LOG="/var/log/shophosting-system-restore.log"
SYSTEM_PATH="/opt/shophosting"
NGINX_PATH="/etc/nginx/sites-available"
TEMP_DIR=$(mktemp -d)

export RESTIC_REPOSITORY
export RESTIC_PASSWORD_FILE
export HOME=/root
export XDG_CACHE_HOME=/root/.cache

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$BACKUP_LOG"
}

log "=========================================="
log "Starting system restore from $SNAPSHOT_ID"
log "=========================================="

# Determine what to restore
RESTORE_APP=true
RESTORE_NGINX=true
RESTORE_DB=true

for arg in "$@"; do
    case "$arg" in
        --app) RESTORE_NGINX=false; RESTORE_DB=false ;;
        --nginx) RESTORE_APP=false; RESTORE_DB=false ;;
        --db) RESTORE_APP=false; RESTORE_NGINX=false ;;
    esac
done

# Verify snapshot exists
log "Verifying snapshot..."
SNAPSHOT_CHECK=$(restic snapshots --json 2>/dev/null | python3 -c "
import sys, json
snapshots = json.load(sys.stdin)
for s in snapshots:
    if s['id'].startswith('$SNAPSHOT_ID') or s['id'] == '$SNAPSHOT_ID':
        print(s['id'])
        break
" 2>/dev/null || true)

if [ -z "$SNAPSHOT_CHECK" ]; then
    log "ERROR: Snapshot $SNAPSHOT_ID not found"
    rm -rf "$TEMP_DIR"
    exit 1
fi

log "Found snapshot: $SNAPSHOT_CHECK"

# Restore to temp directory first
log "Extracting files to temporary directory..."
restic restore "$SNAPSHOT_ID" --target "$TEMP_DIR" 2>&1 | tee -a "$BACKUP_LOG" || {
    log "ERROR: Failed to restore from snapshot"
    rm -rf "$TEMP_DIR"
    exit 1
}

# Restore app code
if [ "$RESTORE_APP" = true ]; then
    log "Restoring application code..."
    
    # Stop services
    systemctl stop shophosting-webapp 2>/dev/null || true
    systemctl stop shophosting-worker 2>/dev/null || true
    
    # Backup current version
    if [ -d "$SYSTEM_PATH" ]; then
        mv "$SYSTEM_PATH" "${SYSTEM_PATH}.backup.$(date +%Y%m%d%H%M%S)" 2>/dev/null || true
    fi
    
    # Restore
    if [ -d "$TEMP_DIR/opt/shophosting" ]; then
        mv "$TEMP_DIR/opt/shophosting" "$SYSTEM_PATH"
        log "Application code restored to $SYSTEM_PATH"
    else
        log "Warning: Application directory not found in snapshot"
    fi
    
    # Restart services
    systemctl start shophosting-webapp 2>/dev/null || true
    systemctl start shophosting-worker 2>/dev/null || true
fi

# Restore nginx configs
if [ "$RESTORE_NGINX" = true ]; then
    log "Restoring nginx configurations..."
    
    if [ -d "$TEMP_DIR/nginx" ]; then
        # Backup current configs
        if [ -d "$NGINX_PATH" ]; then
            mkdir -p "${NGINX_PATH}.backup.$(date +%Y%m%d%H%M%S)"
            cp -r "$NGINX_PATH"/* "${NGINX_PATH}.backup.$(date +%Y%m%d%H%M%S)/" 2>/dev/null || true
        fi
        
        # Restore
        cp -r "$TEMP_DIR/nginx/"* "$NGINX_PATH/" 2>/dev/null || true
        
        # Test and reload nginx
        nginx -t 2>&1 | tee -a "$BACKUP_LOG" && systemctl reload nginx 2>&1 | tee -a "$BACKUP_LOG" || {
            log "Warning: Nginx configuration test failed, not reloading"
        }
        
        log "Nginx configurations restored"
    else
        log "Warning: Nginx configs not found in snapshot"
    fi
fi

# Restore database
if [ "$RESTORE_DB" = true ]; then
    log "Restoring database..."
    
    DB_DUMP="$TEMP_DIR/shophosting_db.sql"
    if [ -f "$DB_DUMP" ]; then
        mysql -u root -p"${DB_ROOT_PASSWORD:-rootpassword}" < "$DB_DUMP" 2>/dev/null || \
        mysql -u root -prootpassword < "$DB_DUMP" 2>/dev/null || {
            log "Warning: Database restore failed"
        }
        log "Database restored"
    else
        log "Warning: Database dump not found in snapshot"
    fi
fi

# Cleanup
rm -rf "$TEMP_DIR"

log "=========================================="
log "System restore completed successfully"
log "=========================================="

exit 0
