#!/usr/bin/env bash
set -euo pipefail

echo "[shophosting.io] entrypoint-wrapper starting..."

# Require DB env vars (these come from docker compose 'environment:')
: "${WORDPRESS_DB_HOST:?Missing WORDPRESS_DB_HOST}"
: "${WORDPRESS_DB_USER:?Missing WORDPRESS_DB_USER}"
: "${WORDPRESS_DB_PASSWORD:?Missing WORDPRESS_DB_PASSWORD}"

# Parse host and port (WORDPRESS_DB_HOST can be "host:port" or just "host")
DB_HOST="${WORDPRESS_DB_HOST%%:*}"
DB_PORT="${WORDPRESS_DB_HOST##*:}"
if [[ "$DB_PORT" == "$DB_HOST" ]]; then
    DB_PORT="3306"
fi

echo "[shophosting.io] waiting for MySQL at ${DB_HOST}:${DB_PORT}..."
for i in {1..150}; do
    if mysqladmin ping -h"${DB_HOST}" -P"${DB_PORT}" -u"${WORDPRESS_DB_USER}" -p"${WORDPRESS_DB_PASSWORD}" --silent --skip-ssl 2>/dev/null; then
        echo "[shophosting.io] MySQL is up."
        break
    fi

    if [[ "$i" -eq 150 ]]; then
        echo "[shophosting.io] ERROR: MySQL did not become ready in time." >&2
        exit 1
    fi
    sleep 2
done

exec docker-entrypoint.sh "$@"
