#!/bin/bash
# Shared restic backup configuration

RESTIC_REPOSITORY="sftp:sh-backup@15.204.249.219:/home/sh-backup/backups"
RESTIC_PASSWORD_FILE="/root/.restic-password"
RETENTION_DAYS=30
