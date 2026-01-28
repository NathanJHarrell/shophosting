# Customer Backups Feature Design

**Date:** 2026-01-27
**Status:** Approved

## Overview

Enable customers to create manual backups and restore from either manual or daily backups via their dashboard.

## Key Decisions

| Decision | Choice |
|----------|--------|
| Repository location | `/home/sh-backup/manual-backups` on existing backup server |
| Backup organization | Single repository with tags per customer |
| Retention policy | 5 most recent manual backups per customer |
| Daily backup access | Customers can restore their own data from daily backups |
| Restore process | Confirmation required, then queue-based |
| Site state during restore | Maintenance mode enabled |

## Data Flow

### Backup Flow

```
Customer clicks "Create Backup" (db/files/both)
    → Confirmation dialog
    → Job queued to 'backups' Redis queue
    → Worker runs customer-backup.sh
    → restic backup to sftp:sh-backup@15.204.249.219:/home/sh-backup/manual-backups
    → Tagged: customer-{id}, manual, {type: db|files|both}, {timestamp}
    → If >5 manual backups exist for customer, prune oldest
    → Customer notified via status polling
```

### Restore Flow

```
Customer selects backup (manual or daily) → clicks "Restore"
    → Confirmation dialog (type "RESTORE")
    → Site put into maintenance mode
    → Job queued to 'backups' Redis queue
    → Worker restores files/database to temp location
    → Swap into place, restart containers
    → Exit maintenance mode
    → Customer notified via status polling
```

## New Files

### Scripts

```
/opt/shophosting/scripts/customer-backup.sh
    - Creates backup for single customer
    - Args: customer_id, backup_type (db|files|both)
    - Tags with customer-{id}, manual, type, timestamp
    - Enforces 5-backup limit per customer

/opt/shophosting/scripts/customer-restore.sh
    - Restores customer from any snapshot
    - Args: customer_id, snapshot_id, restore_type (db|files|both)
    - Handles maintenance mode, container stop/start
```

### Webapp Routes (in app.py)

```
GET  /backups                        - List customer's backups (manual + daily)
POST /backups/create                 - Queue manual backup creation
GET  /backups/<snapshot_id>          - View backup details
POST /backups/<snapshot_id>/restore  - Queue restore (with confirmation)
GET  /api/backups/status             - Poll for job status
```

### Templates

```
/opt/shophosting/webapp/templates/backups.html
    - Lists available backups (manual and daily)
    - Create backup form (radio: db/files/both)
    - Restore buttons with confirmation modal
    - Status indicator for in-progress operations
```

### Worker

```
/opt/shophosting/backup_worker.py
    - Runs backup/restore jobs from Redis queue
    - Similar pattern to staging_worker.py
```

### Systemd

```
/opt/shophosting/shophosting-backup-worker.service
    - Runs backup_worker.py continuously
    - Restarts on failure
```

## Database Schema

### New Table: customer_backup_jobs

```sql
CREATE TABLE customer_backup_jobs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    customer_id INT NOT NULL,
    job_type ENUM('backup', 'restore') NOT NULL,
    backup_type ENUM('db', 'files', 'both') NOT NULL,
    snapshot_id VARCHAR(64) NULL,
    status ENUM('pending', 'running', 'completed', 'failed') DEFAULT 'pending',
    error_message TEXT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(id)
);
```

### New Model: CustomerBackupJob

- Standard CRUD methods
- `get_active_job(customer_id)` - Check if backup/restore already running
- `get_recent_jobs(customer_id, limit=10)` - For showing history

## UI Design

```
┌─────────────────────────────────────────────────────────────┐
│  Backups                                                    │
├─────────────────────────────────────────────────────────────┤
│  Create Manual Backup                                       │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ ○ Database only   ○ Files only   ● Database & Files │   │
│  │                                    [Create Backup]   │   │
│  └─────────────────────────────────────────────────────┘   │
├─────────────────────────────────────────────────────────────┤
│  Your Backups                                               │
│                                                             │
│  ┌─ Manual Backups ────────────────────────────────────┐   │
│  │ Jan 27, 2026 2:30 PM  │ DB & Files │ [Restore ▼]    │   │
│  │ Jan 25, 2026 9:15 AM  │ Database   │ [Restore ▼]    │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─ Daily Backups (Last 30 days) ──────────────────────┐   │
│  │ Jan 27, 2026 2:00 AM  │ Full       │ [Restore ▼]    │   │
│  │ Jan 26, 2026 2:00 AM  │ Full       │ [Restore ▼]    │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

- Restore dropdown: Database only, Files only, Full restore
- Confirmation modal: "Type RESTORE to confirm"
- Progress via polling `/api/backups/status`

## Security

### Access Control

- All `/backups` routes require `@login_required`
- Jobs filtered by `customer_id = current_user.id`
- Snapshot validation via restic tags before restore
- Scripts run via sudo with specific sudoers entries

### Sudoers Configuration

File: `/etc/sudoers.d/shophosting-backups`

```
www-data ALL=(root) NOPASSWD: /opt/shophosting/scripts/customer-backup.sh
www-data ALL=(root) NOPASSWD: /opt/shophosting/scripts/customer-restore.sh
```

### Rate Limiting

- One active backup/restore job per customer at a time
- Check `get_active_job()` before queuing

### Maintenance Mode

- Creates `/var/customers/customer-{id}/.maintenance` file
- Nginx returns 503 with maintenance page when file exists
- Removed after restore completes (success or failure)

## Error Handling

- Script failures captured in `error_message` column
- Maintenance mode always disabled in `finally` block
- Failed jobs shown to customer with error message
- Logs: `/opt/shophosting/logs/backup_worker.log`

## Repository Setup (One-time)

```bash
# Create password file
openssl rand -base64 32 > /opt/shophosting/.manual-restic-password
chmod 600 /opt/shophosting/.manual-restic-password

# Initialize repository
export RESTIC_REPOSITORY="sftp:sh-backup@15.204.249.219:/home/sh-backup/manual-backups"
export RESTIC_PASSWORD_FILE="/opt/shophosting/.manual-restic-password"
restic init
```

## Implementation Order

1. Repository setup (one-time manual step)
2. Database migration (create table)
3. Model class (CustomerBackupJob)
4. Scripts (customer-backup.sh, customer-restore.sh)
5. Worker (backup_worker.py + systemd service)
6. Routes and template
7. Sudoers configuration
8. Nginx maintenance mode config
9. Testing
