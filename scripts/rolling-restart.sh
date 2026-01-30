#!/bin/bash
#
# ShopHosting.io Rolling Restart
#
# Performs a zero-downtime rolling restart of webapp instances.
# Restarts one instance at a time, waiting for it to become healthy
# before restarting the next.
#
# Usage:
#   ./rolling-restart.sh           - Restart all webapp instances
#   ./rolling-restart.sh --status  - Show status of all instances
#

set -euo pipefail

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

# Health check settings
HEALTH_CHECK_URL="http://127.0.0.1:500%d/health"
HEALTH_CHECK_TIMEOUT=5
HEALTH_CHECK_RETRIES=30
HEALTH_CHECK_INTERVAL=1

check_health() {
    local port=$1
    local url="http://127.0.0.1:$port/health"

    if curl -sf -o /dev/null -m "$HEALTH_CHECK_TIMEOUT" "$url" 2>/dev/null; then
        return 0
    fi

    # Fallback: just check if the port is listening
    if nc -z 127.0.0.1 "$port" 2>/dev/null; then
        return 0
    fi

    return 1
}

wait_for_healthy() {
    local instance=$1
    local port="500$instance"

    log_info "Waiting for instance $instance (port $port) to become healthy..."

    for ((i=1; i<=HEALTH_CHECK_RETRIES; i++)); do
        if check_health "$port"; then
            log_info "Instance $instance is healthy!"
            return 0
        fi
        echo -n "."
        sleep "$HEALTH_CHECK_INTERVAL"
    done

    echo ""
    log_error "Instance $instance failed to become healthy after ${HEALTH_CHECK_RETRIES}s"
    return 1
}

get_running_instances() {
    systemctl list-units --type=service --state=running --no-pager \
        | grep -oP 'shophosting-webapp@\K[0-9]+(?=\.service)' \
        | sort -n
}

show_status() {
    log_info "Webapp instance status:"
    echo ""

    for i in $(seq 0 9); do
        service="shophosting-webapp@$i.service"
        if systemctl list-units --all --type=service --no-pager | grep -q "$service"; then
            status=$(systemctl is-active "$service" 2>/dev/null || echo "unknown")
            port="500$i"

            if [[ "$status" == "active" ]]; then
                if check_health "$port"; then
                    echo -e "  ${GREEN}●${NC} $service (port $port) - $status, healthy"
                else
                    echo -e "  ${YELLOW}●${NC} $service (port $port) - $status, NOT responding"
                fi
            else
                echo -e "  ${RED}●${NC} $service (port $port) - $status"
            fi
        fi
    done

    echo ""
}

rolling_restart() {
    local instances
    instances=$(get_running_instances)

    if [[ -z "$instances" ]]; then
        log_error "No running webapp instances found!"
        log_error "Start instances with: sudo ./scripts/setup-load-balancing.sh 2"
        exit 1
    fi

    local count
    count=$(echo "$instances" | wc -w)

    log_info "Found $count running webapp instance(s): $instances"

    if [[ $count -lt 2 ]]; then
        log_warn "Only one instance is running. Rolling restart will cause downtime!"
        read -p "Continue anyway? [y/N] " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 0
        fi
    fi

    echo ""
    log_info "Starting rolling restart..."

    for instance in $instances; do
        log_info "Restarting instance $instance..."

        systemctl restart "shophosting-webapp@$instance.service"

        if ! wait_for_healthy "$instance"; then
            log_error "Rolling restart failed at instance $instance"
            log_error "Check logs: journalctl -u shophosting-webapp@$instance.service -f"
            exit 1
        fi

        echo ""
    done

    log_info "Rolling restart completed successfully!"
    echo ""
    show_status
}

# Main
case "${1:-restart}" in
    --status|status|-s)
        show_status
        ;;
    --help|-h)
        echo "Usage: $0 [--status|--help]"
        echo ""
        echo "Commands:"
        echo "  (default)   Perform rolling restart of all webapp instances"
        echo "  --status    Show status of all instances"
        echo "  --help      Show this help"
        ;;
    *)
        rolling_restart
        ;;
esac
