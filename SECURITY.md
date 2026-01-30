# Security Policy

This document describes the security architecture, controls, and best practices for ShopHosting.io. It covers authentication, authorization, data protection, infrastructure hardening, and incident response procedures.

## Table of Contents

- [Security Architecture](#security-architecture)
- [Authentication & Authorization](#authentication--authorization)
- [Secrets Management](#secrets-management)
- [Data Protection](#data-protection)
- [Network Security](#network-security)
- [Container Security](#container-security)
- [Infrastructure Hardening](#infrastructure-hardening)
- [Security Monitoring](#security-monitoring)
- [Vulnerability Management](#vulnerability-management)
- [Incident Response](#incident-response)
- [Compliance](#compliance)
- [Reporting Security Issues](#reporting-security-issues)

## Security Architecture

### Defense in Depth

ShopHosting.io implements multiple layers of security:

```
┌─────────────────────────────────────────────────────────────┐
│                    Layer 1: Edge Security                    │
│         Cloudflare WAF, DDoS Protection, Rate Limiting       │
├─────────────────────────────────────────────────────────────┤
│                   Layer 2: Network Security                  │
│     Firewall (UFW), fail2ban, TLS/SSL, IP Restrictions       │
├─────────────────────────────────────────────────────────────┤
│                 Layer 3: Application Security                │
│  CSRF, XSS Protection, Rate Limiting, Security Headers       │
├─────────────────────────────────────────────────────────────┤
│                 Layer 4: Authentication                      │
│     Password Hashing, 2FA/MFA, Session Management            │
├─────────────────────────────────────────────────────────────┤
│                  Layer 5: Authorization                      │
│      Role-Based Access Control, Tenant Isolation             │
├─────────────────────────────────────────────────────────────┤
│                   Layer 6: Data Security                     │
│    Encryption at Rest, Secrets Management (Vault)            │
├─────────────────────────────────────────────────────────────┤
│                Layer 7: Container Isolation                  │
│   Docker Networks, Resource Limits, Non-Root Execution       │
└─────────────────────────────────────────────────────────────┘
```

### Trust Boundaries

| Boundary | Description | Controls |
|----------|-------------|----------|
| Internet → Nginx | Public edge | TLS 1.2+, WAF, rate limiting |
| Nginx → Flask | Reverse proxy | Unix socket or localhost only |
| Flask → MySQL | Database access | Parameterized queries, least privilege |
| Flask → Redis | Cache/queue | Password auth, localhost binding |
| Host → Containers | Customer isolation | Docker networks, resource limits |
| Admin → System | Administrative access | 2FA, role-based access, audit logging |

## Authentication & Authorization

### Customer Authentication

#### Password Security

- **Hashing**: Passwords hashed using Werkzeug's `generate_password_hash()` with PBKDF2-SHA256
- **Salt**: Unique salt per password (automatic with Werkzeug)
- **Minimum Requirements**: Enforced in registration forms
- **Rate Limiting**: 5 attempts per minute, 20 per hour on login endpoints

```python
# Password hashing implementation
from werkzeug.security import generate_password_hash, check_password_hash

hashed = generate_password_hash(password, method='pbkdf2:sha256', salt_length=16)
```

#### Two-Factor Authentication (2FA)

- **Method**: TOTP (Time-based One-Time Password) via `pyotp` library
- **Backup Codes**: 8 single-use recovery codes generated on 2FA setup
- **Recovery Flow**: Backup code consumption with automatic regeneration prompt
- **Session Binding**: 2FA verification required per session

Implementation: `webapp/app.py` (routes: `/auth/2fa/*`)

#### Session Management

- **Storage**: Server-side sessions in Redis
- **Cookie Flags**: `Secure`, `HttpOnly`, `SameSite=Lax`
- **Timeout**: Configurable session lifetime
- **Invalidation**: Logout clears session; password change invalidates all sessions

### Admin Authentication

- **Separate Auth System**: Admins use dedicated login at `/admin/login`
- **Password Requirements**: Minimum 12 characters recommended
- **Forced Password Change**: Required after admin password reset
- **Session Separation**: Admin sessions isolated from customer sessions

### Role-Based Access Control (RBAC)

#### Admin Roles

| Role | Permissions |
|------|-------------|
| `super_admin` | Full system access, admin user management, all customer operations |
| `admin` | Customer management, system monitoring, no admin user changes |
| `support` | View-only access for customer support, no modifications |

#### Customer Permissions

- Customers can only access their own resources
- Multi-tenant isolation enforced at database query level
- All customer operations validate ownership before execution

### Login Security

- **Brute Force Protection**: Rate limiting via Flask-Limiter
- **Account Lockout**: After repeated failures (configurable)
- **Login History**: Tracked in `customer_login_history` table
- **IP Logging**: Source IP recorded for audit purposes

## Secrets Management

### HashiCorp Vault Integration

ShopHosting.io supports HashiCorp Vault for centralized secrets management with automatic fallback to environment variables.

#### Vault Setup

```bash
# Initialize Vault
cd /opt/shophosting/vault
docker compose up -d
/opt/shophosting/scripts/vault-init.sh
```

#### Secrets Storage

| Path | Secrets Stored |
|------|----------------|
| `secret/shophosting/database` | `host`, `user`, `password` |
| `secret/shophosting/stripe` | `secret_key`, `publishable_key`, `webhook_secret` |
| `secret/shophosting/app` | `secret_key` (Flask) |
| `secret/shophosting/redis` | `password` |

#### Access Control

- **Policy**: Least-privilege access via `policies/shophosting-policy.hcl`
- **Authentication**: AppRole for automated access (role_id + secret_id)
- **Audit Logging**: All secret access logged by Vault

#### Fallback Behavior

If Vault is unavailable, the `SecretsManager` class gracefully falls back to environment variables:

```python
from secrets import get_secret

# Tries Vault first, falls back to env var
db_password = get_secret('shophosting/database', 'password', env_fallback='DB_PASSWORD')
```

### Environment Variable Security

When not using Vault:

- `.env` file permissions: `chmod 600`
- Never commit `.env` to version control
- Use strong, unique values for all secrets
- Rotate secrets periodically

### Secrets Inventory

| Secret | Storage | Rotation Frequency |
|--------|---------|-------------------|
| Flask SECRET_KEY | Vault or .env | Annually |
| DB_PASSWORD | Vault or .env | Quarterly |
| STRIPE_SECRET_KEY | Vault or .env | As needed |
| STRIPE_WEBHOOK_SECRET | Vault or .env | As needed |
| REDIS_PASSWORD | Vault or .env | Quarterly |
| RESTIC_PASSWORD | File (/root/.restic-password) | Never (backup access) |
| Admin Passwords | Database (hashed) | 90 days recommended |

## Data Protection

### Encryption

#### In Transit

- **TLS 1.2+**: All external connections require TLS
- **Let's Encrypt**: Automatic certificate provisioning and renewal
- **HSTS**: Strict Transport Security enabled via Flask-Talisman
- **Internal Traffic**: Consider mutual TLS for production deployments

#### At Rest

- **Database**: MySQL encryption at rest (if using encrypted storage)
- **Backups**: Encrypted with restic using AES-256
- **Vault Secrets**: Encrypted with Vault's seal key
- **Customer Data**: Stored in Docker volumes (recommend encrypted filesystem)

### Password Storage

Never store plaintext passwords:

| Data | Storage Method |
|------|----------------|
| Customer passwords | PBKDF2-SHA256 hash |
| Admin passwords | PBKDF2-SHA256 hash |
| Database credentials | Vault or encrypted .env |
| Container credentials | Database (encrypted recommended) |

### Data Isolation

- **Multi-Tenant**: Each customer's data isolated by customer_id
- **Database Queries**: All queries filter by customer_id
- **File System**: Separate directory per customer (`/var/customers/customer-{id}/`)
- **Docker Networks**: Isolated bridge network per customer

### Backup Security

- **Encryption**: restic encrypts all backup data with AES-256
- **Access Control**: SSH key authentication to backup server
- **Password Protection**: Restic password stored in `/root/.restic-password` (mode 600)
- **Retention**: 30-day default retention with secure deletion

## Network Security

### Firewall Configuration

Recommended UFW rules:

```bash
# Default deny incoming
sudo ufw default deny incoming
sudo ufw default allow outgoing

# Allow SSH (consider restricting source IPs)
sudo ufw allow 22/tcp

# Allow HTTP/HTTPS
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp

# Enable firewall
sudo ufw enable
```

### Service Binding

Services bind to localhost only (except nginx):

| Service | Binding | Exposure |
|---------|---------|----------|
| Flask/Gunicorn | 127.0.0.1:5000 | Via nginx only |
| MySQL | 127.0.0.1:3306 | Local only |
| Redis | 127.0.0.1:6379 | Local only |
| Prometheus | 127.0.0.1:9090 | Local only |
| Grafana | 127.0.0.1:3000 | Via nginx |
| Vault | 127.0.0.1:8200 | Local only |
| Customer Containers | 127.0.0.1:8001-8100 | Via nginx |

### fail2ban Configuration

Intrusion prevention for SSH and web attacks:

```bash
# Install fail2ban
sudo apt install fail2ban

# Deploy configuration
sudo cp /opt/shophosting/configs/fail2ban-jail.local /etc/fail2ban/jail.local
sudo systemctl enable --now fail2ban
```

Default configuration:
- **SSH**: Ban after 3 failed attempts for 1 hour
- **Aggressive SSH**: Ban repeat offenders for 24 hours
- **Web Auth**: Ban after 5 failed login attempts

### Rate Limiting

Application-level rate limiting via Flask-Limiter:

| Endpoint | Limit | Window |
|----------|-------|--------|
| `/login` | 5 requests | Per minute |
| `/login` | 20 requests | Per hour |
| `/admin/login` | 3 requests | Per minute |
| `/signup` | 30 requests | Per hour |
| `/contact` | 5 requests | Per hour |
| `/api/*` | 60 requests | Per minute |

### Security Headers

Flask-Talisman configures security headers:

```python
# Implemented headers
Content-Security-Policy: default-src 'self'; ...
X-Content-Type-Options: nosniff
X-Frame-Options: SAMEORIGIN
X-XSS-Protection: 1; mode=block
Strict-Transport-Security: max-age=31536000; includeSubDomains
Referrer-Policy: strict-origin-when-cross-origin
```

## Container Security

### Docker Security

#### Resource Limits

Each customer container has enforced limits:

```yaml
# From Docker Compose templates
deploy:
  resources:
    limits:
      cpus: '1.0'
      memory: 1G
    reservations:
      cpus: '0.25'
      memory: 256M
```

#### Network Isolation

- Each customer gets an isolated bridge network
- Containers cannot communicate across customer networks
- Only exposed ports are accessible (via nginx)

```yaml
networks:
  customer-{id}-network:
    driver: bridge
    internal: false  # Allows outbound internet
```

#### Non-Root Execution

- WordPress containers run as www-data (UID 33)
- Consider using `--userns-remap` for additional isolation
- Avoid privileged containers

#### Image Security

- Use official base images (WordPress, MySQL, Redis)
- Pin image versions (avoid `latest` in production)
- Regularly update images for security patches
- Scan images with `docker scan` or Trivy

### Auto-Suspension

Customers exceeding resource limits are automatically suspended:

| Threshold | Action |
|-----------|--------|
| 80% disk/bandwidth | Warning email |
| 90% disk/bandwidth | Critical warning email |
| 100% disk/bandwidth | Auto-suspend, stop containers |

Implementation: `provisioning/resource_worker.py`

## Infrastructure Hardening

### Operating System

```bash
# Keep system updated
sudo apt update && sudo apt upgrade -y

# Enable automatic security updates
sudo apt install unattended-upgrades
sudo dpkg-reconfigure unattended-upgrades

# Disable root SSH login
sudo sed -i 's/PermitRootLogin yes/PermitRootLogin no/' /etc/ssh/sshd_config

# Use SSH key authentication only
sudo sed -i 's/#PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo systemctl restart sshd
```

### Systemd Hardening

Service files include security options:

```ini
[Service]
# Prevent privilege escalation
NoNewPrivileges=true

# Private /tmp
PrivateTmp=true

# Read-only filesystem (where possible)
ProtectSystem=strict

# Restrict capabilities
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
```

### Database Hardening

```sql
-- Use strong passwords
ALTER USER 'shophosting_app'@'localhost' IDENTIFIED BY 'strong-random-password';

-- Limit privileges (principle of least privilege)
GRANT SELECT, INSERT, UPDATE, DELETE ON shophosting_db.* TO 'shophosting_app'@'localhost';

-- Separate read-only user for replicas
CREATE USER 'shophosting_read'@'%' IDENTIFIED BY 'read-password';
GRANT SELECT ON shophosting_db.* TO 'shophosting_read'@'%';

-- Remove anonymous users
DELETE FROM mysql.user WHERE User='';

-- Disable remote root
DELETE FROM mysql.user WHERE User='root' AND Host NOT IN ('localhost', '127.0.0.1', '::1');
```

### File Permissions

```bash
# Application code (read-only for app user)
chmod -R 755 /opt/shophosting
chmod 600 /opt/shophosting/.env

# Customer data (owned by app user)
chown -R shophosting:shophosting /var/customers
chmod 750 /var/customers

# Logs (writable by app)
chmod 755 /opt/shophosting/logs
chmod 644 /opt/shophosting/logs/*.log

# Secrets
chmod 600 /root/.restic-password
chmod 600 /opt/shophosting/vault/.vault-secrets
```

## Security Monitoring

### Audit Logging

The application logs security-relevant events:

| Event | Logged Data |
|-------|-------------|
| Login success/failure | Email, IP, timestamp, user agent |
| Password change | Customer ID, timestamp |
| 2FA enable/disable | Customer ID, timestamp |
| Admin actions | Admin user, action, target, timestamp |
| API key usage | Key ID, endpoint, timestamp |
| Customer suspension | Customer ID, reason, admin |

Database table: `audit_log`

### Log Aggregation

Centralized logging with Loki enables security analysis:

```logql
# Failed login attempts
{job="shophosting-webapp"} |= "login failed"

# Admin actions
{job="shophosting-webapp"} |= "[ADMIN]"

# Suspicious patterns
{job="nginx"} | json | status >= 400 | rate > 100

# Rate limiting hits
{job="shophosting-webapp"} |= "rate limit"
```

### Alerting

AlertManager configured for security alerts:

| Alert | Condition | Severity |
|-------|-----------|----------|
| HighLoginFailures | >10 failures in 5 min | warning |
| AdminLoginFromNewIP | New IP for admin login | warning |
| SuspiciousAPIUsage | Unusual API patterns | warning |
| WebappDown | Application unreachable | critical |

### Metrics

Security-relevant Prometheus metrics:

```
# Login attempts
shophosting_login_attempts_total{status="success|failure"}

# Rate limiting
flask_limiter_hit_total

# Active sessions
shophosting_active_sessions

# Failed webhooks
shophosting_webhook_failures_total
```

## Vulnerability Management

### Dependency Scanning

Regular dependency audits:

```bash
# Python dependencies
cd /opt/shophosting/webapp
source venv/bin/activate
pip-audit

# Check for known vulnerabilities
pip install safety
safety check
```

### Security Scanning

```bash
# Static analysis
bandit -r webapp/ -ll -ii

# Docker image scanning
docker scan shophosting/wordpress:latest

# Using Trivy
trivy image shophosting/wordpress:latest
```

### Update Process

1. **Monitor**: Subscribe to security advisories for dependencies
2. **Test**: Apply updates in staging environment first
3. **Apply**: Use rolling restart for zero-downtime updates
4. **Verify**: Run test suite after updates

```bash
# Update Python dependencies
pip install --upgrade -r requirements.txt
pytest tests/ -v

# Update Docker images
docker pull wordpress:latest
docker pull mysql:8
docker compose up -d
```

### Patching Schedule

| Component | Frequency | Method |
|-----------|-----------|--------|
| OS packages | Weekly | `apt upgrade` |
| Python deps | Monthly | `pip-audit` + upgrade |
| Docker images | Monthly | Pull and rebuild |
| Critical CVEs | Immediate | Emergency patching |

## Incident Response

### Response Procedures

#### 1. Detection

- Monitor alerts from AlertManager, Grafana, and logs
- Review security dashboards daily
- Respond to customer reports

#### 2. Triage

Classify severity:

| Severity | Description | Response Time |
|----------|-------------|---------------|
| Critical | Active breach, data exposure | Immediate |
| High | Vulnerability actively exploited | < 4 hours |
| Medium | Potential vulnerability | < 24 hours |
| Low | Minor security issue | < 1 week |

#### 3. Containment

```bash
# Isolate affected customer
docker compose -f /var/customers/customer-{id}/docker-compose.yml down

# Block malicious IP
sudo ufw deny from <IP>

# Disable compromised account
mysql -e "UPDATE customers SET status='suspended' WHERE id={id}"

# Revoke API keys
mysql -e "UPDATE customer_api_keys SET revoked=1 WHERE customer_id={id}"
```

#### 4. Investigation

```bash
# Collect logs
grep "customer-{id}" /opt/shophosting/logs/*.log > incident-logs.txt

# Check login history
mysql -e "SELECT * FROM customer_login_history WHERE customer_id={id} ORDER BY login_at DESC"

# Review audit log
mysql -e "SELECT * FROM audit_log WHERE customer_id={id} ORDER BY created_at DESC"
```

#### 5. Remediation

- Reset compromised credentials
- Patch vulnerabilities
- Restore from clean backup if needed
- Notify affected parties

#### 6. Recovery

- Verify system integrity
- Re-enable services
- Monitor for recurrence

#### 7. Post-Incident

- Document incident timeline
- Conduct root cause analysis
- Implement preventive measures
- Update runbooks

### Emergency Contacts

Maintain an incident response contact list:

- Primary on-call: [contact]
- Secondary on-call: [contact]
- Security team: [contact]
- Legal counsel: [contact]

### Runbooks

See `docs/runbooks/` for detailed procedures:

- `disaster-recovery.md` - Full DR procedures
- `database-failover.md` - MySQL failover
- `customer-restore.md` - Customer data recovery

## Compliance

### Data Protection

ShopHosting.io implements controls to support:

- **GDPR**: Customer data export, deletion requests, consent tracking
- **PCI DSS**: Stripe handles payment card data; no card numbers stored
- **SOC 2**: Audit logging, access controls, encryption

### Customer Data Rights

Customers can:

| Right | Implementation |
|-------|----------------|
| Access | Data export via dashboard |
| Rectification | Profile editing |
| Erasure | Account deletion request |
| Portability | Data export in standard formats |

Database tables:
- `customer_data_exports` - Export request tracking
- `customer_deletion_requests` - Deletion request tracking

### Data Retention

| Data Type | Retention | Justification |
|-----------|-----------|---------------|
| Customer accounts | Until deletion | Service provision |
| Login history | 90 days | Security auditing |
| Audit logs | 1 year | Compliance |
| Backups | 30 days | Disaster recovery |
| Metrics | 30 days | Operations |
| Logs | 30 days | Troubleshooting |

## Security Checklist

### Deployment Checklist

- [ ] Strong SECRET_KEY generated and stored securely
- [ ] Database passwords are strong and unique
- [ ] `.env` file permissions set to 600
- [ ] Vault initialized and secrets migrated (if using)
- [ ] TLS certificates installed and auto-renewing
- [ ] Firewall configured (UFW)
- [ ] fail2ban installed and configured
- [ ] SSH key-only authentication enabled
- [ ] Root SSH login disabled
- [ ] Security headers verified (use securityheaders.com)
- [ ] Rate limiting tested
- [ ] Backup encryption verified
- [ ] Admin 2FA enabled

### Operational Checklist

- [ ] Regular dependency updates (weekly)
- [ ] Security advisory monitoring
- [ ] Log review (daily)
- [ ] Backup verification (monthly)
- [ ] DR test (quarterly)
- [ ] Access review (quarterly)
- [ ] Password rotation (per policy)
- [ ] Certificate renewal monitoring

## Reporting Security Issues

### Responsible Disclosure

If you discover a security vulnerability, please report it responsibly:

1. **Email**: security@shophosting.io
2. **Do not** disclose publicly until patched
3. **Do not** create a public GitHub issue
4. **Include**:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Suggested remediation (if any)

### Response Timeline

- **Acknowledgment**: Within 24 hours
- **Initial Assessment**: Within 72 hours
- **Resolution Target**: Based on severity
  - Critical: 24 hours
  - High: 7 days
  - Medium: 30 days
  - Low: 90 days

### Bug Bounty

We appreciate security researchers who help improve our security. Please contact us for information about our bug bounty program.

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.x     | :white_check_mark: |

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 2.0 | 2026-01-30 | Comprehensive security documentation with Vault, HA, and monitoring |
| 1.0 | 2026-01-28 | Initial security documentation |

---

*This document is reviewed and updated quarterly. Last review: January 2026*
