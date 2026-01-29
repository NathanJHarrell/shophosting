# Settings Page - Security Features Design

**Date:** 2026-01-29
**Status:** Approved
**Scope:** Password change, 2FA, session management, login history

## Overview

Implement security-focused settings features for the customer dashboard. This is Phase 1 of a larger settings implementation, focusing on account security before convenience features.

## Features

1. **Password Change** - Verify current password, set new one
2. **Two-Factor Authentication (2FA)** - TOTP-based with backup codes and email fallback
3. **Login History** - Audit trail of login attempts
4. **Session Management** - View activity, logout all sessions

## Database Schema

### New Tables

```sql
-- 2FA settings per customer
CREATE TABLE customer_2fa_settings (
    id INT PRIMARY KEY AUTO_INCREMENT,
    customer_id INT NOT NULL UNIQUE,
    totp_secret VARCHAR(32),
    is_enabled BOOLEAN DEFAULT FALSE,
    backup_codes TEXT,
    backup_codes_remaining INT DEFAULT 10,
    last_used_at TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
);

-- Login history for audit trail
CREATE TABLE customer_login_history (
    id INT PRIMARY KEY AUTO_INCREMENT,
    customer_id INT NOT NULL,
    ip_address VARCHAR(45),
    user_agent VARCHAR(500),
    location VARCHAR(100),
    success BOOLEAN DEFAULT TRUE,
    failure_reason VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
    INDEX idx_customer_created (customer_id, created_at)
);

-- Verification tokens for 2FA email recovery
CREATE TABLE customer_verification_tokens (
    id INT PRIMARY KEY AUTO_INCREMENT,
    customer_id INT NOT NULL,
    token VARCHAR(64) NOT NULL UNIQUE,
    token_type ENUM('2fa_recovery', 'email_change') NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    used_at TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
);
```

### Schema Modifications

```sql
ALTER TABLE customers ADD COLUMN password_changed_at TIMESTAMP NULL;
```

## API Routes

### Password
- `POST /api/settings/password` - Change password

### Two-Factor Authentication
- `POST /api/settings/2fa/setup` - Generate TOTP secret, return QR code
- `POST /api/settings/2fa/verify` - Verify code and enable 2FA
- `POST /api/settings/2fa/disable` - Disable 2FA
- `POST /api/settings/2fa/backup-codes/regenerate` - New backup codes
- `POST /api/settings/2fa/recovery/email` - Send recovery email
- `POST /api/settings/2fa/recovery/verify` - Verify email code

### Login Flow
- `GET /auth/2fa` - 2FA verification page
- `POST /auth/2fa/verify` - Verify 2FA during login

### Sessions
- `GET /api/settings/sessions` - Get login history
- `POST /api/settings/sessions/logout-all` - Logout all sessions

## 2FA Login Flow

1. User submits email + password
2. If valid, check if 2FA enabled
3. If no 2FA → logged in normally
4. If 2FA enabled → store `pending_2fa_customer_id` in session, redirect to `/auth/2fa`
5. User enters TOTP code (or backup code, or email recovery)
6. If valid → complete login
7. Max 5 attempts, then 15-minute lockout

### Email Recovery Flow
1. User requests email recovery
2. Generate 8-char code, hash and store, expires in 15 min
3. Send via existing email system
4. User enters code to complete login

## Settings Page UI

### Cards

1. **Account Information** - Email, company name, member since
2. **Password** - Last changed date, change button
3. **Two-Factor Authentication** - Status, setup/disable, backup codes
4. **Login Activity** - Recent logins table, logout all button

### Modals
- Password change modal
- 2FA setup modal (QR code + verify)
- 2FA disable confirmation
- Backup codes display

## Files to Change

### New Files
- `migrations/014_add_settings_tables.sql`
- `templates/auth/2fa_verify.html`

### Modified Files
- `models.py` - New model classes
- `app.py` - New routes, modified login
- `templates/dashboard/settings.html` - Full rewrite
- `email_utils.py` - Add recovery email function

## Dependencies

- `pyotp` - TOTP generation/verification
- `qrcode` - QR code generation (or client-side JS)

## Implementation Order

1. Database migration
2. Models (Customer2FASettings, CustomerLoginHistory, CustomerVerificationToken)
3. Password change
4. Login history tracking
5. 2FA setup/enable
6. 2FA login verification
7. 2FA disable + backup codes
8. Email recovery
9. Session logout-all
10. Settings page UI

## Security Considerations

- Backup codes stored as hashed values
- TOTP secrets encrypted at rest (consider)
- Rate limiting on 2FA attempts (5 attempts, 15-min lockout)
- Login history includes failed attempts for security monitoring
- Email recovery codes expire in 15 minutes
- Password change requires current password verification
