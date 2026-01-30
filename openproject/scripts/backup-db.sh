#!/bin/bash
# OpenProject Database Backup Script
# Dumps PostgreSQL database before Restic backup

set -e

BACKUP_DIR="/opt/shophosting/openproject/data/backups"
CONTAINER_NAME="openproject"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# Ensure backup directory exists
mkdir -p "$BACKUP_DIR"

# Check if container is running
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "OpenProject container is not running, skipping backup"
    exit 0
fi

echo "Starting OpenProject database backup..."

# Dump database using pg_dump inside the container
# OpenProject uses PostgreSQL with user 'openproject' and database 'openproject'
docker exec "$CONTAINER_NAME" pg_dump -U openproject -d openproject > "$BACKUP_DIR/openproject_${TIMESTAMP}.sql"

# Keep only the latest backup (Restic handles versioning)
# Also create a 'latest' symlink for easy access
ln -sf "openproject_${TIMESTAMP}.sql" "$BACKUP_DIR/openproject_latest.sql"

# Remove backups older than 1 day (keep only recent ones, Restic has history)
find "$BACKUP_DIR" -name "openproject_*.sql" -type f -mtime +1 -delete

echo "OpenProject database backup completed: openproject_${TIMESTAMP}.sql"
