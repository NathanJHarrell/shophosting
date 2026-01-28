# Customer Backups Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Enable customers to create manual backups and restore from manual or daily backups via their dashboard.

**Architecture:** Queue-based backup/restore jobs using Redis + RQ (same pattern as staging_worker). Restic for backup storage with tag-based filtering per customer. Maintenance mode during restores.

**Tech Stack:** Python/Flask, Redis/RQ, restic, bash scripts, MySQL

---

## Task 1: Database Migration

**Files:**
- Create: `/opt/shophosting/migrations/006_customer_backup_jobs.sql`

**Step 1: Create migration file**

```sql
-- Migration: Create customer_backup_jobs table
-- Date: 2026-01-27

CREATE TABLE IF NOT EXISTS customer_backup_jobs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    customer_id INT NOT NULL,
    job_type ENUM('backup', 'restore') NOT NULL,
    backup_type ENUM('db', 'files', 'both') NOT NULL,
    snapshot_id VARCHAR(64) NULL,
    status ENUM('pending', 'running', 'completed', 'failed') DEFAULT 'pending',
    error_message TEXT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
    INDEX idx_customer_status (customer_id, status),
    INDEX idx_created_at (created_at)
);
```

**Step 2: Run migration**

Run: `mysql -u shophosting_app -p shophosting_db < /opt/shophosting/migrations/006_customer_backup_jobs.sql`
Expected: Query OK

**Step 3: Verify table exists**

Run: `mysql -u shophosting_app -p shophosting_db -e "DESCRIBE customer_backup_jobs"`
Expected: Table schema displayed

**Step 4: Commit**

```bash
git add migrations/006_customer_backup_jobs.sql
git commit -m "feat(db): add customer_backup_jobs table"
```

---

## Task 2: CustomerBackupJob Model

**Files:**
- Modify: `/opt/shophosting/webapp/models.py` (append after StagingEnvironment class, around line 1760)

**Step 1: Add CustomerBackupJob class**

Add to end of models.py (before any `if __name__` block if present):

```python
# =============================================================================
# Customer Backup Job Model
# =============================================================================

class CustomerBackupJob:
    """Tracks customer backup and restore operations"""

    def __init__(self, id=None, customer_id=None, job_type=None, backup_type=None,
                 snapshot_id=None, status='pending', error_message=None,
                 created_at=None, completed_at=None):
        self.id = id
        self.customer_id = customer_id
        self.job_type = job_type  # 'backup' or 'restore'
        self.backup_type = backup_type  # 'db', 'files', or 'both'
        self.snapshot_id = snapshot_id
        self.status = status
        self.error_message = error_message
        self.created_at = created_at or datetime.now()
        self.completed_at = completed_at

    def save(self):
        """Save job to database"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            if self.id is None:
                cursor.execute("""
                    INSERT INTO customer_backup_jobs
                    (customer_id, job_type, backup_type, snapshot_id, status,
                     error_message, created_at, completed_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    self.customer_id, self.job_type, self.backup_type,
                    self.snapshot_id, self.status, self.error_message,
                    self.created_at, self.completed_at
                ))
                self.id = cursor.lastrowid
            else:
                cursor.execute("""
                    UPDATE customer_backup_jobs SET
                        status = %s, error_message = %s, completed_at = %s
                    WHERE id = %s
                """, (self.status, self.error_message, self.completed_at, self.id))

            conn.commit()
            return self
        finally:
            cursor.close()
            conn.close()

    def update_status(self, status, error_message=None):
        """Update job status"""
        self.status = status
        self.error_message = error_message
        if status in ('completed', 'failed'):
            self.completed_at = datetime.now()
        self.save()

    @staticmethod
    def get_by_id(job_id):
        """Get job by ID"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("SELECT * FROM customer_backup_jobs WHERE id = %s", (job_id,))
            row = cursor.fetchone()
            if row:
                return CustomerBackupJob(**row)
            return None
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_active_job(customer_id):
        """Get active (pending/running) job for customer"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("""
                SELECT * FROM customer_backup_jobs
                WHERE customer_id = %s AND status IN ('pending', 'running')
                ORDER BY created_at DESC LIMIT 1
            """, (customer_id,))
            row = cursor.fetchone()
            if row:
                return CustomerBackupJob(**row)
            return None
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_recent_jobs(customer_id, limit=10):
        """Get recent jobs for customer"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("""
                SELECT * FROM customer_backup_jobs
                WHERE customer_id = %s
                ORDER BY created_at DESC LIMIT %s
            """, (customer_id, limit))
            return [CustomerBackupJob(**row) for row in cursor.fetchall()]
        finally:
            cursor.close()
            conn.close()

    def to_dict(self):
        """Convert to dictionary"""
        return {
            'id': self.id,
            'customer_id': self.customer_id,
            'job_type': self.job_type,
            'backup_type': self.backup_type,
            'snapshot_id': self.snapshot_id,
            'status': self.status,
            'error_message': self.error_message,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None
        }
```

**Step 2: Verify syntax**

Run: `python3 -m py_compile /opt/shophosting/webapp/models.py`
Expected: No output (success)

**Step 3: Commit**

```bash
git add webapp/models.py
git commit -m "feat(models): add CustomerBackupJob model"
```

---

## Task 3: Customer Backup Script

**Files:**
- Create: `/opt/shophosting/scripts/customer-backup.sh`

**Step 1: Create the backup script**

```bash
#!/bin/bash
# Customer Manual Backup Script
# Creates a backup for a single customer to the manual backups repository
# Usage: customer-backup.sh <customer_id> <backup_type: db|files|both>

set -euo pipefail

# Arguments
CUSTOMER_ID="${1:-}"
BACKUP_TYPE="${2:-both}"

if [ -z "$CUSTOMER_ID" ]; then
    echo "Usage: $0 <customer_id> <backup_type: db|files|both>"
    exit 1
fi

# Configuration
RESTIC_REPOSITORY="sftp:sh-backup@15.204.249.219:/home/sh-backup/manual-backups"
RESTIC_PASSWORD_FILE="/opt/shophosting/.manual-restic-password"
CUSTOMER_PATH="/var/customers/customer-${CUSTOMER_ID}"
DB_DUMP_DIR="/tmp/customer-backup-${CUSTOMER_ID}"
MAX_BACKUPS=5
TIMESTAMP=$(date +%Y-%m-%d-%H%M%S)

# Load environment
source /opt/shophosting/.env

# Export for restic
export RESTIC_REPOSITORY
export RESTIC_PASSWORD_FILE
export HOME=/root
export XDG_CACHE_HOME=/root/.cache

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

error_exit() {
    log "ERROR: $1"
    exit 1
}

# Validate customer directory exists
if [ ! -d "$CUSTOMER_PATH" ]; then
    error_exit "Customer directory not found: $CUSTOMER_PATH"
fi

log "Starting backup for customer $CUSTOMER_ID (type: $BACKUP_TYPE)"

# Prepare backup paths
BACKUP_PATHS=()
BACKUP_TAGS=("customer-${CUSTOMER_ID}" "manual" "$BACKUP_TYPE" "$TIMESTAMP")

# Handle database backup
if [ "$BACKUP_TYPE" = "db" ] || [ "$BACKUP_TYPE" = "both" ]; then
    log "Dumping customer database..."
    mkdir -p "$DB_DUMP_DIR"
    rm -f "$DB_DUMP_DIR"/*.sql

    # Get customer database name (customer_ID format)
    CUSTOMER_DB="customer_${CUSTOMER_ID}"

    # Dump the database
    mysqldump -h "${DB_HOST:-localhost}" \
        -u "${DB_USER:-shophosting_app}" \
        -p"${DB_PASSWORD}" \
        --single-transaction \
        "$CUSTOMER_DB" > "$DB_DUMP_DIR/${CUSTOMER_DB}.sql" 2>/dev/null \
        || error_exit "Failed to dump database $CUSTOMER_DB"

    log "Database dump complete: $(du -h "$DB_DUMP_DIR/${CUSTOMER_DB}.sql" | cut -f1)"
    BACKUP_PATHS+=("$DB_DUMP_DIR")
fi

# Handle files backup
if [ "$BACKUP_TYPE" = "files" ] || [ "$BACKUP_TYPE" = "both" ]; then
    log "Adding customer files to backup..."
    BACKUP_PATHS+=("$CUSTOMER_PATH")
fi

# Run restic backup
log "Running restic backup..."
TAG_ARGS=""
for tag in "${BACKUP_TAGS[@]}"; do
    TAG_ARGS="$TAG_ARGS --tag $tag"
done

restic backup $TAG_ARGS "${BACKUP_PATHS[@]}" \
    || error_exit "Restic backup failed"

# Get the snapshot ID of the backup we just created
SNAPSHOT_ID=$(restic snapshots --json --latest 1 --tag "customer-${CUSTOMER_ID}" --tag "manual" 2>/dev/null \
    | python3 -c "import sys,json; data=json.load(sys.stdin); print(data[0]['id'] if data else '')" 2>/dev/null)

log "Backup complete. Snapshot ID: $SNAPSHOT_ID"

# Enforce retention: keep only MAX_BACKUPS per customer
log "Enforcing retention policy (max $MAX_BACKUPS backups)..."
CUSTOMER_SNAPSHOTS=$(restic snapshots --json --tag "customer-${CUSTOMER_ID}" --tag "manual" 2>/dev/null \
    | python3 -c "import sys,json; data=json.load(sys.stdin); print(len(data))" 2>/dev/null || echo "0")

if [ "$CUSTOMER_SNAPSHOTS" -gt "$MAX_BACKUPS" ]; then
    log "Customer has $CUSTOMER_SNAPSHOTS backups, pruning to $MAX_BACKUPS..."
    restic forget --tag "customer-${CUSTOMER_ID}" --tag "manual" --keep-last "$MAX_BACKUPS" --prune \
        || log "Warning: Retention enforcement failed"
fi

# Cleanup
if [ -d "$DB_DUMP_DIR" ]; then
    rm -rf "$DB_DUMP_DIR"
fi

log "Backup completed successfully"
echo "SNAPSHOT_ID=$SNAPSHOT_ID"
exit 0
```

**Step 2: Make executable**

Run: `chmod +x /opt/shophosting/scripts/customer-backup.sh`

**Step 3: Commit**

```bash
git add scripts/customer-backup.sh
git commit -m "feat(scripts): add customer-backup.sh for manual backups"
```

---

## Task 4: Customer Restore Script

**Files:**
- Create: `/opt/shophosting/scripts/customer-restore.sh`

**Step 1: Create the restore script**

```bash
#!/bin/bash
# Customer Restore Script
# Restores a customer's site from a backup snapshot
# Usage: customer-restore.sh <customer_id> <snapshot_id> <restore_type: db|files|both> <source: manual|daily>

set -euo pipefail

# Arguments
CUSTOMER_ID="${1:-}"
SNAPSHOT_ID="${2:-}"
RESTORE_TYPE="${3:-both}"
SOURCE="${4:-manual}"

if [ -z "$CUSTOMER_ID" ] || [ -z "$SNAPSHOT_ID" ]; then
    echo "Usage: $0 <customer_id> <snapshot_id> <restore_type: db|files|both> <source: manual|daily>"
    exit 1
fi

# Configuration based on source
if [ "$SOURCE" = "manual" ]; then
    RESTIC_REPOSITORY="sftp:sh-backup@15.204.249.219:/home/sh-backup/manual-backups"
    RESTIC_PASSWORD_FILE="/opt/shophosting/.manual-restic-password"
else
    RESTIC_REPOSITORY="sftp:sh-backup@15.204.249.219:/home/sh-backup/backups"
    RESTIC_PASSWORD_FILE="/root/.restic-password"
fi

CUSTOMER_PATH="/var/customers/customer-${CUSTOMER_ID}"
RESTORE_DIR="/tmp/restore-customer-${CUSTOMER_ID}-$(date +%s)"
MAINTENANCE_FILE="${CUSTOMER_PATH}/.maintenance"

# Load environment
source /opt/shophosting/.env

# Export for restic
export RESTIC_REPOSITORY
export RESTIC_PASSWORD_FILE
export HOME=/root
export XDG_CACHE_HOME=/root/.cache

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

error_exit() {
    log "ERROR: $1"
    # Always try to disable maintenance mode on error
    rm -f "$MAINTENANCE_FILE" 2>/dev/null || true
    exit 1
}

cleanup() {
    log "Cleaning up..."
    rm -rf "$RESTORE_DIR" 2>/dev/null || true
    rm -f "$MAINTENANCE_FILE" 2>/dev/null || true
}

trap cleanup EXIT

# Validate customer directory exists
if [ ! -d "$CUSTOMER_PATH" ]; then
    error_exit "Customer directory not found: $CUSTOMER_PATH"
fi

# Verify snapshot exists and belongs to this customer
log "Verifying snapshot $SNAPSHOT_ID..."
if [ "$SOURCE" = "manual" ]; then
    # For manual backups, verify customer tag
    SNAPSHOT_INFO=$(restic snapshots --json "$SNAPSHOT_ID" 2>/dev/null | python3 -c "
import sys, json
data = json.load(sys.stdin)
if data:
    tags = data[0].get('tags', [])
    if 'customer-${CUSTOMER_ID}' in tags:
        print('valid')
    else:
        print('invalid')
else:
    print('notfound')
" 2>/dev/null)
else
    # For daily backups, verify snapshot contains customer path
    SNAPSHOT_INFO=$(restic ls "$SNAPSHOT_ID" 2>/dev/null | grep -q "/var/customers/customer-${CUSTOMER_ID}" && echo "valid" || echo "invalid")
fi

if [ "$SNAPSHOT_INFO" != "valid" ]; then
    error_exit "Snapshot $SNAPSHOT_ID not found or does not belong to customer $CUSTOMER_ID"
fi

log "Starting restore for customer $CUSTOMER_ID from snapshot $SNAPSHOT_ID (type: $RESTORE_TYPE)"

# Enable maintenance mode
log "Enabling maintenance mode..."
touch "$MAINTENANCE_FILE"

# Stop customer containers
log "Stopping customer containers..."
cd "$CUSTOMER_PATH"
docker compose down 2>/dev/null || log "Warning: Could not stop containers (may not be running)"

# Create restore directory
mkdir -p "$RESTORE_DIR"

# Restore files if requested
if [ "$RESTORE_TYPE" = "files" ] || [ "$RESTORE_TYPE" = "both" ]; then
    log "Restoring customer files..."

    restic restore "$SNAPSHOT_ID" \
        --target "$RESTORE_DIR" \
        --include "/var/customers/customer-${CUSTOMER_ID}" \
        || error_exit "File restore failed"

    # Determine what to restore (wordpress or volumes/files)
    if [ -d "$RESTORE_DIR/var/customers/customer-${CUSTOMER_ID}/wordpress" ]; then
        RESTORED_FILES="$RESTORE_DIR/var/customers/customer-${CUSTOMER_ID}/wordpress"
        TARGET_FILES="$CUSTOMER_PATH/wordpress"
    elif [ -d "$RESTORE_DIR/var/customers/customer-${CUSTOMER_ID}/volumes/files" ]; then
        RESTORED_FILES="$RESTORE_DIR/var/customers/customer-${CUSTOMER_ID}/volumes/files"
        TARGET_FILES="$CUSTOMER_PATH/volumes/files"
    else
        log "Warning: No files found in snapshot for customer path"
        RESTORED_FILES=""
    fi

    if [ -n "$RESTORED_FILES" ] && [ -d "$RESTORED_FILES" ]; then
        # Backup current files
        BACKUP_SUFFIX=$(date +%Y%m%d%H%M%S)
        if [ -d "$TARGET_FILES" ]; then
            mv "$TARGET_FILES" "${TARGET_FILES}.pre-restore-${BACKUP_SUFFIX}"
        fi

        # Move restored files into place
        mv "$RESTORED_FILES" "$TARGET_FILES"
        log "Files restored successfully"

        # Cleanup old backup after successful restore
        rm -rf "${TARGET_FILES}.pre-restore-${BACKUP_SUFFIX}" 2>/dev/null || true
    fi
fi

# Restore database if requested
if [ "$RESTORE_TYPE" = "db" ] || [ "$RESTORE_TYPE" = "both" ]; then
    log "Restoring customer database..."

    CUSTOMER_DB="customer_${CUSTOMER_ID}"

    # For manual backups, SQL is in /tmp/customer-backup-ID/
    # For daily backups, SQL is in /tmp/shophosting-db-dumps/
    if [ "$SOURCE" = "manual" ]; then
        SQL_PATH="/tmp/customer-backup-${CUSTOMER_ID}/${CUSTOMER_DB}.sql"
    else
        SQL_PATH="/tmp/shophosting-db-dumps/${CUSTOMER_DB}.sql"
    fi

    # Restore SQL dump from snapshot
    restic restore "$SNAPSHOT_ID" \
        --target "$RESTORE_DIR" \
        --include "$SQL_PATH" \
        || log "Warning: Could not restore database dump"

    RESTORED_SQL="$RESTORE_DIR$SQL_PATH"

    if [ -f "$RESTORED_SQL" ]; then
        log "Importing database from $RESTORED_SQL..."
        mysql -h "${DB_HOST:-localhost}" \
            -u "${DB_USER:-shophosting_app}" \
            -p"${DB_PASSWORD}" \
            "$CUSTOMER_DB" < "$RESTORED_SQL" \
            || error_exit "Database import failed"
        log "Database restored successfully"
    else
        log "Warning: No database dump found in snapshot"
    fi
fi

# Start customer containers
log "Starting customer containers..."
cd "$CUSTOMER_PATH"
docker compose up -d || error_exit "Failed to start containers"

# Wait for containers to be ready
log "Waiting for containers to be ready..."
sleep 10

# Disable maintenance mode (handled by trap, but do explicitly)
rm -f "$MAINTENANCE_FILE"

log "Restore completed successfully"
exit 0
```

**Step 2: Make executable**

Run: `chmod +x /opt/shophosting/scripts/customer-restore.sh`

**Step 3: Commit**

```bash
git add scripts/customer-restore.sh
git commit -m "feat(scripts): add customer-restore.sh for restoring backups"
```

---

## Task 5: Backup Worker

**Files:**
- Create: `/opt/shophosting/provisioning/backup_worker.py`

**Step 1: Create the worker**

```python
"""
ShopHosting.io Backup Worker - Handles customer backup and restore jobs
"""

import os
import subprocess
import logging
import sys
import re
from pathlib import Path
from datetime import datetime

sys.path.insert(0, '/opt/shophosting/webapp')
from models import Customer, CustomerBackupJob
from dotenv import load_dotenv

# Load environment variables
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(env_path)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/opt/shophosting/logs/backup_worker.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class BackupError(Exception):
    """Custom exception for backup operations"""
    pass


class BackupWorker:
    """Handles customer backup and restore operations"""

    def __init__(self):
        self.scripts_path = Path('/opt/shophosting/scripts')

    def create_backup(self, job_id):
        """
        Create a manual backup for a customer.
        """
        job = CustomerBackupJob.get_by_id(job_id)
        if not job:
            raise BackupError(f"Job {job_id} not found")

        customer = Customer.get_by_id(job.customer_id)
        if not customer:
            raise BackupError(f"Customer {job.customer_id} not found")

        logger.info(f"Starting backup job {job_id} for customer {customer.id} (type: {job.backup_type})")

        # Update job status to running
        job.update_status('running')

        try:
            # Run backup script
            result = subprocess.run(
                ['sudo', str(self.scripts_path / 'customer-backup.sh'),
                 str(customer.id), job.backup_type],
                capture_output=True,
                text=True,
                timeout=600  # 10 minute timeout
            )

            if result.returncode != 0:
                raise BackupError(f"Backup script failed: {result.stderr}")

            # Extract snapshot ID from output
            snapshot_id = None
            for line in result.stdout.split('\n'):
                if line.startswith('SNAPSHOT_ID='):
                    snapshot_id = line.split('=')[1].strip()
                    break

            job.snapshot_id = snapshot_id
            job.update_status('completed')

            logger.info(f"Backup job {job_id} completed successfully. Snapshot: {snapshot_id}")
            return snapshot_id

        except subprocess.TimeoutExpired:
            job.update_status('failed', 'Backup timed out after 10 minutes')
            raise BackupError("Backup timed out")

        except Exception as e:
            logger.error(f"Backup job {job_id} failed: {e}")
            job.update_status('failed', str(e))
            raise

    def restore_backup(self, job_id):
        """
        Restore a customer's site from a backup snapshot.
        """
        job = CustomerBackupJob.get_by_id(job_id)
        if not job:
            raise BackupError(f"Job {job_id} not found")

        if not job.snapshot_id:
            raise BackupError("No snapshot ID specified for restore")

        customer = Customer.get_by_id(job.customer_id)
        if not customer:
            raise BackupError(f"Customer {job.customer_id} not found")

        logger.info(f"Starting restore job {job_id} for customer {customer.id} "
                   f"(snapshot: {job.snapshot_id}, type: {job.backup_type})")

        # Update job status to running
        job.update_status('running')

        try:
            # Determine source (manual or daily) based on snapshot tags
            # Manual backups have 'manual' tag, daily backups have 'daily' tag
            source = self._determine_backup_source(job.snapshot_id, customer.id)

            # Run restore script
            result = subprocess.run(
                ['sudo', str(self.scripts_path / 'customer-restore.sh'),
                 str(customer.id), job.snapshot_id, job.backup_type, source],
                capture_output=True,
                text=True,
                timeout=1200  # 20 minute timeout for restores
            )

            if result.returncode != 0:
                raise BackupError(f"Restore script failed: {result.stderr}")

            job.update_status('completed')

            logger.info(f"Restore job {job_id} completed successfully")
            return True

        except subprocess.TimeoutExpired:
            job.update_status('failed', 'Restore timed out after 20 minutes')
            raise BackupError("Restore timed out")

        except Exception as e:
            logger.error(f"Restore job {job_id} failed: {e}")
            job.update_status('failed', str(e))
            raise

    def _determine_backup_source(self, snapshot_id, customer_id):
        """Determine if snapshot is from manual or daily backups"""
        # Try manual repository first
        try:
            result = subprocess.run(
                ['sudo', 'bash', '-c',
                 f'export RESTIC_REPOSITORY="sftp:sh-backup@15.204.249.219:/home/sh-backup/manual-backups" && '
                 f'export RESTIC_PASSWORD_FILE="/opt/shophosting/.manual-restic-password" && '
                 f'restic snapshots --json {snapshot_id}'],
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode == 0 and snapshot_id in result.stdout:
                return 'manual'
        except:
            pass

        # Fall back to daily repository
        return 'daily'


# =============================================================================
# RQ Job Functions
# =============================================================================

def create_backup_job(job_id):
    """RQ job wrapper for creating backup"""
    worker = BackupWorker()
    return worker.create_backup(job_id)


def restore_backup_job(job_id):
    """RQ job wrapper for restoring backup"""
    worker = BackupWorker()
    return worker.restore_backup(job_id)


if __name__ == '__main__':
    # Run as RQ worker
    from redis import Redis
    from rq import Worker, Queue

    redis_host = os.getenv('REDIS_HOST', 'localhost')
    redis_conn = Redis(host=redis_host, port=6379)

    queues = [Queue('backups', connection=redis_conn)]

    logger.info("Starting backup worker...")
    worker = Worker(queues, connection=redis_conn)
    worker.work()
```

**Step 2: Verify syntax**

Run: `python3 -m py_compile /opt/shophosting/provisioning/backup_worker.py`
Expected: No output (success)

**Step 3: Commit**

```bash
git add provisioning/backup_worker.py
git commit -m "feat(worker): add backup_worker.py for backup/restore jobs"
```

---

## Task 6: Systemd Service for Backup Worker

**Files:**
- Create: `/opt/shophosting/shophosting-backup-worker.service`

**Step 1: Create service file**

```ini
[Unit]
Description=ShopHosting.io Backup Worker
After=network.target redis-server.service mysql.service

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=/opt/shophosting/provisioning
Environment="PATH=/opt/shophosting/provisioning/venv/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=/opt/shophosting/provisioning/venv/bin/python /opt/shophosting/provisioning/backup_worker.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

**Step 2: Commit**

```bash
git add shophosting-backup-worker.service
git commit -m "feat(systemd): add backup worker service"
```

---

## Task 7: Flask Routes for Backups

**Files:**
- Modify: `/opt/shophosting/webapp/app.py`

**Step 1: Add import for CustomerBackupJob**

Find the imports section (around line 27) and add CustomerBackupJob:

```python
from models import Customer, PortManager, PricingPlan, Subscription, Invoice, init_db_pool
from models import Ticket, TicketMessage, TicketAttachment, TicketCategory
from models import StagingEnvironment, StagingPortManager
from models import CustomerBackupJob  # Add this line
```

**Step 2: Add backup routes**

Add before the Error Handlers section (around line 910):

```python
# =============================================================================
# Customer Backup Routes
# =============================================================================

@app.route('/backups')
@login_required
def backups():
    """Customer backups page"""
    customer = Customer.get_by_id(current_user.id)
    active_job = CustomerBackupJob.get_active_job(customer.id)
    recent_jobs = CustomerBackupJob.get_recent_jobs(customer.id, limit=5)

    # Get manual backups from restic
    manual_backups = get_customer_manual_backups(customer.id)

    # Get daily backups (filtered to this customer's data)
    daily_backups = get_customer_daily_backups(customer.id)

    return render_template('backups.html',
                          customer=customer,
                          active_job=active_job,
                          recent_jobs=recent_jobs,
                          manual_backups=manual_backups,
                          daily_backups=daily_backups)


@app.route('/backups/create', methods=['POST'])
@login_required
def backup_create():
    """Create a manual backup"""
    customer = Customer.get_by_id(current_user.id)

    # Check for active job
    active_job = CustomerBackupJob.get_active_job(customer.id)
    if active_job:
        return jsonify({
            'success': False,
            'message': 'A backup operation is already in progress'
        }), 400

    backup_type = request.form.get('backup_type', 'both')
    if backup_type not in ('db', 'files', 'both'):
        return jsonify({'success': False, 'message': 'Invalid backup type'}), 400

    try:
        # Create job record
        job = CustomerBackupJob(
            customer_id=customer.id,
            job_type='backup',
            backup_type=backup_type,
            status='pending'
        )
        job.save()

        # Queue the job
        from redis import Redis
        from rq import Queue

        redis_host = os.getenv('REDIS_HOST', 'localhost')
        redis_conn = Redis(host=redis_host, port=6379)
        queue = Queue('backups', connection=redis_conn)

        from backup_worker import create_backup_job
        queue.enqueue(create_backup_job, job.id, job_timeout=700)

        logger.info(f"Backup job {job.id} queued for customer {customer.id}")
        return jsonify({'success': True, 'message': 'Backup started', 'job_id': job.id})

    except Exception as e:
        logger.error(f"Failed to queue backup: {e}")
        return jsonify({'success': False, 'message': 'Failed to start backup'}), 500


@app.route('/backups/<snapshot_id>/restore', methods=['POST'])
@login_required
def backup_restore(snapshot_id):
    """Restore from a backup"""
    customer = Customer.get_by_id(current_user.id)

    # Check for active job
    active_job = CustomerBackupJob.get_active_job(customer.id)
    if active_job:
        return jsonify({
            'success': False,
            'message': 'A backup operation is already in progress'
        }), 400

    # Verify confirmation
    confirmation = request.form.get('confirmation', '')
    if confirmation != 'RESTORE':
        return jsonify({
            'success': False,
            'message': 'Please type RESTORE to confirm'
        }), 400

    restore_type = request.form.get('restore_type', 'both')
    if restore_type not in ('db', 'files', 'both'):
        return jsonify({'success': False, 'message': 'Invalid restore type'}), 400

    try:
        # Create job record
        job = CustomerBackupJob(
            customer_id=customer.id,
            job_type='restore',
            backup_type=restore_type,
            snapshot_id=snapshot_id,
            status='pending'
        )
        job.save()

        # Queue the job
        from redis import Redis
        from rq import Queue

        redis_host = os.getenv('REDIS_HOST', 'localhost')
        redis_conn = Redis(host=redis_host, port=6379)
        queue = Queue('backups', connection=redis_conn)

        from backup_worker import restore_backup_job
        queue.enqueue(restore_backup_job, job.id, job_timeout=1300)

        logger.info(f"Restore job {job.id} queued for customer {customer.id}")
        return jsonify({'success': True, 'message': 'Restore started', 'job_id': job.id})

    except Exception as e:
        logger.error(f"Failed to queue restore: {e}")
        return jsonify({'success': False, 'message': 'Failed to start restore'}), 500


@app.route('/api/backups/status')
@login_required
def backup_status():
    """Get current backup job status"""
    customer = Customer.get_by_id(current_user.id)
    active_job = CustomerBackupJob.get_active_job(customer.id)

    if active_job:
        return jsonify({
            'has_active_job': True,
            'job': active_job.to_dict()
        })
    else:
        return jsonify({'has_active_job': False})


def get_customer_manual_backups(customer_id, limit=5):
    """Get manual backups for a customer from restic"""
    try:
        result = subprocess.run(
            ['sudo', 'bash', '-c',
             f'export RESTIC_REPOSITORY="sftp:sh-backup@15.204.249.219:/home/sh-backup/manual-backups" && '
             f'export RESTIC_PASSWORD_FILE="/opt/shophosting/.manual-restic-password" && '
             f'export HOME=/root && '
             f'restic snapshots --json --tag "customer-{customer_id}" --tag "manual"'],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode == 0 and result.stdout.strip():
            import json
            snapshots = json.loads(result.stdout)
            # Sort by time descending and limit
            snapshots.sort(key=lambda x: x.get('time', ''), reverse=True)
            return snapshots[:limit]
    except Exception as e:
        logger.error(f"Error fetching manual backups: {e}")

    return []


def get_customer_daily_backups(customer_id, limit=10):
    """Get daily backups that contain this customer's data"""
    try:
        result = subprocess.run(
            ['sudo', 'bash', '-c',
             f'export RESTIC_REPOSITORY="sftp:sh-backup@15.204.249.219:/home/sh-backup/backups" && '
             f'export RESTIC_PASSWORD_FILE="/root/.restic-password" && '
             f'export HOME=/root && '
             f'restic snapshots --json --tag "daily"'],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode == 0 and result.stdout.strip():
            import json
            snapshots = json.loads(result.stdout)
            # Filter to snapshots that have customer path, sort descending
            customer_path = f"/var/customers/customer-{customer_id}"
            filtered = [s for s in snapshots if any(customer_path in p for p in s.get('paths', []))]
            filtered.sort(key=lambda x: x.get('time', ''), reverse=True)
            return filtered[:limit]
    except Exception as e:
        logger.error(f"Error fetching daily backups: {e}")

    return []
```

**Step 3: Add subprocess import if not present**

Check if `import subprocess` exists at top of file, add if missing.

**Step 4: Verify syntax**

Run: `python3 -m py_compile /opt/shophosting/webapp/app.py`
Expected: No output (success)

**Step 5: Commit**

```bash
git add webapp/app.py
git commit -m "feat(routes): add customer backup routes"
```

---

## Task 8: Backups Template

**Files:**
- Create: `/opt/shophosting/webapp/templates/backups.html`

**Step 1: Create the template**

```html
{% extends "base.html" %}

{% block title %}Backups - ShopHosting.io{% endblock %}

{% block extra_css %}
<style>
    .backups-page {
        max-width: 900px;
        margin: 0 auto;
    }

    .section-card {
        background: var(--bg-elevated);
        border: 1px solid var(--border-subtle);
        border-radius: var(--radius-lg);
        margin-bottom: 24px;
        overflow: hidden;
    }

    .section-header {
        padding: 20px 28px;
        border-bottom: 1px solid var(--border-subtle);
        display: flex;
        align-items: center;
        justify-content: space-between;
    }

    .section-title {
        font-size: 1.1rem;
        font-weight: 600;
        color: var(--text-primary);
    }

    .section-body {
        padding: 20px 28px;
    }

    .backup-options {
        display: flex;
        gap: 20px;
        margin-bottom: 20px;
    }

    .backup-option {
        display: flex;
        align-items: center;
        gap: 8px;
        cursor: pointer;
    }

    .backup-option input[type="radio"] {
        accent-color: var(--accent);
    }

    .backup-list {
        display: flex;
        flex-direction: column;
        gap: 12px;
    }

    .backup-item {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 16px;
        background: var(--bg-surface);
        border-radius: var(--radius-md);
        border: 1px solid var(--border-subtle);
    }

    .backup-info {
        display: flex;
        flex-direction: column;
        gap: 4px;
    }

    .backup-date {
        font-weight: 500;
        color: var(--text-primary);
    }

    .backup-type {
        font-size: 0.85rem;
        color: var(--text-secondary);
    }

    .backup-actions {
        position: relative;
    }

    .restore-dropdown {
        position: absolute;
        right: 0;
        top: 100%;
        background: var(--bg-elevated);
        border: 1px solid var(--border-subtle);
        border-radius: var(--radius-md);
        box-shadow: var(--shadow-lg);
        min-width: 180px;
        z-index: 100;
        display: none;
    }

    .restore-dropdown.show {
        display: block;
    }

    .restore-option {
        padding: 12px 16px;
        cursor: pointer;
        transition: background 0.2s;
    }

    .restore-option:hover {
        background: var(--bg-surface);
    }

    .btn {
        padding: 10px 20px;
        border-radius: var(--radius-md);
        font-weight: 500;
        cursor: pointer;
        transition: all 0.2s;
        border: none;
    }

    .btn-primary {
        background: var(--accent);
        color: white;
    }

    .btn-primary:hover {
        background: var(--accent-hover);
    }

    .btn-secondary {
        background: var(--bg-surface);
        color: var(--text-primary);
        border: 1px solid var(--border-subtle);
    }

    .btn-secondary:hover {
        background: var(--bg-elevated);
    }

    .btn:disabled {
        opacity: 0.5;
        cursor: not-allowed;
    }

    .status-banner {
        padding: 16px 20px;
        border-radius: var(--radius-md);
        margin-bottom: 24px;
        display: flex;
        align-items: center;
        gap: 12px;
    }

    .status-banner.running {
        background: rgba(59, 130, 246, 0.1);
        border: 1px solid rgba(59, 130, 246, 0.3);
        color: var(--text-primary);
    }

    .spinner {
        width: 20px;
        height: 20px;
        border: 2px solid var(--border-subtle);
        border-top-color: var(--accent);
        border-radius: 50%;
        animation: spin 1s linear infinite;
    }

    @keyframes spin {
        to { transform: rotate(360deg); }
    }

    .empty-state {
        text-align: center;
        padding: 40px 20px;
        color: var(--text-secondary);
    }

    /* Modal styles */
    .modal-overlay {
        position: fixed;
        inset: 0;
        background: rgba(0, 0, 0, 0.5);
        display: none;
        align-items: center;
        justify-content: center;
        z-index: 1000;
    }

    .modal-overlay.show {
        display: flex;
    }

    .modal {
        background: var(--bg-elevated);
        border-radius: var(--radius-lg);
        padding: 24px;
        max-width: 450px;
        width: 90%;
    }

    .modal-title {
        font-size: 1.2rem;
        font-weight: 600;
        margin-bottom: 16px;
    }

    .modal-body {
        margin-bottom: 20px;
    }

    .modal-input {
        width: 100%;
        padding: 12px;
        border: 1px solid var(--border-subtle);
        border-radius: var(--radius-md);
        background: var(--bg-surface);
        color: var(--text-primary);
        margin-top: 12px;
    }

    .modal-actions {
        display: flex;
        gap: 12px;
        justify-content: flex-end;
    }

    .warning-text {
        color: #ef4444;
        font-size: 0.9rem;
    }
</style>
{% endblock %}

{% block content %}
<div class="backups-page">
    <h1 style="margin-bottom: 24px;">Backups</h1>

    {% if active_job %}
    <div class="status-banner running" id="status-banner">
        <div class="spinner"></div>
        <span id="status-text">
            {% if active_job.job_type == 'backup' %}
                Creating backup...
            {% else %}
                Restoring from backup...
            {% endif %}
        </span>
    </div>
    {% endif %}

    <!-- Create Backup Section -->
    <div class="section-card">
        <div class="section-header">
            <span class="section-title">Create Manual Backup</span>
        </div>
        <div class="section-body">
            <form id="backup-form">
                <div class="backup-options">
                    <label class="backup-option">
                        <input type="radio" name="backup_type" value="db">
                        <span>Database only</span>
                    </label>
                    <label class="backup-option">
                        <input type="radio" name="backup_type" value="files">
                        <span>Files only</span>
                    </label>
                    <label class="backup-option">
                        <input type="radio" name="backup_type" value="both" checked>
                        <span>Database & Files</span>
                    </label>
                </div>
                <button type="submit" class="btn btn-primary" id="create-backup-btn" {% if active_job %}disabled{% endif %}>
                    Create Backup
                </button>
            </form>
        </div>
    </div>

    <!-- Manual Backups Section -->
    <div class="section-card">
        <div class="section-header">
            <span class="section-title">Manual Backups</span>
            <span style="color: var(--text-secondary); font-size: 0.9rem;">Max 5 kept</span>
        </div>
        <div class="section-body">
            {% if manual_backups %}
            <div class="backup-list">
                {% for backup in manual_backups %}
                <div class="backup-item">
                    <div class="backup-info">
                        <span class="backup-date">{{ backup.time[:19] | replace('T', ' ') }}</span>
                        <span class="backup-type">
                            {% if 'both' in backup.tags %}Database & Files
                            {% elif 'db' in backup.tags %}Database only
                            {% elif 'files' in backup.tags %}Files only
                            {% else %}Full backup{% endif %}
                        </span>
                    </div>
                    <div class="backup-actions">
                        <button class="btn btn-secondary restore-toggle" data-snapshot="{{ backup.id }}" data-source="manual" {% if active_job %}disabled{% endif %}>
                            Restore
                        </button>
                        <div class="restore-dropdown" id="dropdown-{{ backup.id }}">
                            <div class="restore-option" data-type="db">Database only</div>
                            <div class="restore-option" data-type="files">Files only</div>
                            <div class="restore-option" data-type="both">Full restore</div>
                        </div>
                    </div>
                </div>
                {% endfor %}
            </div>
            {% else %}
            <div class="empty-state">
                <p>No manual backups yet. Create one above.</p>
            </div>
            {% endif %}
        </div>
    </div>

    <!-- Daily Backups Section -->
    <div class="section-card">
        <div class="section-header">
            <span class="section-title">Daily Backups</span>
            <span style="color: var(--text-secondary); font-size: 0.9rem;">Last 30 days</span>
        </div>
        <div class="section-body">
            {% if daily_backups %}
            <div class="backup-list">
                {% for backup in daily_backups %}
                <div class="backup-item">
                    <div class="backup-info">
                        <span class="backup-date">{{ backup.time[:19] | replace('T', ' ') }}</span>
                        <span class="backup-type">Full backup (automatic)</span>
                    </div>
                    <div class="backup-actions">
                        <button class="btn btn-secondary restore-toggle" data-snapshot="{{ backup.id }}" data-source="daily" {% if active_job %}disabled{% endif %}>
                            Restore
                        </button>
                        <div class="restore-dropdown" id="dropdown-{{ backup.id }}">
                            <div class="restore-option" data-type="db">Database only</div>
                            <div class="restore-option" data-type="files">Files only</div>
                            <div class="restore-option" data-type="both">Full restore</div>
                        </div>
                    </div>
                </div>
                {% endfor %}
            </div>
            {% else %}
            <div class="empty-state">
                <p>No daily backups available yet.</p>
            </div>
            {% endif %}
        </div>
    </div>
</div>

<!-- Restore Confirmation Modal -->
<div class="modal-overlay" id="restore-modal">
    <div class="modal">
        <div class="modal-title">Confirm Restore</div>
        <div class="modal-body">
            <p>This will restore your site from the selected backup. Your site will be put into maintenance mode during the restore.</p>
            <p class="warning-text" style="margin-top: 12px;">This action cannot be undone. Any changes made after this backup will be lost.</p>
            <input type="text" class="modal-input" id="restore-confirmation" placeholder="Type RESTORE to confirm">
            <input type="hidden" id="restore-snapshot-id">
            <input type="hidden" id="restore-type">
        </div>
        <div class="modal-actions">
            <button class="btn btn-secondary" id="cancel-restore">Cancel</button>
            <button class="btn btn-primary" id="confirm-restore">Restore</button>
        </div>
    </div>
</div>
{% endblock %}

{% block extra_js %}
<script>
const csrfToken = '{{ csrf_token() }}';

// Create backup form
document.getElementById('backup-form').addEventListener('submit', async (e) => {
    e.preventDefault();

    const backupType = document.querySelector('input[name="backup_type"]:checked').value;
    const btn = document.getElementById('create-backup-btn');

    btn.disabled = true;
    btn.textContent = 'Starting...';

    try {
        const formData = new FormData();
        formData.append('backup_type', backupType);

        const response = await fetch('/backups/create', {
            method: 'POST',
            headers: { 'X-CSRFToken': csrfToken },
            body: formData
        });

        const data = await response.json();

        if (data.success) {
            location.reload();
        } else {
            alert(data.message || 'Failed to start backup');
            btn.disabled = false;
            btn.textContent = 'Create Backup';
        }
    } catch (err) {
        alert('An error occurred');
        btn.disabled = false;
        btn.textContent = 'Create Backup';
    }
});

// Restore dropdown toggles
document.querySelectorAll('.restore-toggle').forEach(btn => {
    btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const snapshotId = btn.dataset.snapshot;
        const dropdown = document.getElementById('dropdown-' + snapshotId);

        // Close all other dropdowns
        document.querySelectorAll('.restore-dropdown').forEach(d => {
            if (d !== dropdown) d.classList.remove('show');
        });

        dropdown.classList.toggle('show');
    });
});

// Close dropdowns when clicking outside
document.addEventListener('click', () => {
    document.querySelectorAll('.restore-dropdown').forEach(d => d.classList.remove('show'));
});

// Restore option clicks
document.querySelectorAll('.restore-option').forEach(opt => {
    opt.addEventListener('click', (e) => {
        e.stopPropagation();
        const dropdown = opt.closest('.restore-dropdown');
        const snapshotId = dropdown.id.replace('dropdown-', '');
        const restoreType = opt.dataset.type;

        document.getElementById('restore-snapshot-id').value = snapshotId;
        document.getElementById('restore-type').value = restoreType;
        document.getElementById('restore-confirmation').value = '';
        document.getElementById('restore-modal').classList.add('show');

        dropdown.classList.remove('show');
    });
});

// Modal cancel
document.getElementById('cancel-restore').addEventListener('click', () => {
    document.getElementById('restore-modal').classList.remove('show');
});

// Modal confirm
document.getElementById('confirm-restore').addEventListener('click', async () => {
    const confirmation = document.getElementById('restore-confirmation').value;
    const snapshotId = document.getElementById('restore-snapshot-id').value;
    const restoreType = document.getElementById('restore-type').value;

    if (confirmation !== 'RESTORE') {
        alert('Please type RESTORE to confirm');
        return;
    }

    const btn = document.getElementById('confirm-restore');
    btn.disabled = true;
    btn.textContent = 'Starting...';

    try {
        const formData = new FormData();
        formData.append('confirmation', confirmation);
        formData.append('restore_type', restoreType);

        const response = await fetch(`/backups/${snapshotId}/restore`, {
            method: 'POST',
            headers: { 'X-CSRFToken': csrfToken },
            body: formData
        });

        const data = await response.json();

        if (data.success) {
            location.reload();
        } else {
            alert(data.message || 'Failed to start restore');
            btn.disabled = false;
            btn.textContent = 'Restore';
        }
    } catch (err) {
        alert('An error occurred');
        btn.disabled = false;
        btn.textContent = 'Restore';
    }
});

// Poll for status updates if there's an active job
{% if active_job %}
setInterval(async () => {
    try {
        const response = await fetch('/api/backups/status');
        const data = await response.json();

        if (!data.has_active_job) {
            location.reload();
        }
    } catch (err) {
        console.error('Status poll failed:', err);
    }
}, 5000);
{% endif %}
</script>
{% endblock %}
```

**Step 2: Commit**

```bash
git add webapp/templates/backups.html
git commit -m "feat(templates): add backups.html template"
```

---

## Task 9: Add Backups Link to Navigation

**Files:**
- Modify: `/opt/shophosting/webapp/templates/base.html`

**Step 1: Find the navigation section and add Backups link**

Look for links like Dashboard, Billing, Support in the nav. Add after them:

```html
<a href="{{ url_for('backups') }}" class="nav-link {% if request.endpoint == 'backups' %}active{% endif %}">Backups</a>
```

**Step 2: Commit**

```bash
git add webapp/templates/base.html
git commit -m "feat(nav): add Backups link to navigation"
```

---

## Task 10: Sudoers Configuration

**Files:**
- Create: `/opt/shophosting/sudoers.d/shophosting-backups`

**Step 1: Create sudoers file**

```
# Sudoers configuration for ShopHosting backup operations
# Install to /etc/sudoers.d/shophosting-backups

www-data ALL=(root) NOPASSWD: /opt/shophosting/scripts/customer-backup.sh
www-data ALL=(root) NOPASSWD: /opt/shophosting/scripts/customer-restore.sh
www-data ALL=(root) NOPASSWD: /bin/bash -c export RESTIC_REPOSITORY=* && export RESTIC_PASSWORD_FILE=* && export HOME=/root && restic snapshots *
```

**Step 2: Commit**

```bash
git add sudoers.d/shophosting-backups
git commit -m "feat(security): add sudoers config for backup scripts"
```

---

## Task 11: Nginx Maintenance Mode Configuration

**Files:**
- Create: `/opt/shophosting/templates/maintenance.html`
- Document: Update nginx config pattern

**Step 1: Create maintenance page**

```html
<!DOCTYPE html>
<html>
<head>
    <title>Maintenance in Progress</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            margin: 0;
            background: #0f0f0f;
            color: #fff;
        }
        .container {
            text-align: center;
            padding: 40px;
        }
        h1 { font-size: 2rem; margin-bottom: 16px; }
        p { color: #888; }
        .spinner {
            width: 40px;
            height: 40px;
            border: 3px solid #333;
            border-top-color: #3b82f6;
            border-radius: 50%;
            animation: spin 1s linear infinite;
            margin: 20px auto;
        }
        @keyframes spin { to { transform: rotate(360deg); } }
    </style>
</head>
<body>
    <div class="container">
        <div class="spinner"></div>
        <h1>Maintenance in Progress</h1>
        <p>We're restoring your site from a backup. This usually takes a few minutes.</p>
        <p>Please check back shortly.</p>
    </div>
</body>
</html>
```

**Step 2: Commit**

```bash
git add templates/maintenance.html
git commit -m "feat(templates): add maintenance mode page"
```

---

## Task 12: Final Integration Commit

**Step 1: Create final commit with all changes**

```bash
git add -A
git commit -m "feat: complete customer backups feature implementation

- Database migration for customer_backup_jobs table
- CustomerBackupJob model with CRUD operations
- customer-backup.sh and customer-restore.sh scripts
- Backup worker with RQ job processing
- Systemd service for backup worker
- Flask routes for backup/restore operations
- Backups template with create/restore UI
- Navigation link added
- Sudoers and maintenance mode configuration

Implements self-service backup and restore for customers."
```

---

## Post-Implementation Steps (Manual)

These require server access with sudo:

1. **Run database migration:**
   ```bash
   mysql -u shophosting_app -p shophosting_db < /opt/shophosting/migrations/006_customer_backup_jobs.sql
   ```

2. **Initialize restic repository on backup server:**
   ```bash
   # Create password file
   openssl rand -base64 32 | sudo tee /opt/shophosting/.manual-restic-password
   sudo chmod 600 /opt/shophosting/.manual-restic-password

   # SSH to backup server and create directory
   ssh sh-backup@15.204.249.219 "mkdir -p /home/sh-backup/manual-backups"

   # Initialize repository
   export RESTIC_REPOSITORY="sftp:sh-backup@15.204.249.219:/home/sh-backup/manual-backups"
   export RESTIC_PASSWORD_FILE="/opt/shophosting/.manual-restic-password"
   sudo -E restic init
   ```

3. **Install sudoers configuration:**
   ```bash
   sudo cp /opt/shophosting/sudoers.d/shophosting-backups /etc/sudoers.d/
   sudo chmod 440 /etc/sudoers.d/shophosting-backups
   sudo visudo -c  # Verify syntax
   ```

4. **Install and start backup worker service:**
   ```bash
   sudo cp /opt/shophosting/shophosting-backup-worker.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable shophosting-backup-worker
   sudo systemctl start shophosting-backup-worker
   ```

5. **Restart webapp:**
   ```bash
   sudo systemctl restart shophosting-webapp
   ```
