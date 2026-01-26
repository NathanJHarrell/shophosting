# ShopHosting.io

A multi-tenant Docker hosting platform that automatically provisions containerized e-commerce stores (WooCommerce and Magento) on-demand.

## Features

- **Automated Provisioning**: Customers sign up and receive a fully configured online store within minutes
- **Multi-Platform Support**: WooCommerce (WordPress) and Magento 2 with Varnish caching
- **Self-Service Dashboard**: Customers can view store status, credentials, and manage their stores
- **Admin Panel**: Full-featured admin interface for system monitoring and customer management
  - **Admin User Management**: Super admins can create, edit, and manage other admin users with role-based access control
  - **Live Provisioning Logs**: Real-time persistent logs showing detailed provisioning progress on customer pages
  - **CMS for Marketing Pages**: Full WYSIWYG editor for homepage, pricing, features, about, and contact pages with draft/publish workflow and version history
  - **Stripe Pricing Sync**: Two-way sync between local pricing plans and Stripe with choice dialog for creating new prices or updating existing ones
- **Background Job Processing**: Redis-backed queue system for reliable provisioning
- **SSL/TLS Support**: Automatic certificate management with Let's Encrypt
- **Resource Isolation**: Each customer gets isolated Docker containers with configurable resource limits
- **Automated Backups**: Daily encrypted backups to remote server using restic with 30-day retention
- **Customer Self-Service Backups**: Customers can create manual backups and restore from any snapshot with options for database-only, files-only, or full restore
- **Production Security Hardening**: Comprehensive security features for production deployment

## Security Features

ShopHosting.io includes enterprise-grade security features:

### Authentication & Session Security
- **Secure Password Storage**: PBKDF2-SHA256 hashing via Werkzeug
- **Session Protection**: HTTPOnly, Secure (HTTPS), SameSite=Lax cookies
- **Idle Timeout**: 30-minute session timeout for both customers and admins
- **CSRF Protection**: Flask-WTF CSRF tokens on all forms

### Rate Limiting
Rate limits enforced via Flask-Limiter with Redis backend:
| Endpoint | Limit |
|----------|-------|
| Customer Login | 5/min, 20/hr |
| Admin Login | 3/min, 10/hr |
| Signup | 10/hr |
| Contact/Consultation | 5/hr |
| Backup Operations | 3/hr |

### Security Headers (Flask-Talisman)
- Content-Security-Policy (CSP)
- Strict-Transport-Security (HSTS) - 1 year
- X-Content-Type-Options: nosniff
- X-Frame-Options: DENY
- Referrer-Policy: strict-origin-when-cross-origin

### Input Validation
- **File Uploads**: Extension whitelist + magic number validation
- **API Inputs**: Strict format validation (e.g., backup snapshot IDs)
- **Domain Validation**: Regex pattern matching
- **Request Size Limits**: 50MB max to prevent DoS

### Audit Logging
Security events logged to `/opt/shophosting/logs/security.log`:
- Login attempts (success/failure)
- Session timeouts
- Backup/restore operations
- Rate limit violations

### Configuration Security
- **Fail-Fast**: App refuses to start without proper `SECRET_KEY` and `DB_PASSWORD`
- **Environment Variables**: All secrets loaded from `.env`, never hardcoded
- **File Permissions**: Restrictive permissions on sensitive files

See [SECURITY.md](SECURITY.md) for complete security documentation.

## CI/CD Pipeline

The project includes a GitHub Actions CI/CD pipeline (`.github/workflows/ci.yml`) that runs on every push and pull request:

### Pipeline Stages

1. **Lint**: Code quality checks
   - `flake8` for Python syntax and style
   - `black` for code formatting (advisory)
   - `isort` for import ordering (advisory)
   - `bandit` for security analysis

2. **Test**: Automated testing
   - `pytest` with Flask test client
   - Tests for health endpoints, authentication, security headers
   - Runs against MySQL and Redis services

3. **Security**: Dependency scanning
   - `pip-audit` for known vulnerabilities
   - Secret pattern detection

### Running Tests Locally

```bash
cd /opt/shophosting/webapp
source venv/bin/activate
pytest tests/ -v
```

## Requirements

- Ubuntu 22.04 LTS (recommended)
- Python 3.10+
- Docker Engine + Docker Compose
- MySQL 8.0
- Redis Server
- Nginx
- Certbot (for SSL)

## Installation

### 1. Install System Dependencies

```bash
sudo apt update
sudo apt install -y docker.io docker-compose mysql-server redis-server nginx certbot python3-certbot-nginx python3.10 python3.10-venv
```

### 2. Create System User and Directories

```bash
# Create system user
sudo useradd -r -s /bin/bash shophosting

# Create directories
sudo mkdir -p /opt/shophosting /var/customers
sudo chown -R shophosting:shophosting /opt/shophosting /var/customers

# Add user to docker group
sudo usermod -aG docker shophosting
```

### 3. Clone the Repository

```bash
cd /opt
sudo git clone https://github.com/NJHarrell/shophosting.git
sudo chown -R shophosting:shophosting /opt/shophosting
```

### 4. Set Up the Database

```bash
sudo mysql -u root -p < /opt/shophosting/schema.sql
```

### 5. Configure Environment

```bash
cd /opt/shophosting
cp .env.example .env
nano .env  # Edit with your actual values
```

Key variables to configure:
- `SECRET_KEY`: Generate with `python3 -c "import secrets; print(secrets.token_hex(32))"`
- `DB_PASSWORD`: Your MySQL password
- `BASE_DOMAIN`: Your domain name
- `SMTP_*`: Email settings (optional)

### 6. Set Up Python Virtual Environments

```bash
# Web application
cd /opt/shophosting/webapp
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
deactivate

# Provisioning worker
cd /opt/shophosting/provisioning
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
deactivate
```

### 7. Build Custom Docker Images

```bash
cd /opt/shophosting/docker/wordpress
docker build -t shophosting/wordpress:latest .
```

### 8. Configure Sudoers for Webapp

The webapp user needs passwordless sudo access for certain operations (nginx reload, customer cleanup):

```bash
# Add sudoers entries for the webapp user
echo 'agileweb ALL=(ALL) NOPASSWD: /usr/sbin/nginx -t, /usr/bin/systemctl reload nginx, /usr/bin/certbot' | sudo tee /etc/sudoers.d/shophosting-nginx
echo 'agileweb ALL=(ALL) NOPASSWD: /usr/bin/rm -rf /var/customers/customer-*' | sudo tee /etc/sudoers.d/shophosting-cleanup
sudo chmod 440 /etc/sudoers.d/shophosting-nginx /etc/sudoers.d/shophosting-cleanup
sudo visudo -c  # Validate sudoers configuration
```

### 9. Install Systemd Services

```bash
sudo cp /opt/shophosting/shophosting-webapp.service /etc/systemd/system/
sudo cp /opt/shophosting/provisioning-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable shophosting-webapp provisioning-worker
sudo systemctl start shophosting-webapp provisioning-worker
```

### 10. Configure Nginx

Create `/etc/nginx/sites-available/shophosting`:

```nginx
server {
    server_name yourdomain.com www.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    listen 80;
}
```

Enable the site and get SSL:

```bash
sudo ln -s /etc/nginx/sites-available/shophosting /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
sudo certbot --nginx -d yourdomain.com -d www.yourdomain.com
```

## Usage

### Managing Services

```bash
# Start services
sudo systemctl start shophosting-webapp provisioning-worker

# Stop services
sudo systemctl stop shophosting-webapp provisioning-worker

# View logs
tail -f /opt/shophosting/logs/webapp.log
tail -f /opt/shophosting/logs/provisioning_worker.log

# Check service status
sudo systemctl status shophosting-webapp
sudo systemctl status provisioning-worker
```

### Checking Redis Queue

```bash
redis-cli
> KEYS rq:*
> LLEN rq:queue:provisioning
```

### Customer Container Management

```bash
# List running customer containers
docker ps --filter "name=customer-"

# View container logs
docker logs customer-{id}-web

# Stop a customer's containers
cd /var/customers/customer-{id}
docker compose down
```

## Admin Panel

ShopHosting.io includes a comprehensive admin panel for system monitoring and customer management.

### Setup

1. **Run the migrations:**
   ```bash
   mysql -u root -p shophosting_db < /opt/shophosting/migrations/002_add_admin_users.sql
   mysql -u root -p shophosting_db < /opt/shophosting/migrations/005_add_admin_features.sql
   ```

2. **Create your first admin user:**
   ```bash
   cd /opt/shophosting
   source webapp/venv/bin/activate
   python3 scripts/create_admin.py admin@example.com YourSecurePassword "Admin Name" super_admin
   ```

3. **Access the admin panel:**
   Navigate to `https://yourdomain.com/admin/login`

### Features

- **Dashboard**: Overview of customer stats, port usage, queue status, and quick action buttons
- **Customer Management**: Create, edit, delete customers with automatic provisioning
  - Quick "New Customer" button on all customer listing pages
  - Detailed customer view with store credentials and activity logs
  - Retry provisioning for failed customers with automatic cleanup
- **Provisioning Monitoring**: View queue status, job history with real-time status updates
  - Job status tracking: queued → started → finished/failed
  - Expandable, prettified error logs (auto-formats JSON and stack traces)
  - One-click retry for failed provisioning jobs
  - **Live Provisioning Logs**: Real-time progress updates on customer detail page when provisioning is in progress (auto-refreshes every 5 seconds)
- **Admin User Management**: Super admins can manage admin users
  - View all admin users (all admin roles)
  - Create new admin users with `admin` or `support` roles
  - Edit existing admin details
  - Reset passwords with automatic email notification
  - Toggle admin active status
  - Delete admin users
- **System Health**: Service status, disk usage, backup status, port allocation
- **Billing Overview**: MRR, subscription stats, recent invoices
- **Log Viewer**: View webapp and worker logs directly from the admin panel
- **Consultation Appointments**: Manage prospect consultations and sales pipeline
- **Pricing Plans Management**: Edit pricing plans directly from the admin panel

### Quick Actions

The dashboard includes quick action buttons for:
- Restart webapp/worker services
- Run manual backup
- Clear failed provisioning jobs
- View logs
- External links to Portainer and Stripe Dashboard

### Admin Roles

- `super_admin`: Full access to all features including admin user management and CMS
- `admin`: Standard admin access (can view admin users, cannot modify)
- `support`: Limited access for support staff (can view admin users, cannot modify)

### Password Management

- Admins can change their own password via the "Change Password" option in the sidebar
- Super admins can reset other admin users' passwords via the Admin Users page
- When a password is reset by a super admin:
  - A temporary 16-character secure password is generated
  - An email is sent to the admin user with the temporary password
  - The admin is forced to change their password on next login
  - The temporary password must be changed before accessing any other admin pages

### Consultation Appointments

Manage prospect consultations from the scheduler form on the marketing site.

**Access:** Navigate to `https://yourdomain.com/admin/appointments` or click "Appointments" in the admin sidebar.

**Features:**
- **Dashboard Stats**: View total appointments, today's count, this week, pending, and confirmed
- **Filtering**: Search by name/email/phone, filter by status and date range
- **Status Workflow**: Track appointments through: pending → confirmed → completed/cancelled/no_show
- **Assignment**: Assign consultations to specific admin users
- **Notes**: Add internal notes to each appointment
- **Quick Actions**: Email or call prospects directly from the detail view

**Setup:**
```bash
mysql -u root -p shophosting_db < /opt/shophosting/migrations/007_add_consultations_table.sql
```

### Pricing Plans Management

Edit WooCommerce and Magento pricing plans directly from the admin panel.

**Access:** Navigate to `https://yourdomain.com/admin/pricing-plans` or click "Pricing Plans" in the admin sidebar under "Billing".

**Features:**
- **View Plans**: See all plans organized by platform (WooCommerce, Magento)
- **Edit Plan Details**: Name, monthly price, store limit, display order
- **Resource Allocation**: Configure memory (1-16GB) and CPU limits (0.5-8 cores)
- **Feature Toggles**: Enable/disable plan features:
  - Daily Backups, Email Support, Premium Plugins
  - 24/7 Support, Redis Cache, Staging Environment
  - SLA Uptime, Advanced Security, White Label
- **Stripe Integration**: View linked Stripe product/price IDs (read-only)
- **Active/Inactive Status**: Toggle plan visibility

**Note:** Price changes do not affect existing subscriptions until renewal. Use the Stripe sync API to update Stripe prices after changes.

## CMS - Site Pages Management

The admin panel includes a full Content Management System for managing customer-facing marketing pages.

### Access

Navigate to `https://yourdomain.com/admin/pages` or click "Site Pages" in the admin sidebar under "Content".

### Features

- **Structured Markdown Editor**: Single full-page editor with Markdown + preview for all sections
- **Section Preservation**: Section headers keep existing page layouts intact
- **Page Types**: Homepage, Pricing, Features, About, Contact
- **Draft/Publish Workflow**: Save as draft or publish immediately
- **Version History**: Every change is tracked with ability to rollback
- **Preview Mode**: Preview pages in modal before publishing

### Editor Format

The editor uses section headers to map Markdown back into the structured JSON used by the layout renderers.

```
## section: hero.headline
Launch on managed hosting.

## section: hero.subheadline
Scale with zero ops overhead.

## section: stats (json)
```json
{
  "stores_count": "120+",
  "uptime": "99.99%",
  "hours_saved": "9000+"
}
```

Notes:
- Use `## section: <section>.<field>` for structured text fields.
- Use `## section: <section> (json)` plus a fenced JSON block for structured objects.
- Keep all sections you want to preserve in the page layout.

### Managing Pages

1. Click "Edit" on any page to open the editor
2. Update the section Markdown and keep the section headers intact
3. Use the preview button to validate changes before saving
4. Add a change summary (optional) to track what changed
5. Save as Draft or Publish
6. Use "Version History" to see all changes and rollback if needed

### Rollback

To rollback to a previous version:
1. Go to the page's Version History
2. Find the version you want to restore
3. Click "Rollback" and confirm

## Stripe Pricing Sync

The pricing page integrates with Stripe for two-way pricing synchronization.

### Sync Options

When editing pricing on the pricing page:
- **Create New Prices**: Archives old Stripe prices and creates new ones (use when you want to change pricing structure)
- **Update Existing**: Updates the current Stripe price objects (use for simple price changes)

### API Endpoints

- `GET /admin/api/pricing/sync-options` - Get sync status for all pricing plans
- `POST /admin/api/pricing/sync/<plan_id>` - Sync a plan to Stripe

## Infrastructure Improvements

Recent operational enhancements to improve reliability and isolation.

### Per-Customer Automated Backups

Every customer now gets automatic scheduled backups to the remote restic repository.

**How it works:**
- Step 10 of provisioning sets up a cron job for each customer
- Backups run every 6 hours (configurable)
- 14-day retention policy for customer backups
- Backups include database and all customer files

**Configuration:**
```bash
# Backup script location
/opt/shophosting/scripts/customer-backup.sh

# Backup logs
/var/log/shophosting-customer-backup.log

# Cron schedule (every 6 hours)
0 */6 * * * /opt/shophosting/scripts/customer-backup.sh {customer_id}
```

**Manual backup (customers can do this from their dashboard):**
```bash
/opt/shophosting/scripts/customer-backup.sh 6  # For customer 6
```

### Idempotent Provisioning

The provisioning system now handles retries correctly without crashing on existing resources.

**What was fixed:**
- Directory creation checks for existing directories before creating
- Existing containers are cleaned up before reprovisioning
- Retry button in admin panel now works correctly for failed jobs
- No more "directory already exists" errors on retry

**How it works:**
```python
# In create_customer_directory()
if customer_path.exists():
    logger.info(f"Directory {customer_path} already exists, cleaning up first")
    # Stop existing containers first
    subprocess.run(['docker', 'compose', 'down', '-v', '--remove-orphans'], ...)
    # Then proceed with creation (exist_ok=True)
    customer_path.mkdir(parents=True, exist_ok=True)
```

### Docker Resource Limits

All customer containers now have memory and CPU limits enforced via Docker's deploy.resources.

**Resource limits per service (WooCommerce):**
| Service | Memory | CPU |
|---------|--------|-----|
| db | 512m | 0.5 |
| redis | 256m | 0.25 |
| wordpress | From pricing plan | From pricing plan |
| phpmyadmin | 256m | 0.25 |

**Resource limits per service (Magento):**
| Service | Memory | CPU |
|---------|--------|-----|
| varnish | 512m | 0.5 |
| web | From pricing plan | From pricing plan |
| db | 1g | 1.0 |
| elasticsearch | 1g | 0.5 |
| redis | 256m | 0.25 |

**Configuration:**
Limits are passed from the `pricing_plans` table:
```python
config = {
    'memory_limit': job_data.get('memory_limit', '1g'),
    'cpu_limit': job_data.get('cpu_limit', '1.0')
}
```

## Backup System

ShopHosting.io includes an automated backup system using [restic](https://restic.net/) that backs up all customer data to a remote server daily, plus customer self-service backup management.

### What Gets Backed Up

- `/var/customers/` - All customer sites (MySQL data, WordPress/Magento files, Redis, configs)
- `shophosting_db` - Master database (customer metadata, credentials, billing)
- `/etc/nginx/sites-available/` - Customer reverse proxy configurations
- `/etc/letsencrypt/` - SSL certificates
- `/opt/shophosting/.env` - Application configuration

### Setup

1. **Install restic:**
   ```bash
   sudo apt install restic
   ```

2. **Set up SSH key authentication to backup server:**
   ```bash
   ssh-keygen -t ed25519 -f /root/.ssh/id_ed25519 -N "" -C "shophosting-backup"
   # Copy public key to backup server's authorized_keys
   ```

3. **Create restic password file:**
   ```bash
   openssl rand -base64 32 > /root/.restic-password
   chmod 600 /root/.restic-password
   ```

4. **Initialize restic repository:**
   ```bash
   restic -r sftp:user@backup-server:/path/to/backups --password-file /root/.restic-password init
   ```

5. **Install systemd timer:**
   ```bash
   sudo cp /opt/shophosting/shophosting-backup.service /etc/systemd/system/
   sudo cp /opt/shophosting/shophosting-backup.timer /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now shophosting-backup.timer
   ```

### Managing Backups

```bash
# Run manual backup
/opt/shophosting/scripts/backup.sh

# List available snapshots
restic -r sftp:user@host:/backups snapshots

# Check backup timer status
systemctl status shophosting-backup.timer

# View backup logs
journalctl -u shophosting-backup.service
```

### Customer Self-Service Backups

Customers can manage their own backups through the `/backup` page.

**Features:**
- **Create Backup**: Customers can trigger manual backups of their store
- **View History**: See all available snapshots with dates
- **Restore Options**: Restore from any snapshot with three options:
  - **Database Only**: Restore just the database (products, orders, settings)
  - **Files Only**: Restore just the store files (uploads, themes, plugins)
  - **Database + Files**: Full restore of everything

**Navigation:**
- Customers access backups via "Backups" link in their navigation
- Quick link on dashboard: "Manage Backups"

**How Restore Works:**
1. Customer selects a snapshot from the list
2. Chooses restore type (db/files/all)
3. Confirms the action
4. Store containers are stopped temporarily
5. Selected data is restored from snapshot
6. Containers restart automatically

**Retention:**
- Customer backups are retained for 14 days
- Daily system backups are retained for 30 days

### Restore Commands (Admin)

```bash
# Restore a specific customer
/opt/shophosting/scripts/customer-restore.sh <customer_id> <snapshot_id> db|files|all

# Restore a specific file or directory
restic -r sftp:user@host:/backups restore <snapshot_id> --target / --path /var/customers/customer-X

# Full disaster recovery (use with caution)
/opt/shophosting/scripts/restore.sh restore-all latest
```

### Configuration

Edit `/opt/shophosting/scripts/backup.sh` to customize:
- `RESTIC_REPOSITORY` - Backup destination (sftp://user@host:/path)
- `RETENTION_DAYS` - Number of daily snapshots to keep (default: 30)

**Important:** Keep `/root/.restic-password` safe - without it, backups cannot be restored.

### Application Code Backup

In addition to customer data backups, a separate backup system protects the application code in `/opt/shophosting`.

**What Gets Backed Up:**
- Application source code
- Configuration templates
- Migration scripts
- Static assets

**Schedule:** Daily at 2:30 AM (with up to 5 minutes random delay)

**Setup:**
```bash
# Install systemd units
sudo cp /opt/shophosting/shophosting-dir-backup.service /etc/systemd/system/
sudo cp /opt/shophosting/shophosting-dir-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now shophosting-dir-backup.timer
```

**Management:**
```bash
# Run manual backup
/opt/shophosting/scripts/shophosting-dir-backup.sh

# List application snapshots
restic -r sftp:user@host:/backups snapshots --tag "shophosting-dir"

# View backup logs
cat /var/log/shophosting-dir-backup.log
```

**Retention:** 30 daily snapshots (configurable in `scripts/restic-backup-config.sh`)

## Architecture

```
                    ┌─────────────────┐
                    │  User Browser   │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │  Nginx (:80/443)│
                    └────────┬────────┘
                             │
         ┌───────────────────┼───────────────────┐
         │                   │                   │
         ▼                   ▼                   ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│ Flask App :5000 │ │ Customer Store  │ │ Customer Store  │
│   (Gunicorn)    │ │   :8001-8010    │ │   :8001-8010    │
└────────┬────────┘ └─────────────────┘ └─────────────────┘
         │
    ┌────┴────┐
    │         │
    ▼         ▼
┌───────┐ ┌───────┐
│ MySQL │ │ Redis │
└───────┘ └───────┘
              │
              ▼
      ┌───────────────┐
      │   RQ Worker   │
      │ (Provisioner) │
      └───────────────┘
```

## Directory Structure

```
/opt/shophosting/
├── webapp/                 # Flask web application
│   ├── app.py              # Main routes and application
│   ├── models.py           # Database models
│   ├── email_service.py    # Email sending service
│   ├── stripe_integration/ # Stripe payment integration
│   │   ├── __init__.py
│   │   ├── config.py       # Stripe configuration
│   │   ├── checkout.py     # Checkout sessions
│   │   ├── webhooks.py     # Webhook handlers
│   │   ├── portal.py       # Customer portal
│   │   └── pricing.py      # Stripe pricing sync
│   ├── admin/              # Admin panel blueprint
│   │   ├── __init__.py
│   │   ├── routes.py       # Admin routes (includes admin user management)
│   │   ├── models.py       # Admin user model
│   │   ├── api.py          # Admin API endpoints
│   │   └── templates/      # Admin panel templates
│   │       ├── base_admin.html
│   │       ├── admins.html           # Admin users list
│   │       ├── admin_form.html       # Create/edit admin form
│   │       ├── change_password.html  # Password change form
│   │       ├── pages.html            # CMS page list
│   │       ├── page_edit.html        # CMS page editor
│   │       ├── page_history.html     # CMS version history
│   │       ├── appointments.html     # Consultation appointments list
│   │       ├── appointment_detail.html # Appointment detail view
│   │       ├── pricing_plans.html    # Pricing plans list
│   │       ├── pricing_plan_edit.html # Pricing plan editor
│   │       └── ...
│   └── templates/          # Customer-facing templates
│       ├── base.html
│       ├── dashboard.html
│       ├── backup.html     # Customer backup management
│       └── ...
├── provisioning/           # Background worker
│   └── provisioning_worker.py
├── templates/              # Docker Compose templates
│   ├── woocommerce-compose.yml.j2
│   └── magento-compose.yml.j2
├── docker/                 # Custom Docker images
├── migrations/             # Database migrations
│   ├── 002_add_admin_users.sql
│   ├── 003_add_ticketing_system.sql
│   ├── 005_add_admin_features.sql
│   ├── 006_add_cms_tables.sql      # CMS and page versions
│   └── 007_add_consultations_table.sql  # Consultation appointments
├── scripts/                # Utility scripts
│   ├── backup.sh           # Daily customer data backup script
│   ├── shophosting-dir-backup.sh  # Application code backup script
│   ├── restic-backup-config.sh    # Backup configuration
│   ├── customer-backup.sh  # Customer self-service backup script
│   ├── customer-restore.sh # Customer self-service restore script
│   ├── create_admin.py     # Create admin users
│   └── setup_stripe_products.py
├── logs/                   # Application logs
│   ├── webapp.log          # Application logs
│   └── security.log        # Security audit trail
├── schema.sql              # Database schema
├── shophosting-backup.service   # Customer data backup systemd service
├── shophosting-backup.timer     # Customer data backup systemd timer
├── shophosting-dir-backup.service  # App code backup systemd service
├── shophosting-dir-backup.timer    # App code backup systemd timer
├── SECURITY.md             # Security documentation
├── .github/workflows/      # CI/CD pipeline
│   └── ci.yml              # Lint, test, security scan
└── .env                    # Environment configuration
```

## Documentation

- [Security Policy](SECURITY.md) - Security architecture and incident response
- [Development Guide](DEVELOPMENT_GUIDE.md) - Detailed development instructions
- [System Guide](SYSTEM_GUIDE.md) - System architecture and operations

## License

Copyright (c) 2026. All rights reserved.
