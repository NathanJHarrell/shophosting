# ShopHosting.io

A multi-tenant Docker hosting platform that automatically provisions containerized e-commerce stores (WooCommerce and Magento) on-demand.

## Features

- **Automated Provisioning**: Customers sign up and receive a fully configured online store within minutes
- **Multi-Platform Support**: WooCommerce (WordPress) and Magento 2 with Varnish caching
- **Self-Service Dashboard**: Customers can view store status, credentials, and manage their stores
- **Staging Environments**: Customers can create isolated staging copies of their production sites to test changes before deploying
- **Admin Panel**: Full-featured admin interface for system monitoring and customer management
  - **Admin User Management**: Super admins can create, edit, and manage other admin users with role-based access control
  - **Live Provisioning Logs**: Real-time persistent logs showing detailed provisioning progress on customer pages
- **Background Job Processing**: Redis-backed queue system for reliable provisioning
- **Multi-Server Provisioning**: Scale horizontally by adding worker servers with automatic load balancing
- **SSL/TLS Support**: Automatic certificate management with Let's Encrypt
- **Resource Isolation**: Each customer gets isolated Docker containers with configurable resource limits
- **Automated Backups**: Daily encrypted backups to remote server using restic with 30-day retention
- **DNS Management**: Customers connect their Cloudflare account to manage DNS records directly from the dashboard

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

### Quick Actions

The dashboard includes quick action buttons for:
- Restart webapp/worker services
- Run manual backup
- Clear failed provisioning jobs
- View logs
- External links to Portainer and Stripe Dashboard

### Admin Roles

- `super_admin`: Full access to all features including admin user management
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

## Staging Environments

ShopHosting.io allows customers to create isolated staging copies of their production sites, similar to WP Engine's staging feature. This enables safe testing of changes before deploying to production.

### Features

- **Isolated Environments**: Each staging site runs in separate Docker containers with its own database
- **Production Cloning**: Staging environments are created by cloning the production site's files and database
- **Selective Push to Production**: Push files only, database only, or both back to production
- **Automatic SSL**: Staging domains automatically receive Let's Encrypt SSL certificates
- **Up to 3 Staging Sites**: Each customer can have up to 3 staging environments

### Setup

1. **Run the staging migration:**
   ```bash
   mysql -u root -p shophosting_db < /opt/shophosting/migrations/006_add_staging_environments.sql
   ```

2. **Configure wildcard DNS:**
   Add a wildcard A record for staging subdomains:
   - **Record**: `*.shophosting.io` (or your domain)
   - **Type**: A
   - **Value**: Your server IP

3. **Ensure the worker is running:**
   The provisioning worker handles both production provisioning and staging operations:
   ```bash
   sudo systemctl status provisioning-worker
   ```

### Staging Domain Format

Staging environments use the subdomain format: `cust{customer_id}-staging-{n}.yourdomain.com`

Example: `cust9-staging-1.shophosting.io`

### Port Allocation

- **Production sites**: Ports 8001-8100 (web), 9001-9100 (phpMyAdmin)
- **Staging sites**: Ports 10001-10100 (web), 11001-11100 (phpMyAdmin)

### Customer Usage

Customers can manage staging environments from their dashboard:

1. **Create Staging**: Click "Create Staging Environment" and enter a name
2. **Access Staging**: Click the staging URL to view the staging site
3. **Make Changes**: Edit the staging site freely without affecting production
4. **Push to Production**: Choose to push files, database, or both
5. **Delete Staging**: Remove staging environments when no longer needed

### Admin Management

Admins can view and manage all staging environments across customers:
- Navigate to Admin Panel → Staging Environments
- View staging stats (total, active, creating, failed)
- Monitor port usage
- Delete staging environments if needed

### Technical Details

| Component | Location |
|-----------|----------|
| Database migration | `migrations/006_add_staging_environments.sql` |
| Staging models | `webapp/models.py` (StagingEnvironment, StagingPortManager) |
| Staging worker | `provisioning/staging_worker.py` |
| Docker templates | `templates/woocommerce-staging-compose.yml.j2`, `templates/magento-staging-compose.yml.j2` |
| Customer routes | `webapp/app.py` (/staging/*) |
| Admin routes | `webapp/admin/routes.py` (/admin/staging/*) |

## Backup System

ShopHosting.io includes an automated backup system using [restic](https://restic.net/) that backs up all customer data to a remote server daily.

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
/opt/shophosting/scripts/restore.sh list

# Show snapshot contents
/opt/shophosting/scripts/restore.sh show latest

# Check backup timer status
systemctl status shophosting-backup.timer

# View backup logs
journalctl -u shophosting-backup.service
```

### Restoring Data

```bash
# Restore a specific customer
/opt/shophosting/scripts/restore.sh restore-customer 12 latest

# Restore a specific file or directory
/opt/shophosting/scripts/restore.sh restore-file /var/customers/customer-12/wordpress latest

# Restore a database from SQL dump
/opt/shophosting/scripts/restore.sh restore-db shophosting_db latest

# Full disaster recovery (use with caution)
/opt/shophosting/scripts/restore.sh restore-all latest
```

### Configuration

Edit `/opt/shophosting/scripts/backup.sh` to customize:
- `RESTIC_REPOSITORY` - Backup destination (sftp://user@host:/path)
- `RETENTION_DAYS` - Number of daily snapshots to keep (default: 30)

**Important:** Keep `/root/.restic-password` safe - without it, backups cannot be restored.

### Backrest Web UI (Recommended)

[Backrest](https://github.com/garethgeorge/backrest) provides a web-based interface for managing and monitoring restic backups. It offers scheduling, retention policies, and easy restore capabilities through a user-friendly dashboard.

#### Installing Backrest

1. **Download and install Backrest:**
   ```bash
   # Download latest release (check https://github.com/garethgeorge/backrest/releases)
   curl -L https://github.com/garethgeorge/backrest/releases/download/v1.11.1/backrest_Linux_x86_64.tar.gz | tar xz
   sudo mv backrest /usr/local/bin/
   sudo chmod +x /usr/local/bin/backrest
   ```

2. **Create configuration directory:**
   ```bash
   sudo mkdir -p /etc/backrest /var/lib/backrest
   ```

3. **Create initial configuration** (`/etc/backrest/config.json`):
   ```json
   {
     "modno": 1,
     "version": 4,
     "instance": "shophosting",
     "repos": [
       {
         "id": "shophosting-backup",
         "uri": "sftp:user@backup-server:/path/to/backups",
         "password": "your-base64-encoded-password",
         "prunePolicy": {
           "maxUnusedPercent": 25
         },
         "checkPolicy": {
           "schedule": {
             "cron": "0 0 * * 0"
           },
           "readDataSubsetPercent": 10
         }
       }
     ],
     "plans": [
       {
         "id": "daily-backup",
         "repo": "shophosting-backup",
         "paths": [
           "/etc/letsencrypt",
           "/etc/nginx/sites-available",
           "/opt/shophosting/.env",
           "/var/customers"
         ],
         "schedule": {
           "cron": "0 2 * * *"
         },
         "retention": {
           "policyKeepLastN": 7
         }
       }
     ],
     "auth": {
       "disabled": true
     }
   }
   ```

4. **Install systemd service** (`/etc/systemd/system/backrest.service`):
   ```ini
   [Unit]
   Description=Backrest Restic Web UI
   After=network.target

   [Service]
   Type=simple
   ExecStart=/usr/local/bin/backrest -config-file /etc/backrest/config.json -data-dir /var/lib/backrest -bind-address 127.0.0.1:9898
   Restart=always
   RestartSec=10

   [Install]
   WantedBy=multi-user.target
   ```

5. **Enable and start Backrest:**
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now backrest
   ```

6. **Configure Nginx reverse proxy** (add to your nginx config):
   ```nginx
   location /backrest/ {
       proxy_pass http://127.0.0.1:9898/;
       proxy_set_header Host $host;
       proxy_set_header X-Real-IP $remote_addr;
       proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
       proxy_set_header X-Forwarded-Proto $scheme;
   }
   ```

#### Using Backrest UI

Access the Backrest dashboard at `https://yourdomain.com/backrest/` (or directly at `http://localhost:9898` on the server).

**Dashboard Overview:**
- **Repos** - View repository status, storage usage, and run integrity checks
- **Plans** - See backup schedules, trigger manual backups, view backup history
- **Snapshots** - Browse all snapshots, view contents, restore files

**Common Tasks:**

| Task | How to do it |
|------|--------------|
| Run manual backup | Plans → Click "Backup Now" on your plan |
| View backup history | Plans → Click plan name → See operation history |
| Browse snapshot contents | Snapshots → Click snapshot ID → Browse files |
| Restore files | Snapshots → Select snapshot → Browse → Click "Restore" |
| Check repository health | Repos → Click "Check" button |
| View backup logs | Click any operation → View detailed logs |

**Retention Policies:**

Configure retention in your plan's `retention` section:
- `policyKeepLastN: 7` - Keep last 7 snapshots
- `policyKeepDaily: 7` - Keep 7 daily snapshots
- `policyKeepWeekly: 4` - Keep 4 weekly snapshots
- `policyKeepMonthly: 12` - Keep 12 monthly snapshots

#### Migrating from Systemd Timers to Backrest

If you were using the systemd backup timers, disable them to avoid duplicate backups:

```bash
sudo systemctl disable --now shophosting-backup.timer shophosting-dir-backup.timer
```

Backrest will now manage all backup scheduling and retention.

## Status Page

Public status page available at `status.shophosting.io` showing real-time system health.

### Features

- **Per-server status** - Individual status for each web server and backup server
- **Service monitoring** - API and dashboard health checks
- **Incident management** - Create, update, and resolve incidents from admin panel
- **Scheduled maintenance** - Announce planned maintenance windows
- **Auto-detection** - Automatic incident creation when servers go unhealthy
- **Manual overrides** - Override status for any service from admin panel

### Monitored Systems

| System | Check Method |
|--------|--------------|
| Web Servers | Heartbeat + HTTP health check |
| Backup Server (15.204.249.219) | TCP port 22 |
| API | HTTP GET /api/health |
| Customer Dashboard | HTTP GET /dashboard |

### Admin Management

Access status management at `/admin/status` to:
- Create and update incidents
- Schedule maintenance windows
- Set manual status overrides

### Nginx Setup

Copy `webapp/nginx-status.conf` to `/etc/nginx/sites-available/status.shophosting.io` and enable:

```bash
sudo cp webapp/nginx-status.conf /etc/nginx/sites-available/status.shophosting.io
sudo ln -s /etc/nginx/sites-available/status.shophosting.io /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

### DNS Setup

Add DNS record:
```
status.shophosting.io  A  147.135.8.170
```

## DNS Management

Customers can connect their Cloudflare account to manage DNS records directly from their ShopHosting dashboard.

### Features

- **Cloudflare OAuth Integration**: Customers connect their own Cloudflare account via secure OAuth flow
- **Automatic DNS Configuration**: On connection, automatically configures A and CNAME records to point to the customer's store
- **Confirmation Flow**: If existing DNS records are found, customers review and confirm changes before they're applied
- **Full DNS Management**: Add, edit, and delete A, CNAME, MX, and TXT records from the dashboard
- **Record Preservation**: MX and TXT records (email, SPF, DKIM) are preserved during auto-configuration
- **Local Caching**: DNS records are cached locally for fast display

### Setup

1. **Run the migration:**
   ```bash
   mysql -u root -p shophosting_db < /opt/shophosting/migrations/014_add_cloudflare_tables.sql
   ```

2. **Register a Cloudflare OAuth application:**
   - Go to [Cloudflare Dashboard](https://dash.cloudflare.com) → Manage Account → API Tokens → OAuth section
   - Create an OAuth application with redirect URI: `https://yourdomain.com/dashboard/cloudflare/callback`
   - Request scopes: `zone:read`, `dns:read`, `dns:edit`

3. **Add credentials to `.env`:**
   ```bash
   CLOUDFLARE_CLIENT_ID=your_client_id
   CLOUDFLARE_CLIENT_SECRET=your_client_secret
   CLOUDFLARE_REDIRECT_URI=https://yourdomain.com/dashboard/cloudflare/callback
   ```

4. **Restart the webapp:**
   ```bash
   sudo systemctl restart shophosting-webapp
   ```

### Customer Usage

1. Navigate to Dashboard → Domains
2. Click "Connect Cloudflare" (includes link to Cloudflare signup for new users)
3. Authorize ShopHosting to manage DNS
4. Review proposed DNS changes (if existing records found)
5. Confirm to apply changes
6. Manage DNS records directly from the dashboard

### Technical Details

| Component | Location |
|-----------|----------|
| Database migration | `migrations/014_add_cloudflare_tables.sql` |
| Models | `webapp/cloudflare/models.py` |
| API wrapper | `webapp/cloudflare/api.py` |
| Routes | `webapp/cloudflare/routes.py` |
| Templates | `webapp/templates/dashboard/domains.html`, `cloudflare_confirm.html` |

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
│   ├── admin/              # Admin panel blueprint
│   │   ├── __init__.py
│   │   ├── routes.py       # Admin routes (includes admin user management)
│   │   ├── models.py       # Admin user model
│   │   ├── api.py          # Admin API endpoints
│   │   └── templates/      # Admin panel templates
│   ├── cloudflare/         # Cloudflare DNS management
│   │   ├── __init__.py
│   │   ├── api.py          # Cloudflare API v4 wrapper
│   │   ├── models.py       # Connection and DNS cache models
│   │   └── routes.py       # OAuth and DNS CRUD routes
│   │       ├── base_admin.html
│   │       ├── admins.html           # Admin users list
│   │       ├── admin_form.html       # Create/edit admin form
│   │       ├── change_password.html  # Password change form
│   │       └── ...
│   └── templates/          # Customer-facing templates
├── provisioning/           # Background worker
│   ├── provisioning_worker.py
│   └── staging_worker.py   # Staging environment operations
├── templates/              # Docker Compose templates
│   ├── woocommerce-compose.yml.j2
│   ├── magento-compose.yml.j2
│   ├── woocommerce-staging-compose.yml.j2
│   └── magento-staging-compose.yml.j2
├── docker/                 # Custom Docker images
├── migrations/             # Database migrations
│   ├── 002_add_admin_users.sql
│   ├── 003_add_ticketing_system.sql
│   ├── 005_add_admin_features.sql   # Admin user management features
│   ├── 006_add_staging_environments.sql  # Staging environments
│   └── 013_add_status_page_tables.sql    # Status page incidents and maintenance
├── scripts/                # Utility scripts
│   ├── backup.sh           # Daily backup script
│   ├── restore.sh          # Restore tool
│   ├── create_admin.py     # Create admin users
│   └── setup_stripe_products.py
├── logs/                   # Application logs
├── schema.sql              # Database schema
├── shophosting-backup.service   # Backup systemd service
├── shophosting-backup.timer     # Backup systemd timer
└── .env                    # Environment configuration
```

## Documentation

- [Development Guide](DEVELOPMENT_GUIDE.md) - Detailed development instructions
- [System Guide](SYSTEM_GUIDE.md) - System architecture and operations
- [Worker Deployment](WORKER_DEPLOYMENT.md) - Deploying provisioning workers on additional servers

## License

Copyright (c) 2026. All rights reserved.
