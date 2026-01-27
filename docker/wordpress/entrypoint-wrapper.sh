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

# Run the original WordPress entrypoint to set up wp-config.php and copy files
# We run it in the background and wait for WordPress files to be ready
docker-entrypoint.sh apache2-foreground &
APACHE_PID=$!

# Wait for WordPress files to be set up
echo "[shophosting.io] waiting for WordPress files..."
for i in {1..60}; do
    if [[ -f /var/www/html/wp-config.php ]]; then
        echo "[shophosting.io] WordPress files ready."
        break
    fi
    if [[ "$i" -eq 60 ]]; then
        echo "[shophosting.io] WARNING: wp-config.php not found after 60s, continuing anyway..."
    fi
    sleep 1
done

# Check if WordPress is already installed
if ! wp core is-installed --allow-root 2>/dev/null; then
    echo "[shophosting.io] Installing WordPress..."

    # Get admin credentials from environment (with defaults)
    WP_ADMIN_USER="${WP_ADMIN_USER:-admin}"
    WP_ADMIN_PASSWORD="${WP_ADMIN_PASSWORD:-changeme}"
    WP_ADMIN_EMAIL="${WP_ADMIN_EMAIL:-admin@example.com}"
    WP_HOME="${WP_HOME:-http://localhost}"
    WP_SITE_TITLE="${WP_SITE_TITLE:-My Site}"

    # Install WordPress core
    wp core install \
        --url="${WP_HOME}" \
        --title="${WP_SITE_TITLE}" \
        --admin_user="${WP_ADMIN_USER}" \
        --admin_password="${WP_ADMIN_PASSWORD}" \
        --admin_email="${WP_ADMIN_EMAIL}" \
        --skip-email \
        --allow-root

    echo "[shophosting.io] WordPress installed successfully!"

    # Install and activate WooCommerce
    echo "[shophosting.io] Installing WooCommerce..."
    wp plugin install woocommerce --activate --allow-root || echo "[shophosting.io] WooCommerce install failed, may already exist"

    # Install Redis Object Cache plugin
    echo "[shophosting.io] Installing Redis Object Cache..."
    wp plugin install redis-cache --activate --allow-root || echo "[shophosting.io] Redis cache install failed, may already exist"

    # Enable Redis cache if WP_REDIS_HOST is set
    if [[ -n "${WP_REDIS_HOST:-}" ]]; then
        wp redis enable --allow-root 2>/dev/null || echo "[shophosting.io] Redis enable skipped"
    fi

    echo "[shophosting.io] WordPress setup complete!"
else
    echo "[shophosting.io] WordPress already installed, skipping installation."
fi

# Wait for the Apache process we started
wait $APACHE_PID
