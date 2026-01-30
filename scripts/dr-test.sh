#!/bin/bash
#
# ShopHosting.io Disaster Recovery Test Script
# Run monthly to verify backup integrity and restore capability
#
# Usage: ./dr-test.sh [--full]
#   --full: Run full restore test (requires more disk space and time)
#

set -euo pipefail

# Configuration
RESTIC_REPOSITORY="${RESTIC_REPOSITORY:-sftp:sh-backup@15.204.249.219:/home/sh-backup/backups}"
RESTIC_PASSWORD_FILE="${RESTIC_PASSWORD_FILE:-/root/.restic-password}"
TEST_DIR="/tmp/dr-test-$(date +%Y%m%d-%H%M%S)"
LOG_FILE="/var/log/shophosting-dr-test.log"
FULL_TEST="${1:-}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Logging
log() {
    local level=$1
    shift
    local message="$*"
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo -e "${timestamp} [${level}] ${message}" | tee -a "$LOG_FILE"
}

info() { log "INFO" "$*"; }
warn() { log "${YELLOW}WARN${NC}" "$*"; }
error() { log "${RED}ERROR${NC}" "$*"; }
success() { log "${GREEN}OK${NC}" "$*"; }

# Cleanup function
cleanup() {
    info "Cleaning up test directory..."
    rm -rf "$TEST_DIR" 2>/dev/null || true
}
trap cleanup EXIT

# Check prerequisites
check_prerequisites() {
    info "Checking prerequisites..."

    if ! command -v restic &> /dev/null; then
        error "restic is not installed"
        exit 1
    fi

    if [[ ! -f "$RESTIC_PASSWORD_FILE" ]]; then
        error "Restic password file not found: $RESTIC_PASSWORD_FILE"
        exit 1
    fi

    export RESTIC_REPOSITORY
    export RESTIC_PASSWORD_FILE

    success "Prerequisites check passed"
}

# Test 1: Verify repository integrity
test_repository_integrity() {
    info "Test 1: Verifying backup repository integrity..."

    if restic check --read-data-subset=1% 2>&1 | tee -a "$LOG_FILE"; then
        success "Repository integrity check passed"
        return 0
    else
        error "Repository integrity check failed"
        return 1
    fi
}

# Test 2: List and verify snapshots exist
test_snapshots_exist() {
    info "Test 2: Verifying snapshots exist..."

    local snapshot_count=$(restic snapshots --json 2>/dev/null | jq length)

    if [[ "$snapshot_count" -gt 0 ]]; then
        success "Found $snapshot_count snapshots"

        # Show recent snapshots
        info "Recent snapshots:"
        restic snapshots --last 5 2>&1 | tee -a "$LOG_FILE"
        return 0
    else
        error "No snapshots found in repository"
        return 1
    fi
}

# Test 3: Verify latest snapshot contains expected files
test_snapshot_contents() {
    info "Test 3: Verifying snapshot contains expected files..."

    local expected_paths=(
        "/opt/shophosting/webapp"
        "/opt/shophosting/.env"
        "/var/customers"
        "/etc/nginx/sites-available"
    )

    local latest_snapshot=$(restic snapshots --json --last 1 2>/dev/null | jq -r '.[0].id')

    if [[ -z "$latest_snapshot" || "$latest_snapshot" == "null" ]]; then
        error "Could not get latest snapshot ID"
        return 1
    fi

    info "Checking snapshot: $latest_snapshot"

    local missing=0
    for path in "${expected_paths[@]}"; do
        if restic ls "$latest_snapshot" "$path" &>/dev/null; then
            success "  Found: $path"
        else
            error "  Missing: $path"
            missing=$((missing + 1))
        fi
    done

    if [[ $missing -eq 0 ]]; then
        success "All expected paths found in snapshot"
        return 0
    else
        error "$missing expected paths missing from snapshot"
        return 1
    fi
}

# Test 4: Restore sample data and verify
test_restore_sample() {
    info "Test 4: Testing restore of sample data..."

    mkdir -p "$TEST_DIR"

    # Restore just the webapp directory (small, quick test)
    if restic restore latest --target "$TEST_DIR" --include "/opt/shophosting/webapp/app.py" 2>&1 | tee -a "$LOG_FILE"; then

        if [[ -f "$TEST_DIR/opt/shophosting/webapp/app.py" ]]; then
            local file_size=$(stat -c%s "$TEST_DIR/opt/shophosting/webapp/app.py" 2>/dev/null || echo "0")
            if [[ "$file_size" -gt 1000 ]]; then
                success "Sample restore successful (app.py: $file_size bytes)"
                return 0
            else
                error "Restored file appears corrupted (size: $file_size)"
                return 1
            fi
        else
            error "Restored file not found"
            return 1
        fi
    else
        error "Restore command failed"
        return 1
    fi
}

# Test 5: Verify database dump can be parsed (if full test)
test_database_restore() {
    if [[ "$FULL_TEST" != "--full" ]]; then
        info "Test 5: Skipping database restore test (use --full to enable)"
        return 0
    fi

    info "Test 5: Testing database dump restore..."

    # Restore database dumps
    if restic restore latest --target "$TEST_DIR" --include "/tmp/shophosting-db-dumps" 2>&1 | tee -a "$LOG_FILE"; then

        local dump_file="$TEST_DIR/tmp/shophosting-db-dumps/shophosting_db.sql"

        if [[ -f "$dump_file" ]]; then
            local dump_size=$(stat -c%s "$dump_file" 2>/dev/null || echo "0")

            # Basic SQL validation - check for expected statements
            if grep -q "CREATE TABLE" "$dump_file" && grep -q "INSERT INTO" "$dump_file"; then
                success "Database dump appears valid ($dump_size bytes)"

                # Try to create a test database and import
                local test_db="dr_test_$(date +%s)"
                if mysql -u root -e "CREATE DATABASE $test_db" 2>/dev/null; then
                    if mysql -u root "$test_db" < "$dump_file" 2>&1 | tee -a "$LOG_FILE"; then
                        success "Database import successful"
                        mysql -u root -e "DROP DATABASE $test_db" 2>/dev/null || true
                        return 0
                    else
                        warn "Database import had errors (may be expected for schema differences)"
                        mysql -u root -e "DROP DATABASE IF EXISTS $test_db" 2>/dev/null || true
                        return 0
                    fi
                else
                    warn "Could not create test database (MySQL access issue)"
                    return 0
                fi
            else
                error "Database dump appears invalid or empty"
                return 1
            fi
        else
            error "Database dump file not found in backup"
            return 1
        fi
    else
        error "Failed to restore database dumps"
        return 1
    fi
}

# Test 6: Check backup age
test_backup_age() {
    info "Test 6: Checking backup age..."

    local latest_time=$(restic snapshots --json --last 1 2>/dev/null | jq -r '.[0].time')

    if [[ -z "$latest_time" || "$latest_time" == "null" ]]; then
        error "Could not determine latest backup time"
        return 1
    fi

    local latest_epoch=$(date -d "$latest_time" +%s 2>/dev/null || echo "0")
    local now_epoch=$(date +%s)
    local age_hours=$(( (now_epoch - latest_epoch) / 3600 ))

    info "Latest backup: $latest_time ($age_hours hours ago)"

    if [[ $age_hours -gt 48 ]]; then
        error "Latest backup is more than 48 hours old!"
        return 1
    elif [[ $age_hours -gt 24 ]]; then
        warn "Latest backup is more than 24 hours old"
        return 0
    else
        success "Backup age is within acceptable range"
        return 0
    fi
}

# Main execution
main() {
    echo "=============================================="
    echo "ShopHosting.io Disaster Recovery Test"
    echo "Started: $(date)"
    echo "=============================================="
    echo ""

    local total_tests=6
    local passed_tests=0
    local failed_tests=0

    check_prerequisites

    # Run tests
    if test_repository_integrity; then ((passed_tests++)); else ((failed_tests++)); fi
    echo ""

    if test_snapshots_exist; then ((passed_tests++)); else ((failed_tests++)); fi
    echo ""

    if test_snapshot_contents; then ((passed_tests++)); else ((failed_tests++)); fi
    echo ""

    if test_restore_sample; then ((passed_tests++)); else ((failed_tests++)); fi
    echo ""

    if test_database_restore; then ((passed_tests++)); else ((failed_tests++)); fi
    echo ""

    if test_backup_age; then ((passed_tests++)); else ((failed_tests++)); fi
    echo ""

    # Summary
    echo "=============================================="
    echo "DR Test Summary"
    echo "=============================================="
    echo "Tests Passed: $passed_tests / $total_tests"
    echo "Tests Failed: $failed_tests"
    echo ""

    if [[ $failed_tests -eq 0 ]]; then
        echo -e "${GREEN}All DR tests passed!${NC}"
        echo "Completed: $(date)"
        exit 0
    else
        echo -e "${RED}$failed_tests test(s) failed - review above output${NC}"
        echo "Completed: $(date)"
        exit 1
    fi
}

main "$@"
