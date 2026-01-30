# Disaster Recovery Runbook

## Overview

This document outlines procedures for recovering ShopHosting.io from various disaster scenarios.

**Recovery Objectives:**
- **RPO (Recovery Point Objective):** 24 hours (daily backups)
- **RTO (Recovery Time Objective):** 4 hours (full system restore)

## Backup Infrastructure

### Backup Schedule
| Backup Type | Schedule | Retention | Location |
|-------------|----------|-----------|----------|
| Database (MySQL) | Daily 02:00 | 30 days | SFTP: 15.204.249.219 |
| Customer Files | Daily 02:00 | 30 days | SFTP: 15.204.249.219 |
| Application Code | Daily 02:30 | 30 days | SFTP: 15.204.249.219 |
| System Config | Daily 03:00 | 7 days | SFTP: 15.204.249.219 |

### Backup Verification
- Weekly integrity checks run every Sunday via `restic check`
- Monthly DR tests using `/opt/shophosting/scripts/dr-test.sh`

## Disaster Scenarios

### Scenario 1: Single Customer Data Loss

**Symptoms:** Customer reports missing files or corrupted database

**Recovery Steps:**
```bash
# 1. List available snapshots
/opt/shophosting/scripts/restore.sh list

# 2. Find snapshot containing customer data
/opt/shophosting/scripts/restore.sh show <snapshot-id>

# 3. Restore specific customer
/opt/shophosting/scripts/restore.sh restore-customer <customer-id> <snapshot-id>

# 4. Verify restoration
docker compose -f /var/customers/customer-<id>/docker-compose.yml ps
curl -I https://<customer-domain>
```

**Expected Duration:** 15-30 minutes

---

### Scenario 2: Database Corruption

**Symptoms:** Application errors, MySQL crashes, data inconsistencies

**Recovery Steps:**
```bash
# 1. Stop the application
sudo systemctl stop shophosting-webapp
sudo systemctl stop provisioning-worker
sudo systemctl stop resource-worker

# 2. List available database backups
export RESTIC_REPOSITORY="sftp:sh-backup@15.204.249.219:/home/sh-backup/backups"
export RESTIC_PASSWORD_FILE="/root/.restic-password"
restic snapshots --tag database

# 3. Restore database dump to temp location
restic restore latest --target /tmp/db-restore --include "/tmp/shophosting-db-dumps"

# 4. Import the database
mysql -u root -p shophosting_db < /tmp/db-restore/tmp/shophosting-db-dumps/shophosting_db.sql

# 5. Restore customer databases
for f in /tmp/db-restore/tmp/shophosting-db-dumps/customer_*.sql; do
    dbname=$(basename "$f" .sql)
    mysql -u root -p "$dbname" < "$f"
done

# 6. Restart services
sudo systemctl start shophosting-webapp
sudo systemctl start provisioning-worker
sudo systemctl start resource-worker

# 7. Verify
curl -s https://shophosting.io/health | jq .
```

**Expected Duration:** 30-60 minutes

---

### Scenario 3: Complete Server Failure

**Symptoms:** Server unreachable, hardware failure, data center outage

**Prerequisites:**
- New server provisioned with Ubuntu 22.04
- Network configured with same IP or DNS updated
- SSH access established

**Recovery Steps:**

```bash
# On the NEW server:

# 1. Install dependencies
apt update && apt install -y mysql-server redis-server nginx docker.io docker-compose restic

# 2. Create application user
useradd -m -s /bin/bash agileweb
usermod -aG docker agileweb

# 3. Configure restic
export RESTIC_REPOSITORY="sftp:sh-backup@15.204.249.219:/home/sh-backup/backups"
echo "YOUR_RESTIC_PASSWORD" > /root/.restic-password
chmod 600 /root/.restic-password

# 4. Restore application code
restic restore latest --target / --include "/opt/shophosting"

# 5. Restore customer data
restic restore latest --target / --include "/var/customers"

# 6. Restore nginx configs
restic restore latest --target / --include "/etc/nginx/sites-available"

# 7. Restore SSL certificates
restic restore latest --target / --include "/etc/letsencrypt"

# 8. Restore database
restic restore latest --target /tmp/db-restore --include "/tmp/shophosting-db-dumps"
mysql -u root < /tmp/db-restore/tmp/shophosting-db-dumps/shophosting_db.sql

# 9. Configure MySQL user
mysql -u root -e "CREATE USER IF NOT EXISTS 'shophosting_app'@'localhost' IDENTIFIED BY 'PASSWORD';"
mysql -u root -e "GRANT ALL PRIVILEGES ON shophosting_db.* TO 'shophosting_app'@'localhost';"

# 10. Set up Python environment
cd /opt/shophosting/webapp
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 11. Install systemd services
cp /opt/shophosting/*.service /etc/systemd/system/
cp /opt/shophosting/*.timer /etc/systemd/system/
systemctl daemon-reload

# 12. Enable nginx sites
for site in /etc/nginx/sites-available/*; do
    ln -sf "$site" /etc/nginx/sites-enabled/
done
nginx -t && systemctl restart nginx

# 13. Start all services
systemctl enable --now shophosting-webapp
systemctl enable --now provisioning-worker
systemctl enable --now resource-worker
systemctl enable --now monitoring-worker

# 14. Start customer containers
for dir in /var/customers/customer-*/; do
    cd "$dir" && docker compose up -d
done

# 15. Verify
curl -s https://shophosting.io/health | jq .
```

**Expected Duration:** 2-4 hours

---

### Scenario 4: Ransomware / Security Breach

**Symptoms:** Files encrypted, unauthorized access detected, data exfiltration

**Immediate Actions:**
1. **Isolate the server** - Disconnect from network if possible
2. **Preserve evidence** - Take disk snapshots before any recovery
3. **Notify stakeholders** - Alert team and affected customers
4. **Contact legal/compliance** if customer data was compromised

**Recovery Steps:**
```bash
# 1. Provision a NEW server (do not trust compromised server)

# 2. Restore from CLEAN backup (verify backup date is before breach)
# Use oldest known-good snapshot
restic snapshots
restic restore <pre-breach-snapshot-id> --target /

# 3. Change ALL credentials:
# - Database passwords
# - API keys (Stripe, Cloudflare)
# - Admin passwords
# - Customer passwords (force reset)

# 4. Rotate secrets
# - Generate new SECRET_KEY
# - Regenerate Stripe webhook secret
# - Invalidate all API tokens

# 5. Review and harden security
# - Enable fail2ban
# - Review firewall rules
# - Enable 2FA for all admins
# - Audit admin access logs
```

**Expected Duration:** 4-8 hours (plus investigation time)

---

## Verification Procedures

### Post-Recovery Checklist

- [ ] Application loads: `curl -I https://shophosting.io`
- [ ] Health check passes: `curl https://shophosting.io/health`
- [ ] Database connection works: Check health endpoint
- [ ] Redis connection works: Check health endpoint
- [ ] Admin panel accessible: `https://shophosting.io/admin`
- [ ] Customer containers running: `docker ps | grep customer`
- [ ] Customer sites accessible: Test sample customer domains
- [ ] Monitoring working: Check Prometheus/Grafana
- [ ] Backups running: Check systemd timers

### Automated Verification
```bash
# Run the DR test script
/opt/shophosting/scripts/dr-test.sh
```

---

## Contacts

| Role | Contact | Phone |
|------|---------|-------|
| On-Call Engineer | ops@shophosting.io | - |
| Backup Server | 15.204.249.219 | - |
| Hosting Provider | - | - |

---

## Document History

| Date | Author | Changes |
|------|--------|---------|
| 2026-01-30 | System | Initial version |
