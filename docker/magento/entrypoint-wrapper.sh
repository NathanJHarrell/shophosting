#!/bin/bash
set -euo pipefail

echo "[shophosting.io] Magento entrypoint-wrapper starting..."

# Support both naming conventions for database config
DB_HOST="${MAGENTO_DATABASE_HOST:-${DB_HOST:-db}}"
DB_USER="${MAGENTO_DATABASE_USER:-${DB_USER:-magento}}"
DB_PASSWORD="${MAGENTO_DATABASE_PASSWORD:-${DB_PASSWORD:-}}"
ES_HOST="${ELASTICSEARCH_HOST:-${OPENSEARCH_HOST:-elasticsearch}}"
ES_PORT="${ELASTICSEARCH_PORT:-${OPENSEARCH_PORT:-9200}}"

# Validate required environment variables
if [ -z "$DB_PASSWORD" ]; then
    echo "[shophosting.io] ERROR: Database password not set (MAGENTO_DATABASE_PASSWORD or DB_PASSWORD)"
    exit 1
fi

# Wait for MySQL to be ready
echo "[shophosting.io] Waiting for MySQL at ${DB_HOST}..."
MAX_MYSQL_RETRIES=150
MYSQL_RETRY_COUNT=0
until mysqladmin ping -h"${DB_HOST}" -u"${DB_USER}" -p"${DB_PASSWORD}" --silent 2>/dev/null; do
    MYSQL_RETRY_COUNT=$((MYSQL_RETRY_COUNT + 1))
    if [ $MYSQL_RETRY_COUNT -ge $MAX_MYSQL_RETRIES ]; then
        echo "[shophosting.io] ERROR: MySQL not available after ${MAX_MYSQL_RETRIES} attempts. Exiting."
        exit 1
    fi
    echo "[shophosting.io] MySQL not ready yet (attempt ${MYSQL_RETRY_COUNT}/${MAX_MYSQL_RETRIES})..."
    sleep 2
done
echo "[shophosting.io] MySQL is ready!"

# Wait for Elasticsearch/OpenSearch to be ready
echo "[shophosting.io] Waiting for Elasticsearch at ${ES_HOST}:${ES_PORT}..."
MAX_ES_RETRIES=150
ES_RETRY_COUNT=0
until curl -s "http://${ES_HOST}:${ES_PORT}/_cluster/health" | grep -q '"status"'; do
    ES_RETRY_COUNT=$((ES_RETRY_COUNT + 1))
    if [ $ES_RETRY_COUNT -ge $MAX_ES_RETRIES ]; then
        echo "[shophosting.io] ERROR: Elasticsearch not available after ${MAX_ES_RETRIES} attempts. Exiting."
        exit 1
    fi
    echo "[shophosting.io] Elasticsearch not ready yet (attempt ${ES_RETRY_COUNT}/${MAX_ES_RETRIES})..."
    sleep 2
done
echo "[shophosting.io] Elasticsearch is ready!"

echo "[shophosting.io] All dependencies ready. Starting Magento..."

# Start background process to fix PHP-FPM socket permissions
# This is needed because nginx runs as 'nobody' but PHP-FPM creates socket with 0660
# The socket is created after s6-overlay starts PHP-FPM, so we need to wait for it
(
    SOCKET_PATH="/run/php-fpm.sock"
    MAX_WAIT=120
    WAITED=0

    # Wait for socket to exist
    while [ ! -S "$SOCKET_PATH" ] && [ $WAITED -lt $MAX_WAIT ]; do
        sleep 1
        WAITED=$((WAITED + 1))
    done

    if [ -S "$SOCKET_PATH" ]; then
        chmod 0666 "$SOCKET_PATH"
        echo "[shophosting.io] Fixed PHP-FPM socket permissions"
    fi

    # Keep monitoring and fixing in case socket is recreated (e.g., PHP-FPM restart)
    while true; do
        if [ -S "$SOCKET_PATH" ]; then
            # Check current permissions and fix if needed
            PERMS=$(stat -c %a "$SOCKET_PATH" 2>/dev/null || echo "666")
            if [ "$PERMS" != "666" ]; then
                chmod 0666 "$SOCKET_PATH"
                echo "[shophosting.io] Re-fixed PHP-FPM socket permissions"
            fi
        fi
        sleep 30
    done
) &

# Run original docker-php-entrypoint
exec docker-php-entrypoint "$@"
