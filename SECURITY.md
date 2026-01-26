# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.x     | :white_check_mark: |

## Reporting a Vulnerability

If you discover a security vulnerability in ShopHosting.io, please report it responsibly:

1. **Do NOT** create a public GitHub issue for security vulnerabilities
2. Email security concerns to: security@shophosting.io
3. Include:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Any suggested fixes

We aim to respond within 48 hours and will work with you to understand and address the issue.

---

## Security Architecture

### Authentication & Authorization

- **Customer Authentication**: Flask-Login with secure session management
- **Admin Authentication**: Separate session with role-based access control
- **Password Storage**: Werkzeug security with PBKDF2-SHA256 hashing
- **Session Security**:
  - HTTPOnly cookies (JavaScript cannot access)
  - Secure flag in production (HTTPS only)
  - SameSite=Lax (CSRF protection)
  - 30-minute idle timeout
  - 24-hour maximum session lifetime

### Rate Limiting

Rate limits are enforced using Flask-Limiter with Redis backend:

| Endpoint | Limit |
|----------|-------|
| Customer Login | 5/minute, 20/hour |
| Admin Login | 3/minute, 10/hour |
| Signup | 10/hour |
| Contact Form | 5/hour |
| Consultation Booking | 5/hour |
| Backup Trigger | 3/hour |
| Restore Trigger | 2/hour |
| Global Default | 200/day, 50/hour |

### Security Headers

Implemented via Flask-Talisman:

- **Content-Security-Policy**: Restricts script/style sources
- **Strict-Transport-Security**: Forces HTTPS for 1 year
- **X-Content-Type-Options**: nosniff
- **X-Frame-Options**: DENY (clickjacking protection)
- **Referrer-Policy**: strict-origin-when-cross-origin

### Input Validation

- **Domain validation**: Regex pattern matching
- **Email validation**: WTForms Email validator
- **File uploads**:
  - Extension whitelist
  - Magic number (file signature) validation
  - 10MB size limit
  - Restrictive file permissions (0644)
- **API inputs**: JSON schema validation where applicable
- **Backup snapshot IDs**: Hex format validation (8-64 chars)

### Data Protection

- **Database**: Parameterized queries (SQL injection prevention)
- **Templates**: Jinja2 autoescaping (XSS prevention)
- **CSRF**: Flask-WTF CSRF tokens on all forms
- **Secrets**: Environment variables, not hardcoded
- **Backups**: Encrypted with Restic (AES-256)

---

## Security Logging

Security events are logged to `/opt/shophosting/logs/security.log`:

- Login attempts (success/failure)
- Admin authentication events
- Session timeouts
- Rate limit violations
- Backup/restore operations
- File upload validation failures

### Log Format
```
YYYY-MM-DD HH:MM:SS - LEVEL - EVENT_TYPE: details
```

### Monitored Events
- `ADMIN_LOGIN_SUCCESS` / `ADMIN_LOGIN_FAILED`
- `SESSION_TIMEOUT` / `ADMIN_SESSION_TIMEOUT`
- `BACKUP_STARTED` / `RESTORE_STARTED`
- `REQUEST` (sensitive endpoints)
- `FAILED REQUEST` (401/403 responses)

---

## Configuration Requirements

### Required Environment Variables

These must be set - the application will not start without them:

| Variable | Description |
|----------|-------------|
| `SECRET_KEY` | Flask session encryption key (32+ chars hex) |
| `DB_PASSWORD` | Database password |

### Recommended Security Configuration

```bash
# Generate secure secret key
python3 -c "import secrets; print(secrets.token_hex(32))"

# Set proper file permissions
chmod 600 /opt/shophosting/.env
chmod 600 /opt/shophosting/.system-restic-password

# Environment should be set to production
FLASK_ENV=production
FLASK_DEBUG=false
```

---

## Incident Response

### If You Suspect a Breach

1. **Contain**: Disable affected accounts/services
2. **Preserve**: Do not delete logs or evidence
3. **Notify**: Contact security@shophosting.io immediately
4. **Document**: Record timeline and observations

### Key Contacts

- Security Team: security@shophosting.io
- On-Call: See internal runbook

### Recovery Procedures

1. Rotate all secrets (database, API keys, session keys)
2. Invalidate all active sessions
3. Review audit logs
4. Restore from verified clean backup if needed
5. Post-incident review within 72 hours

---

## Compliance

### Data Handling

- Customer credentials stored encrypted
- Payment processing via Stripe (PCI DSS compliant)
- Backups encrypted at rest
- Logs retained for 90 days minimum

### Access Control

- Principle of least privilege
- Admin actions audit logged
- Separate admin/customer authentication
- Role-based access for admin panel

---

## Security Checklist

### Before Production

- [ ] Generate and set unique `SECRET_KEY`
- [ ] Set `FLASK_ENV=production`
- [ ] Verify `.env` file permissions (600)
- [ ] Verify password file permissions (600)
- [ ] Enable HTTPS (Let's Encrypt)
- [ ] Configure firewall rules
- [ ] Set up log rotation
- [ ] Test backup/restore procedures
- [ ] Review rate limiting configuration

### Regular Maintenance

- [ ] Rotate secrets quarterly
- [ ] Review security logs weekly
- [ ] Update dependencies monthly
- [ ] Penetration testing annually
- [ ] Backup verification monthly

---

## Dependency Security

Dependencies are monitored for vulnerabilities:

```bash
# Check for known vulnerabilities
pip-audit

# Update dependencies
pip install --upgrade -r requirements.txt
```

The CI/CD pipeline includes:
- `pip-audit` for vulnerability scanning
- `bandit` for Python security analysis
- `flake8` for code quality

---

## Known Limitations

1. **Email Security**: Welcome emails contain initial admin passwords. Consider implementing secure password reset tokens instead.

2. **2FA**: Not currently implemented for admin accounts. Recommended for production.

3. **IP Allowlisting**: Admin panel accessible from any IP. Consider implementing IP restrictions for admin access.

---

*Last Updated: January 2026*
