# Cloudflare DNS Management Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Enable customers to connect their Cloudflare account via OAuth and manage DNS records directly from the ShopHosting dashboard.

**Architecture:** Flask blueprint for Cloudflare integration with OAuth flow, encrypted token storage, and Cloudflare API wrapper. Updates existing domains.html template with DNS management UI. Records cached locally for fast display.

**Tech Stack:** Flask, Cloudflare API v4, Fernet encryption, MySQL

---

## Task 1: Database Migration

**Files:**
- Create: `migrations/014_add_cloudflare_tables.sql`

**Step 1: Create migration file**

```sql
-- Migration: 014_add_cloudflare_tables.sql
-- Description: Add tables for Cloudflare DNS integration
-- Created: 2026-01-30

-- Table: customer_cloudflare_connections
-- Stores OAuth tokens for customer Cloudflare accounts
CREATE TABLE IF NOT EXISTS customer_cloudflare_connections (
    id INT AUTO_INCREMENT PRIMARY KEY,
    customer_id INT NOT NULL UNIQUE,
    cloudflare_zone_id VARCHAR(50) NULL,
    access_token TEXT NOT NULL,
    refresh_token TEXT NULL,
    token_expires_at DATETIME NULL,
    connected_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_sync_at DATETIME NULL,
    CONSTRAINT fk_cloudflare_customer_id FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
    INDEX idx_cloudflare_customer_id (customer_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Table: dns_records_cache
-- Caches DNS records from Cloudflare for fast display
CREATE TABLE IF NOT EXISTS dns_records_cache (
    id INT AUTO_INCREMENT PRIMARY KEY,
    customer_id INT NOT NULL,
    cloudflare_record_id VARCHAR(50) NOT NULL,
    record_type ENUM('A', 'CNAME', 'MX', 'TXT') NOT NULL,
    name VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    priority INT NULL,
    proxied BOOLEAN NOT NULL DEFAULT FALSE,
    ttl INT NOT NULL DEFAULT 1,
    synced_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_dns_cache_customer_id FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
    INDEX idx_dns_cache_customer_id (customer_id),
    UNIQUE KEY uk_cloudflare_record_id (cloudflare_record_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

**Step 2: Run migration**

```bash
mysql -u shophosting_app -p shophosting_db < migrations/014_add_cloudflare_tables.sql
```

**Step 3: Verify tables exist**

```bash
mysql -u shophosting_app -p shophosting_db -e "SHOW TABLES LIKE '%cloudflare%'; SHOW TABLES LIKE 'dns_%';"
```

**Step 4: Commit**

```bash
git add migrations/014_add_cloudflare_tables.sql
git commit -m "feat(db): add Cloudflare integration tables"
```

---

## Task 2: Cloudflare Models

**Files:**
- Create: `webapp/cloudflare/__init__.py`
- Create: `webapp/cloudflare/models.py`

**Step 1: Create blueprint init file**

```python
# webapp/cloudflare/__init__.py
"""Cloudflare DNS Management Blueprint"""

from flask import Blueprint

cloudflare_bp = Blueprint('cloudflare', __name__, url_prefix='/dashboard/cloudflare')

from . import routes
```

**Step 2: Create models file with CloudflareConnection and DNSRecordCache classes**

The models file should include:
- `get_encryption_key()` - Derive Fernet key from SECRET_KEY
- `encrypt_token()` / `decrypt_token()` - Token encryption helpers
- `CloudflareConnection` class with:
  - Properties for encrypted token access
  - `is_token_expired()` method
  - `save()`, `delete()`, `get_by_customer_id()` methods
- `DNSRecordCache` class with:
  - `save()`, `get_by_customer_id()`, `delete_by_cloudflare_id()`, `clear_customer_cache()` methods

**Step 3: Verify imports work**

```bash
cd /opt/shophosting/webapp && python3 -c "from cloudflare.models import CloudflareConnection, DNSRecordCache; print('Models OK')"
```

**Step 4: Commit**

```bash
git add webapp/cloudflare/__init__.py webapp/cloudflare/models.py
git commit -m "feat(cloudflare): add connection and DNS cache models"
```

---

## Task 3: Cloudflare API Wrapper

**Files:**
- Create: `webapp/cloudflare/api.py`

**Step 1: Create API wrapper with:**
- `CloudflareAPIError` exception class
- `CloudflareAPI` class with methods:
  - `get_zones()` - List all zones
  - `get_zone_by_name(domain)` - Find zone for domain
  - `get_dns_records(zone_id, record_types)` - List DNS records
  - `create_dns_record()` - Create record
  - `update_dns_record()` - Update record
  - `delete_dns_record()` - Delete record
- Helper functions:
  - `get_oauth_authorize_url()` - Build OAuth URL
  - `exchange_code_for_tokens()` - Exchange auth code
  - `refresh_access_token()` - Refresh expired token

**Step 2: Verify imports work**

```bash
cd /opt/shophosting/webapp && python3 -c "from cloudflare.api import CloudflareAPI, get_oauth_authorize_url; print('API OK')"
```

**Step 3: Commit**

```bash
git add webapp/cloudflare/api.py
git commit -m "feat(cloudflare): add API wrapper for Cloudflare v4"
```

---

## Task 4: OAuth and DNS Routes

**Files:**
- Create: `webapp/cloudflare/routes.py`

**Step 1: Create routes file with endpoints:**
- `GET /connect` - Initiate OAuth flow
- `GET /callback` - Handle OAuth callback
- `GET /confirm` - Show confirmation screen
- `POST /confirm` - Apply DNS changes
- `POST /disconnect` - Remove connection
- `GET /api/records` - List DNS records (JSON)
- `POST /api/records` - Create record
- `PUT /api/records/<id>` - Update record
- `DELETE /api/records/<id>` - Delete record
- `POST /api/sync` - Force sync from Cloudflare

Include helper functions:
- `get_cloudflare_api()` - Get API instance with token refresh
- `sync_dns_records()` - Sync records to cache

**Step 2: Verify imports work**

```bash
cd /opt/shophosting/webapp && python3 -c "from cloudflare import cloudflare_bp; print('Routes OK')"
```

**Step 3: Commit**

```bash
git add webapp/cloudflare/routes.py
git commit -m "feat(cloudflare): add OAuth and DNS management routes"
```

---

## Task 5: Register Blueprint in App

**Files:**
- Modify: `webapp/app.py`

**Step 1: Add blueprint registration after other blueprints (around line 360)**

```python
# Register Cloudflare DNS management blueprint
from cloudflare import cloudflare_bp
app.register_blueprint(cloudflare_bp)
```

**Step 2: Add environment variables to .env**

```bash
# Cloudflare OAuth
CLOUDFLARE_CLIENT_ID=
CLOUDFLARE_CLIENT_SECRET=
CLOUDFLARE_REDIRECT_URI=https://shophosting.io/dashboard/cloudflare/callback
```

**Step 3: Verify app starts**

```bash
cd /opt/shophosting/webapp && python3 -c "from app import app; print('App OK')"
```

**Step 4: Commit**

```bash
git add webapp/app.py
git commit -m "feat(app): register Cloudflare blueprint"
```

---

## Task 6: Confirmation Template

**Files:**
- Create: `webapp/templates/dashboard/cloudflare_confirm.html`

**Step 1: Create template extending base_dashboard.html with:**
- Header explaining the review process
- List of existing records with REPLACE/KEEP labels
- Checkboxes for records to replace
- Proposed changes section showing new A and CNAME records
- Warning about MX/TXT preservation
- Cancel and Confirm buttons

**Step 2: Commit**

```bash
git add webapp/templates/dashboard/cloudflare_confirm.html
git commit -m "feat(cloudflare): add DNS confirmation template"
```

---

## Task 7: Update Domains Template

**Files:**
- Modify: `webapp/templates/dashboard/domains.html`
- Modify: `webapp/app.py` (dashboard_domains route)

**Step 1: Update domains.html to show either:**
- **If connected:** DNS records table with add/edit/delete buttons, connection status, disconnect button
- **If not connected:** Connect Cloudflare button with signup link, manual DNS fallback section

**Step 2: Update dashboard_domains route to pass:**
- `cloudflare_connected` boolean
- `dns_records` list from cache
- `last_sync_time` formatted string

**Step 3: Commit**

```bash
git add webapp/templates/dashboard/domains.html webapp/app.py
git commit -m "feat(cloudflare): update domains page with DNS management UI"
```

---

## Task 8: Add Record Modal

**Files:**
- Modify: `webapp/templates/dashboard/domains.html`

**Step 1: Add modal for add/edit DNS records with:**
- Type dropdown (A, CNAME, MX, TXT)
- Name input with domain suffix
- Content input
- Priority input (for MX, hidden otherwise)
- Proxied checkbox (for A/CNAME)
- JavaScript for open/close, form submission via fetch API

**Step 2: Add event listeners for:**
- Add button opens modal in add mode
- Edit buttons open modal in edit mode with pre-filled data
- Delete buttons confirm and call DELETE API
- Form submission calls POST/PUT API

**Step 3: Commit**

```bash
git add webapp/templates/dashboard/domains.html
git commit -m "feat(cloudflare): add DNS record add/edit/delete modal"
```

---

## Task 9: Final Testing & Documentation

**Step 1: Verify all imports work**

```bash
cd /opt/shophosting/webapp && python3 -c "
from app import app
from cloudflare import cloudflare_bp
from cloudflare.models import CloudflareConnection, DNSRecordCache
from cloudflare.api import CloudflareAPI
print('All imports OK')
"
```

**Step 2: Run the migration**

```bash
mysql -u shophosting_app -p shophosting_db < /opt/shophosting/migrations/014_add_cloudflare_tables.sql
```

**Step 3: Reload the application**

```bash
kill -HUP $(pgrep -f "gunicorn.*app:app" | head -1)
```

**Step 4: Update README - add DNS Management to features**

**Step 5: Final commit**

```bash
git add -A
git commit -m "docs: update README with DNS management feature"
```

---

## Environment Variables Required

Add these to `/opt/shophosting/.env`:

```bash
# Cloudflare OAuth
# Register app at: https://dash.cloudflare.com/profile/api-tokens (OAuth section)
CLOUDFLARE_CLIENT_ID=your_client_id_here
CLOUDFLARE_CLIENT_SECRET=your_client_secret_here
CLOUDFLARE_REDIRECT_URI=https://shophosting.io/dashboard/cloudflare/callback
```

---

## Testing Checklist

- [ ] Can click "Connect Cloudflare" and be redirected to Cloudflare OAuth
- [ ] After authorizing, redirected back to ShopHosting
- [ ] If existing records, see confirmation screen
- [ ] After confirmation, DNS records are created/updated
- [ ] Can view DNS records in table
- [ ] Can add new DNS record via modal
- [ ] Can edit existing DNS record
- [ ] Can delete DNS record
- [ ] Can disconnect Cloudflare
- [ ] After disconnect, shows connect button again
