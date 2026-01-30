#!/bin/bash
#
# MySQL Replication Setup Script
# Sets up source-replica replication between two MySQL servers
#
# Usage:
#   On PRIMARY: ./setup-mysql-replication.sh primary
#   On REPLICA: ./setup-mysql-replication.sh replica <primary-host> <repl-password>
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="$SCRIPT_DIR/../configs/mysql"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

setup_primary() {
    log_info "Setting up MySQL PRIMARY server..."

    # Check if running as root
    if [[ $EUID -ne 0 ]]; then
        log_error "This script must be run as root"
        exit 1
    fi

    # Copy configuration
    log_info "Installing replication configuration..."
    cp "$CONFIG_DIR/primary.cnf" /etc/mysql/mysql.conf.d/replication.cnf

    # Restart MySQL to apply config
    log_info "Restarting MySQL..."
    systemctl restart mysql

    # Wait for MySQL to start
    sleep 5

    # Create replication user
    log_info "Creating replication user..."
    read -sp "Enter password for replication user: " REPL_PASSWORD
    echo

    mysql -u root <<EOF
-- Create replication user
CREATE USER IF NOT EXISTS 'repl_user'@'%' IDENTIFIED BY '$REPL_PASSWORD';
GRANT REPLICATION SLAVE ON *.* TO 'repl_user'@'%';

-- Create read-only user for application
CREATE USER IF NOT EXISTS 'shophosting_read'@'%' IDENTIFIED BY '$REPL_PASSWORD';
GRANT SELECT ON shophosting_db.* TO 'shophosting_read'@'%';

FLUSH PRIVILEGES;
EOF

    # Get binary log position
    log_info "Getting binary log position..."
    mysql -u root -e "SHOW MASTER STATUS\G"

    log_info ""
    log_info "PRIMARY setup complete!"
    log_info ""
    log_info "Next steps:"
    log_info "1. Note the File and Position from SHOW MASTER STATUS above"
    log_info "2. Create a backup: mysqldump --all-databases --source-data > backup.sql"
    log_info "3. Copy backup.sql to replica server"
    log_info "4. On replica, run: ./setup-mysql-replication.sh replica <this-host> <repl-password>"
    log_info ""
    log_info "Firewall: Ensure port 3306 is open to the replica server"
}

setup_replica() {
    local PRIMARY_HOST="${1:-}"
    local REPL_PASSWORD="${2:-}"

    if [[ -z "$PRIMARY_HOST" ]]; then
        log_error "Usage: $0 replica <primary-host> <repl-password>"
        exit 1
    fi

    if [[ -z "$REPL_PASSWORD" ]]; then
        read -sp "Enter replication user password: " REPL_PASSWORD
        echo
    fi

    log_info "Setting up MySQL REPLICA server..."

    # Check if running as root
    if [[ $EUID -ne 0 ]]; then
        log_error "This script must be run as root"
        exit 1
    fi

    # Copy configuration
    log_info "Installing replication configuration..."
    cp "$CONFIG_DIR/replica.cnf" /etc/mysql/mysql.conf.d/replication.cnf

    # Restart MySQL to apply config
    log_info "Restarting MySQL..."
    systemctl restart mysql
    sleep 5

    # Configure replication
    log_info "Configuring replication from $PRIMARY_HOST..."

    mysql -u root <<EOF
-- Stop any existing replication
STOP REPLICA;
RESET REPLICA ALL;

-- Configure connection to primary using GTID
CHANGE REPLICATION SOURCE TO
    SOURCE_HOST='$PRIMARY_HOST',
    SOURCE_USER='repl_user',
    SOURCE_PASSWORD='$REPL_PASSWORD',
    SOURCE_AUTO_POSITION=1,
    SOURCE_SSL=0;

-- Start replication
START REPLICA;
EOF

    # Wait and check status
    sleep 3
    log_info "Checking replication status..."
    mysql -u root -e "SHOW REPLICA STATUS\G" | grep -E "(Replica_IO_Running|Replica_SQL_Running|Seconds_Behind_Source|Last_Error)"

    log_info ""
    log_info "REPLICA setup complete!"
    log_info ""
    log_info "Verify replication is working:"
    log_info "  mysql -u root -e 'SHOW REPLICA STATUS\\G'"
    log_info ""
    log_info "Expected: Replica_IO_Running: Yes, Replica_SQL_Running: Yes"
}

check_replication() {
    log_info "Checking replication status..."

    # Check if this is primary or replica
    local is_replica=$(mysql -u root -N -e "SHOW REPLICA STATUS" 2>/dev/null | wc -l)

    if [[ $is_replica -gt 0 ]]; then
        log_info "This server is configured as a REPLICA"
        mysql -u root -e "SHOW REPLICA STATUS\G" | grep -E "(Replica_IO_Running|Replica_SQL_Running|Seconds_Behind_Source|Last_Error|Source_Host)"
    else
        log_info "This server is configured as a PRIMARY"
        mysql -u root -e "SHOW MASTER STATUS\G"
        mysql -u root -e "SHOW REPLICAS"
    fi
}

# Main
case "${1:-help}" in
    primary)
        setup_primary
        ;;
    replica)
        setup_replica "${2:-}" "${3:-}"
        ;;
    status)
        check_replication
        ;;
    *)
        echo "MySQL Replication Setup Script"
        echo ""
        echo "Usage:"
        echo "  $0 primary              - Set up this server as PRIMARY"
        echo "  $0 replica <host> [pw]  - Set up this server as REPLICA"
        echo "  $0 status               - Check replication status"
        echo ""
        exit 1
        ;;
esac
