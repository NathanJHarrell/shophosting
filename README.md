# ShopHosting.io

A production-ready, multi-tenant Docker hosting platform that automatically provisions containerized e-commerce stores (WooCommerce and Magento) on-demand with full infrastructure management, billing, monitoring, and disaster recovery.

## Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Operations](#operations)
- [Monitoring & Alerting](#monitoring--alerting)
- [Backup & Disaster Recovery](#backup--disaster-recovery)
- [High Availability](#high-availability)
- [Admin Panel](#admin-panel)
- [Customer Features](#customer-features)
- [API Reference](#api-reference)
- [Troubleshooting](#troubleshooting)
- [Development](#development)

## Features

### Core Platform
- **Automated Provisioning**: Customers sign up via Stripe checkout and receive a fully configured online store within minutes
- **Multi-Platform Support**: WooCommerce (WordPress + MySQL + Redis) and Magento 2 (with Elasticsearch + Varnish caching)
- **Multi-Server Provisioning**: Scale horizontally by adding worker servers with automatic load balancing
- **Resource Isolation**: Each customer gets isolated Docker containers with configurable CPU, memory, disk, and bandwidth limits

### Customer Self-Service
- **Dashboard**: View store credentials, health status, resource usage, and billing information
- **Staging Environments**: Create up to 3 isolated staging copies per customer for testing changes
- **DNS Management**: Cloudflare OAuth integration for managing DNS records directly from the dashboard
- **Two-Factor Authentication**: TOTP-based 2FA with backup recovery codes
- **Backup Management**: View and request restores of automated backups

### Administration
- **Admin Panel**: Full-featured interface for customer management, provisioning monitoring, and system health
- **Role-Based Access**: Super admin, admin, and support roles with appropriate permissions
- **Live Provisioning Logs**: Real-time progress updates during customer provisioning
- **Resource Enforcement**: Automatic suspension when customers exceed disk/bandwidth limits

### Infrastructure
- **Automated Backups**: Daily encrypted backups to remote server using restic with configurable retention
- **SSL/TLS**: Automatic certificate management with Let's Encrypt
- **Database HA**: MySQL source-replica replication with read/write splitting
- **Redis HA**: Redis Sentinel with automatic failover
- **Load Balancing**: Multiple webapp instances with nginx upstream
- **Secrets Management**: HashiCorp Vault integration with environment variable fallback

### Monitoring & Observability
- **Metrics**: Prometheus metrics collection with 30-day retention
- **Dashboards**: Grafana visualization with pre-built dashboards
- **Logging**: Centralized log aggregation with Grafana Loki
- **Alerting**: AlertManager with email and PagerDuty notifications
- **Status Page**: Public status page with incident management

### Billing
- **Stripe Integration**: Checkout, subscriptions, invoicing, and customer portal
- **Webhook Handling**: Automatic provisioning on successful payment
- **Plan Management**: Multiple pricing tiers with resource limits

## Architecture

```
                         ┌─────────────────────────┐
                         │    Load Balancer        │
                         │    (Nginx Upstream)     │
                         └───────────┬─────────────┘
                                     │
              ┌──────────────────────┼──────────────────────┐
              │                      │                      │
       ┌──────▼──────┐        ┌──────▼──────┐        ┌──────▼──────┐
       │   Gunicorn  │        │   Gunicorn  │        │   Customer  │
       │   :5000     │        │   :5001     │        │   Stores    │
       │  (Flask)    │        │  (Flask)    │        │ :8001-8100  │
       └──────┬──────┘        └──────┬──────┘        └─────────────┘
              │                      │
              └──────────┬───────────┘
                         │
    ┌────────────────────┼────────────────────┐
    │                    │                    │
┌───▼────┐         ┌─────▼─────┐        ┌─────▼─────┐
│ MySQL  │◄───────►│   Redis   │        │   Vault   │
│ Master │         │  Sentinel │        │ (Secrets) │
└───┬────┘         └─────┬─────┘        └───────────┘
    │                    │
┌───▼────┐         ┌─────▼─────┐
│ MySQL  │         │   Redis   │
│Replica │         │   Slave   │
└────────┘         └───────────┘

Background Workers:
┌────────────────────────────────────────────────────────┐
│                    Redis Queue (RQ)                    │
│  provisioning │ staging │ backup │ resource │ monitor │
└───────┬───────────┬─────────┬────────┬─────────┬──────┘
        │           │         │        │         │
   ┌────▼────┐ ┌────▼────┐ ┌──▼──┐ ┌───▼───┐ ┌───▼───┐
   │Provision│ │ Staging │ │Back │ │Resour │ │Monitor│
   │ Worker  │ │ Worker  │ │ up  │ │  ce   │ │  ing  │
   └─────────┘ └─────────┘ └─────┘ └───────┘ └───────┘

Monitoring Stack:
┌─────────────────────────────────────────────────────┐
│  Prometheus  │  Grafana  │  Loki  │  AlertManager   │
│    :9090     │   :3000   │ :3100  │     :9093       │
└─────────────────────────────────────────────────────┘
```

### Port Allocation

| Service | Port Range | Description |
|---------|------------|-------------|
| Production Web | 8001-8100 | Customer store frontends |
| Production Admin | 9001-9100 | phpMyAdmin interfaces |
| Staging Web | 10001-10100 | Staging store frontends |
| Staging Admin | 11001-11100 | Staging phpMyAdmin |

## Requirements

### System Requirements
- Ubuntu 22.04 LTS (recommended)
- 4+ CPU cores
- 8+ GB RAM
- 100+ GB SSD storage
- Python 3.10+

### Software Dependencies
- Docker Engine + Docker Compose v2
- MySQL 8.0
- Redis 7
- Nginx
- Certbot (Let's Encrypt)
- restic (backups)

### Optional Components
- HashiCorp Vault (secrets management)
- Grafana + Prometheus + Loki (monitoring)
- fail2ban (intrusion prevention)

## Installation

### 1. Install System Dependencies

```bash
sudo apt update
sudo apt install -y \
    docker.io docker-compose-v2 \
    mysql-server redis-server nginx \
    certbot python3-certbot-nginx \
    python3.10 python3.10-venv python3-pip \
    restic jq curl
```

### 2. Create System User and Directories

```bash
# Create system user
sudo useradd -r -m -s /bin/bash shophosting

# Create directories
sudo mkdir -p /opt/shophosting /var/customers /opt/shophosting/logs
sudo chown -R shophosting:shophosting /opt/shophosting /var/customers

# Add user to docker group
sudo usermod -aG docker shophosting
```

### 3. Clone the Repository

```bash
cd /opt
sudo git clone https://github.com/NathanJHarrell/shophosting.git
sudo chown -R shophosting:shophosting /opt/shophosting
```

### 4. Set Up the Database

```bash
# Create database and user
sudo mysql -u root << 'EOF'
CREATE DATABASE shophosting_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'shophosting_app'@'localhost' IDENTIFIED BY 'your-secure-password';
GRANT ALL PRIVILEGES ON shophosting_db.* TO 'shophosting_app'@'localhost';
FLUSH PRIVILEGES;
EOF

# Initialize schema
sudo mysql -u root shophosting_db < /opt/shophosting/schema.sql

# Run migrations
cd /opt/shophosting/webapp
source venv/bin/activate
python migrate.py
```

### 5. Configure Environment

```bash
cd /opt/shophosting
cp .env.example .env
chmod 600 .env
nano .env  # Edit with your values
```

See [Configuration](#configuration) for all environment variables.

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

### 8. Configure Sudoers

The webapp user needs passwordless sudo for nginx and certbot operations:

```bash
cat << 'EOF' | sudo tee /etc/sudoers.d/shophosting
shophosting ALL=(ALL) NOPASSWD: /usr/sbin/nginx -t
shophosting ALL=(ALL) NOPASSWD: /usr/bin/systemctl reload nginx
shophosting ALL=(ALL) NOPASSWD: /usr/bin/certbot
shophosting ALL=(ALL) NOPASSWD: /usr/bin/rm -rf /var/customers/customer-*
shophosting ALL=(ALL) NOPASSWD: /usr/bin/docker *
EOF
sudo chmod 440 /etc/sudoers.d/shophosting
```

### 9. Install Systemd Services

```bash
# Core services
sudo cp /opt/shophosting/shophosting-webapp.service /etc/systemd/system/
sudo cp /opt/shophosting/provisioning-worker.service /etc/systemd/system/
sudo cp /opt/shophosting/provisioning/resource-worker.service /etc/systemd/system/
sudo cp /opt/shophosting/provisioning/monitoring-worker.service /etc/systemd/system/

# Backup services
sudo cp /opt/shophosting/shophosting-backup.service /etc/systemd/system/
sudo cp /opt/shophosting/shophosting-backup.timer /etc/systemd/system/

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable shophosting-webapp provisioning-worker
sudo systemctl enable shophosting-backup.timer
sudo systemctl start shophosting-webapp provisioning-worker
sudo systemctl start shophosting-backup.timer
```

### 10. Configure Nginx

```bash
# Copy configuration
sudo cp /opt/shophosting/configs/nginx/shophosting-upstream.conf /etc/nginx/conf.d/

# Create site configuration
sudo tee /etc/nginx/sites-available/shophosting << 'EOF'
upstream shophosting_app {
    least_conn;
    server 127.0.0.1:5000;
    keepalive 32;
}

server {
    server_name yourdomain.com www.yourdomain.com;

    location / {
        proxy_pass http://shophosting_app;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Connection "";
    }

    listen 80;
}
EOF

# Enable and get SSL
sudo ln -s /etc/nginx/sites-available/shophosting /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d yourdomain.com -d www.yourdomain.com
```

### 11. Create First Admin User

```bash
cd /opt/shophosting
source webapp/venv/bin/activate
python scripts/create_admin.py admin@example.com "SecurePassword123!" "Admin Name" super_admin
```

## Configuration

### Environment Variables (.env)

#### Required

| Variable | Description | Example |
|----------|-------------|---------|
| `SECRET_KEY` | Flask secret key (32+ chars) | `openssl rand -hex 32` |
| `DB_PASSWORD` | MySQL password | `your-db-password` |
| `BASE_DOMAIN` | Your domain name | `shophosting.io` |

#### Database

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_HOST` | `localhost` | MySQL host |
| `DB_USER` | `shophosting_app` | MySQL user |
| `DB_NAME` | `shophosting_db` | Database name |
| `DB_POOL_SIZE` | `5` | Connection pool size |
| `DB_REPLICA_HOST` | - | Read replica host (optional) |
| `DB_REPLICA_USER` | - | Replica user (optional) |
| `DB_REPLICA_PASSWORD` | - | Replica password (optional) |

#### Redis

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | `redis://localhost:6379/1` | Redis connection URL |
| `REDIS_SENTINEL_HOSTS` | - | Sentinel hosts (e.g., `localhost:26379,localhost:26380`) |
| `REDIS_SENTINEL_MASTER` | `mymaster` | Sentinel master name |
| `REDIS_PASSWORD` | - | Redis password |

#### Vault (Optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `VAULT_ADDR` | `http://127.0.0.1:8200` | Vault server address |
| `VAULT_ROLE_ID` | - | AppRole role ID |
| `VAULT_SECRET_ID` | - | AppRole secret ID |

#### Stripe

| Variable | Description |
|----------|-------------|
| `STRIPE_SECRET_KEY` | Stripe API secret key |
| `STRIPE_PUBLISHABLE_KEY` | Stripe publishable key |
| `STRIPE_WEBHOOK_SECRET` | Webhook signing secret |

#### Cloudflare (Optional)

| Variable | Description |
|----------|-------------|
| `CLOUDFLARE_CLIENT_ID` | OAuth client ID |
| `CLOUDFLARE_CLIENT_SECRET` | OAuth client secret |
| `CLOUDFLARE_REDIRECT_URI` | OAuth callback URL |

#### Email

| Variable | Default | Description |
|----------|---------|-------------|
| `EMAILS_ENABLED` | `false` | Enable email sending |
| `SMTP_HOST` | - | SMTP server |
| `SMTP_PORT` | `587` | SMTP port |
| `SMTP_USERNAME` | - | SMTP username |
| `SMTP_PASSWORD` | - | SMTP password |
| `FROM_EMAIL` | - | Sender email address |

#### Customers

| Variable | Default | Description |
|----------|---------|-------------|
| `CUSTOMERS_BASE_PATH` | `/var/customers` | Customer data directory |
| `PORT_RANGE_START` | `8001` | First customer port |
| `PORT_RANGE_END` | `8100` | Last customer port |
| `DEFAULT_MEMORY_LIMIT` | `1g` | Default container memory |
| `DEFAULT_CPU_LIMIT` | `1.0` | Default container CPU |

#### Backup

| Variable | Description |
|----------|-------------|
| `RESTIC_REPOSITORY` | Backup destination (`sftp:user@host:/path`) |
| `RESTIC_PASSWORD_FILE` | Path to restic password file |

## Operations

### Service Management

```bash
# Start all services
sudo systemctl start shophosting-webapp provisioning-worker

# Stop all services
sudo systemctl stop shophosting-webapp provisioning-worker

# Restart with zero downtime (if load balanced)
/opt/shophosting/scripts/rolling-restart.sh

# Check status
sudo systemctl status shophosting-webapp
sudo systemctl status provisioning-worker
```

### Viewing Logs

```bash
# Webapp logs
tail -f /opt/shophosting/logs/webapp.log

# Provisioning worker logs
tail -f /opt/shophosting/logs/provisioning_worker.log

# Systemd journal
journalctl -u shophosting-webapp -f
journalctl -u provisioning-worker -f
```

### Database Migrations

```bash
cd /opt/shophosting/webapp
source venv/bin/activate

# Check migration status
python migrate.py --status

# Run pending migrations
python migrate.py

# Dry run (show what would run)
python migrate.py --dry-run
```

### Customer Container Management

```bash
# List running customer containers
docker ps --filter "name=customer-"

# View container logs
docker logs customer-{id}-web

# Restart a customer's stack
cd /var/customers/customer-{id}
docker compose restart

# Stop a customer's containers
docker compose down

# Start a customer's containers
docker compose up -d
```

### Queue Management

```bash
# Check queue status
redis-cli LLEN rq:queue:provisioning

# View failed jobs
redis-cli LRANGE rq:failed 0 -1

# Clear failed jobs (from admin panel or)
redis-cli DEL rq:failed
```

## Monitoring & Alerting

### Deploy Monitoring Stack

```bash
cd /opt/shophosting/monitoring
cp .env.example .env
nano .env  # Set GRAFANA_ADMIN_PASSWORD

docker compose up -d
```

### Access Dashboards

| Service | URL | Default Credentials |
|---------|-----|---------------------|
| Grafana | `https://yourdomain.com/grafana` | admin / (from .env) |
| Prometheus | `http://localhost:9090` | - |
| AlertManager | `http://localhost:9093` | - |

### Configure Alerting

Edit `monitoring/.env`:

```bash
# Email alerts
SMTP_HOST=smtp.gmail.com:587
SMTP_FROM=alerts@yourdomain.com
SMTP_USERNAME=your-email
SMTP_PASSWORD=your-app-password
ALERT_EMAIL=ops@yourdomain.com

# PagerDuty alerts
PAGERDUTY_ROUTING_KEY=your-routing-key
```

### Alert Rules

Pre-configured alerts in `monitoring/prometheus/alerts.yml`:

| Alert | Severity | Description |
|-------|----------|-------------|
| CustomerContainerDown | critical | Container down > 5 min |
| CustomerHighMemory | warning | Memory > 90% for 10 min |
| CustomerHighDiskUsage | warning | Disk quota > 90% |
| WebappDown | critical | Webapp unreachable > 2 min |
| MySQLDown | critical | Database unreachable |
| MySQLReplicationLag | warning | Replica > 60s behind |
| BackupFailed | critical | No backup in 24 hours |

### Centralized Logging

Logs are aggregated in Loki and queryable via Grafana:

```logql
# All webapp errors
{job="shophosting-webapp"} |= "ERROR"

# Customer provisioning logs
{job="shophosting-workers"} |~ "customer-\\d+"

# Nginx 5xx errors
{job="nginx"} | json | status >= 500
```

## Backup & Disaster Recovery

### Automated Backups

Backups run daily via systemd timer and include:
- All customer data (`/var/customers/`)
- Master database dump
- SSL certificates
- Application configuration

```bash
# Check backup timer status
systemctl status shophosting-backup.timer

# Run manual backup
/opt/shophosting/scripts/backup.sh

# List snapshots
/opt/shophosting/scripts/restore.sh list

# View backup logs
journalctl -u shophosting-backup.service
```

### Restore Procedures

```bash
# Restore specific customer
/opt/shophosting/scripts/restore.sh restore-customer 12 latest

# Restore database
/opt/shophosting/scripts/restore.sh restore-db shophosting_db latest

# Restore specific file/directory
/opt/shophosting/scripts/restore.sh restore-file /var/customers/customer-12 latest

# Full disaster recovery
/opt/shophosting/scripts/restore.sh restore-all latest
```

### Disaster Recovery Testing

Run monthly DR verification:

```bash
/opt/shophosting/scripts/dr-test.sh
```

This validates:
- Repository integrity
- Snapshot availability
- Restore functionality
- Database recoverability

See `docs/runbooks/disaster-recovery.md` for detailed procedures.

## High Availability

### MySQL Replication

Set up source-replica replication for read scaling and failover:

```bash
# On primary server
sudo /opt/shophosting/scripts/setup-mysql-replication.sh primary

# On replica server
sudo /opt/shophosting/scripts/setup-mysql-replication.sh replica primary-host repl-password
```

Configure the app to use the replica:

```bash
# In .env
DB_REPLICA_HOST=replica-host
DB_REPLICA_USER=shophosting_read
DB_REPLICA_PASSWORD=replica-password
```

### Redis Sentinel

Deploy Redis HA with automatic failover:

```bash
cd /opt/shophosting/redis
./setup-sentinel.sh
```

Configure the app:

```bash
# In .env
REDIS_SENTINEL_HOSTS=localhost:26379,localhost:26380,localhost:26381
REDIS_SENTINEL_MASTER=mymaster
REDIS_PASSWORD=your-redis-password
```

### Webapp Load Balancing

Run multiple webapp instances:

```bash
# Set up 2 instances
sudo /opt/shophosting/scripts/setup-load-balancing.sh 2

# Rolling restart for zero downtime
/opt/shophosting/scripts/rolling-restart.sh
```

### Secrets Management (Vault)

Deploy HashiCorp Vault for secure secrets storage:

```bash
# Start Vault
cd /opt/shophosting/vault
docker compose up -d

# Initialize and configure
/opt/shophosting/scripts/vault-init.sh

# Add secrets
export VAULT_TOKEN=your-root-token
vault kv put secret/shophosting/database password=db-password
vault kv put secret/shophosting/stripe secret_key=sk_live_xxx
```

## Admin Panel

Access at `https://yourdomain.com/admin/login`

### Features

| Feature | Description |
|---------|-------------|
| Dashboard | Customer stats, queue status, system health |
| Customers | Create, edit, suspend, delete customers |
| Provisioning | Job monitoring, retry failed jobs, live logs |
| Admin Users | Manage admin accounts (super_admin only) |
| Staging | View all staging environments |
| Status Page | Incident and maintenance management |
| Billing | MRR, subscription overview, invoices |
| Logs | View webapp and worker logs |

### Admin Roles

| Role | Permissions |
|------|-------------|
| `super_admin` | Full access including admin user management |
| `admin` | Customer and system management |
| `support` | View-only access for support staff |

## Customer Features

### Dashboard

Customers can access their dashboard at `https://yourdomain.com/dashboard`:

- View store URL and admin credentials
- Monitor health status and resource usage
- Manage staging environments
- Configure DNS via Cloudflare
- View billing and invoices
- Enable two-factor authentication
- Request backups and restores

### Staging Environments

Customers can create up to 3 staging sites:

1. Click "Create Staging Environment"
2. Enter a name for the staging site
3. Wait for cloning to complete
4. Test changes on staging
5. Push files/database to production when ready

Staging URLs: `cust{id}-staging-{n}.yourdomain.com`

### DNS Management

Customers with Cloudflare can:

1. Connect their Cloudflare account via OAuth
2. Review and approve automatic DNS configuration
3. Add/edit/delete DNS records from dashboard
4. MX and TXT records are preserved

## API Reference

### Health Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/health` | Application health check |
| `GET /api/status` | Detailed status with DB/Redis |
| `GET /health` | Simple liveness probe |
| `GET /ready` | Readiness probe |

### Metrics

| Endpoint | Description |
|----------|-------------|
| `GET /metrics` | Prometheus metrics |
| `GET /metrics/containers` | Container metrics |

### Webhooks

| Endpoint | Description |
|----------|-------------|
| `POST /webhook/stripe` | Stripe webhook receiver |

## Troubleshooting

### Common Issues

#### Provisioning Fails

```bash
# Check worker logs
tail -100 /opt/shophosting/logs/provisioning_worker.log

# Check Docker
docker ps -a --filter "name=customer-"

# Verify ports
ss -tlnp | grep 800

# Retry from admin panel or clear failed job
```

#### Database Connection Issues

```bash
# Test connection
mysql -u shophosting_app -p -h localhost shophosting_db

# Check pool exhaustion
grep "pool" /opt/shophosting/logs/webapp.log

# Restart to reset connections
sudo systemctl restart shophosting-webapp
```

#### Redis Connection Issues

```bash
# Test connection
redis-cli ping

# Check Sentinel (if using)
redis-cli -p 26379 SENTINEL master mymaster

# View Redis logs
docker logs redis-master
```

#### SSL Certificate Issues

```bash
# Check certificate status
sudo certbot certificates

# Renew manually
sudo certbot renew

# Check nginx config
sudo nginx -t
```

### Log Locations

| Log | Location |
|-----|----------|
| Webapp | `/opt/shophosting/logs/webapp.log` |
| Provisioning | `/opt/shophosting/logs/provisioning_worker.log` |
| Resource Worker | `/opt/shophosting/logs/resource_worker.log` |
| Nginx | `/var/log/nginx/` |
| MySQL | `/var/log/mysql/` |
| Backups | `/var/log/shophosting-backup.log` |

## Development

### Running Tests

```bash
cd /opt/shophosting/webapp
source venv/bin/activate

# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ -v --cov=. --cov-report=term-missing

# Run specific test file
pytest tests/test_auth.py -v
```

### Code Quality

```bash
# Linting
flake8 webapp/ --max-line-length=120

# Security scan
bandit -r webapp/ -ll

# Dependency audit
pip-audit
```

### Project Structure

```
/opt/shophosting/
├── webapp/                    # Flask web application
│   ├── app.py                 # Main routes (3,300+ lines)
│   ├── models.py              # Database models (4,300+ lines)
│   ├── secrets.py             # Vault integration
│   ├── migrate.py             # Migration runner
│   ├── admin/                 # Admin panel blueprint
│   ├── cloudflare/            # DNS management
│   ├── stripe_integration/    # Billing integration
│   ├── status/                # Status page
│   └── tests/                 # Test suite
├── provisioning/              # Background workers
│   ├── provisioning_worker.py # Main provisioning
│   ├── staging_worker.py      # Staging operations
│   ├── resource_worker.py     # Resource monitoring
│   └── backup_worker.py       # Backup jobs
├── templates/                 # Docker Compose templates
├── monitoring/                # Prometheus/Grafana/Loki stack
├── redis/                     # Redis Sentinel configuration
├── vault/                     # HashiCorp Vault configuration
├── configs/                   # System configurations
│   ├── mysql/                 # Replication configs
│   ├── nginx/                 # Load balancer configs
│   └── logrotate/             # Log rotation
├── migrations/                # Database migrations (17 files)
├── scripts/                   # Operational scripts
│   ├── backup.sh              # Backup script
│   ├── restore.sh             # Restore tool
│   ├── vault-init.sh          # Vault setup
│   ├── setup-load-balancing.sh
│   ├── setup-mysql-replication.sh
│   ├── rolling-restart.sh
│   └── dr-test.sh             # DR verification
├── docs/                      # Documentation
│   └── runbooks/              # Operational runbooks
└── docker/                    # Custom Docker images
```

## License

Copyright (c) 2026 ShopHosting.io. All rights reserved.

## Support

- GitHub Issues: https://github.com/NathanJHarrell/shophosting/issues
- Documentation: https://docs.shophosting.io
- Status Page: https://status.shophosting.io
