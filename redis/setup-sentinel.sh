#!/bin/bash
#
# Redis Sentinel Setup Script
# Creates sentinel config files and starts the Redis HA cluster
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }

# Get or generate Redis password
if [[ -z "${REDIS_PASSWORD:-}" ]]; then
    if [[ -f ".redis-password" ]]; then
        REDIS_PASSWORD=$(cat .redis-password)
        log_info "Using existing Redis password from .redis-password"
    else
        REDIS_PASSWORD=$(openssl rand -base64 32 | tr -dc 'a-zA-Z0-9' | head -c 32)
        echo "$REDIS_PASSWORD" > .redis-password
        chmod 600 .redis-password
        log_info "Generated new Redis password (saved to .redis-password)"
    fi
fi

export REDIS_PASSWORD

# Create sentinel config files
log_info "Creating sentinel configuration files..."

for i in 1 2 3; do
    sed "s/REDIS_PASSWORD_PLACEHOLDER/$REDIS_PASSWORD/g" sentinel.conf.template > "sentinel-$i.conf"
    chmod 644 "sentinel-$i.conf"
done

log_info "Sentinel configs created: sentinel-1.conf, sentinel-2.conf, sentinel-3.conf"

# Start the cluster
log_info "Starting Redis Sentinel cluster..."
docker compose up -d

# Wait for services to start
log_info "Waiting for services to start..."
sleep 10

# Check status
log_info "Checking cluster status..."

echo ""
echo "=== Redis Master ==="
docker exec redis-master redis-cli -a "$REDIS_PASSWORD" INFO replication 2>/dev/null | grep -E "role:|connected_slaves:"

echo ""
echo "=== Sentinel Status ==="
docker exec redis-sentinel-1 redis-cli -p 26379 SENTINEL master mymaster 2>/dev/null | head -20

echo ""
log_info "Redis Sentinel cluster is running!"
echo ""
echo "Connection info for your application:"
echo "  REDIS_SENTINEL_HOSTS=localhost:26379,localhost:26380,localhost:26381"
echo "  REDIS_SENTINEL_MASTER=mymaster"
echo "  REDIS_PASSWORD=$REDIS_PASSWORD"
echo ""
echo "Add to .env file:"
echo "  REDIS_SENTINEL_HOSTS=localhost:26379,localhost:26380,localhost:26381"
echo "  REDIS_SENTINEL_MASTER=mymaster"
echo "  REDIS_PASSWORD=$REDIS_PASSWORD"
echo ""
