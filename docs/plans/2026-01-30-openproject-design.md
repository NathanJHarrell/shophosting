# OpenProject Docker Installation Design

**Date:** 2026-01-30
**Purpose:** Internal project management tool for shophosting.io team

## Overview

Deploy OpenProject as a standalone Docker service for internal team use, with automated backups integrated into the existing Restic infrastructure.

## Configuration Summary

| Setting | Value |
|---------|-------|
| Access URL | `http://shophosting.io:6789` |
| Location | `/opt/shophosting/openproject/` |
| Data Storage | `/opt/shophosting/openproject/data/` |
| Resources | 4GB RAM, 2 CPUs |
| Email | Local Postfix (172.17.0.1:25) |
| Backups | pg_dump + Restic integration |

## Directory Structure

```
/opt/shophosting/openproject/
├── docker-compose.yml      # Main container configuration
├── .env                    # Secrets (SECRET_KEY_BASE)
├── scripts/
│   └── backup-db.sh        # Pre-backup database dump
└── data/
    ├── pgdata/             # PostgreSQL database files
    ├── assets/             # Uploaded files, attachments
    └── backups/            # SQL dumps for Restic
```

## Files to Create

1. `/opt/shophosting/openproject/docker-compose.yml` - Container configuration
2. `/opt/shophosting/openproject/.env` - Environment secrets
3. `/opt/shophosting/openproject/scripts/backup-db.sh` - Database backup script
4. `/etc/systemd/system/openproject.service` - Systemd unit file

## Files to Modify

1. `provisioning/backup_worker.py` - Add OpenProject to backup paths + pre-backup hook

## Docker Compose Configuration

- Image: `openproject/openproject:15`
- Port: `6789:80`
- Network: `openproject-network` (bridge)
- Restart: `unless-stopped`
- Memory limit: 4GB
- CPU limit: 2

## Environment Variables

- `SECRET_KEY_BASE` - Random 64-char hex string
- `OPENPROJECT_HOST__NAME=shophosting.io:6789`
- `OPENPROJECT_HTTPS=false`
- `OPENPROJECT_DEFAULT__LANGUAGE=en`
- SMTP via local Postfix (172.17.0.1:25)

## Backup Strategy

1. Pre-backup script runs `pg_dump` via `docker exec`
2. Dumps saved to `data/backups/openproject.sql`
3. Restic backs up entire `data/` directory
4. Restic handles versioning and retention

## Post-Installation

1. Access http://shophosting.io:6789
2. Login with admin/admin
3. Change admin password immediately
4. Configure first project
