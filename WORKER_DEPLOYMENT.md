# Worker Server Deployment Guide

This guide explains how to deploy a provisioning worker on a secondary server without the full webapp.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                   Main Server (Control Plane)                │
│  ┌──────────┐  ┌──────────┐  ┌───────────────────────────┐  │
│  │  Web App │  │  MySQL   │  │  Redis (job queues)       │  │
│  │  Admin   │  │          │  │  - provisioning:server-1  │  │
│  │  Stripe  │  │          │  │  - provisioning:server-2  │  │
│  └──────────┘  └──────────┘  └───────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                              │
           ┌──────────────────┼──────────────────┐
           ▼                  ▼                  ▼
    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
    │  Server 1   │    │  Server 2   │    │  Server N   │
    │  (Main)     │    │  (Worker)   │    │  (Worker)   │
    │  Worker     │    │  Worker     │    │  Worker     │
    │  Docker     │    │  Docker     │    │  Docker     │
    │  Nginx      │    │  Nginx      │    │  Nginx      │
    └─────────────┘    └─────────────┘    └─────────────┘
```

## Prerequisites

- Ubuntu 20.04+ or Debian 11+
- Root or sudo access
- Network connectivity to main server (MySQL and Redis ports)

## Step 1: Register the Server

Before deploying, register the new server in the database.

**Option A: Via Admin Panel**

Navigate to `/admin/servers/create` and fill in:
- Name: `Worker 2`
- Hostname: `worker2.yourdomain.com`
- IP Address: `192.168.1.101`
- Max Customers: `50`
- Port Range: `8001-8100`

**Option B: Via SQL**

```sql
INSERT INTO servers (name, hostname, ip_address, status, max_customers, port_range_start, port_range_end)
VALUES ('Worker 2', 'worker2.yourdomain.com', '192.168.1.101', 'active', 50, 8001, 8100);

-- Note the returned ID (e.g., 2)
SELECT LAST_INSERT_ID();
```

## Step 2: Install System Dependencies

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install required packages
sudo apt install -y \
    python3 \
    python3-pip \
    python3-venv \
    docker.io \
    docker-compose \
    nginx \
    certbot \
    python3-certbot-nginx \
    git

# Add current user to docker group
sudo usermod -aG docker $USER

# Start and enable Docker
sudo systemctl enable docker
sudo systemctl start docker

# You may need to log out and back in for docker group to take effect
```

## Step 3: Create Directory Structure

```bash
# Create directories
sudo mkdir -p /opt/shophosting/{provisioning,templates,logs,scripts}
sudo mkdir -p /var/customers

# Set ownership (adjust user as needed)
sudo chown -R $USER:$USER /opt/shophosting
sudo chown -R $USER:$USER /var/customers
```

## Step 4: Copy Provisioning Code

Copy only the necessary files from the main server:

```bash
MAIN_SERVER="user@main-server-ip"

# Copy provisioning scripts
scp $MAIN_SERVER:/opt/shophosting/provisioning/provisioning_worker.py /opt/shophosting/provisioning/
scp $MAIN_SERVER:/opt/shophosting/provisioning/enqueue_provisioning.py /opt/shophosting/provisioning/

# Copy templates
scp -r $MAIN_SERVER:/opt/shophosting/templates/ /opt/shophosting/

# Copy helper scripts (for backups, etc.)
scp $MAIN_SERVER:/opt/shophosting/scripts/customer-backup.sh /opt/shophosting/scripts/
scp $MAIN_SERVER:/opt/shophosting/scripts/customer-restore.sh /opt/shophosting/scripts/
```

### Minimal Files Required

```
/opt/shophosting/
├── provisioning/
│   ├── provisioning_worker.py      # Main worker script
│   └── enqueue_provisioning.py     # Queue utilities
├── templates/
│   ├── woocommerce-compose.yml.j2  # WooCommerce Docker template
│   ├── magento-compose.yml.j2      # Magento Docker template
│   └── nginx-customer.conf.j2      # Nginx config template
├── scripts/
│   ├── customer-backup.sh          # Backup script
│   └── customer-restore.sh         # Restore script
├── logs/                           # Log directory
└── .env                            # Environment config
```

## Step 5: Set Up Python Environment

```bash
cd /opt/shophosting

# Create virtual environment
python3 -m venv venv

# Activate it
source venv/bin/activate

# Install dependencies
pip install \
    redis \
    rq \
    mysql-connector-python \
    jinja2 \
    requests \
    python-dotenv
```

## Step 6: Configure Environment

Create `/opt/shophosting/.env`:

```bash
# Server Identity
SERVER_ID=2  # <-- Use the ID from Step 1

# Central Redis (main server)
REDIS_HOST=192.168.1.100
REDIS_PORT=6379
# REDIS_PASSWORD=your_redis_password  # If Redis auth is enabled

# Central MySQL (main server)
DB_HOST=192.168.1.100
DB_USER=shophosting_app
DB_PASSWORD=your_database_password
DB_NAME=shophosting_db

# Local Configuration
CUSTOMERS_BASE_PATH=/var/customers

# Resource Limits (optional)
DEFAULT_MEMORY_LIMIT=1g
DEFAULT_CPU_LIMIT=1.0
```

## Step 7: Configure Main Server for Remote Access

### MySQL Remote Access

On the **main server**, allow the worker to connect:

```bash
# Edit MySQL configuration
sudo nano /etc/mysql/mysql.conf.d/mysqld.cnf

# Change bind-address to allow remote connections:
bind-address = 0.0.0.0
```

Grant access to the worker server:

```sql
-- Connect to MySQL as root
mysql -u root -p

-- Grant access (replace IP with worker server IP)
CREATE USER IF NOT EXISTS 'shophosting_app'@'192.168.1.101' IDENTIFIED BY 'your_password';
GRANT ALL PRIVILEGES ON shophosting_db.* TO 'shophosting_app'@'192.168.1.101';
FLUSH PRIVILEGES;
```

Restart MySQL:

```bash
sudo systemctl restart mysql
```

### Redis Remote Access

On the **main server**, configure Redis:

```bash
# Edit Redis configuration
sudo nano /etc/redis/redis.conf

# Change bind address
bind 0.0.0.0

# Optionally set a password (recommended)
requirepass your_redis_password

# Restart Redis
sudo systemctl restart redis
```

### Firewall Rules

Ensure the main server allows connections on:
- Port 3306 (MySQL)
- Port 6379 (Redis)

```bash
# On main server (using ufw)
sudo ufw allow from 192.168.1.101 to any port 3306
sudo ufw allow from 192.168.1.101 to any port 6379
```

## Step 8: Create Systemd Service

Create `/etc/systemd/system/provisioning-worker.service`:

```ini
[Unit]
Description=ShopHosting Provisioning Worker
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=/opt/shophosting
EnvironmentFile=/opt/shophosting/.env
ExecStart=/opt/shophosting/venv/bin/python provisioning/provisioning_worker.py
Restart=always
RestartSec=10
StandardOutput=append:/opt/shophosting/logs/provisioning_worker.log
StandardError=append:/opt/shophosting/logs/provisioning_worker.log

# Security hardening (optional)
NoNewPrivileges=false
ProtectSystem=false
ProtectHome=false

[Install]
WantedBy=multi-user.target
```

Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable provisioning-worker
sudo systemctl start provisioning-worker
```

## Step 9: Configure Nginx

The worker needs nginx to serve as reverse proxy for customer sites.

```bash
# Ensure nginx is running
sudo systemctl enable nginx
sudo systemctl start nginx

# The provisioning worker will automatically create
# /etc/nginx/sites-available/customer-{id}.conf files
```

## Step 10: Configure Sudoers

The worker needs sudo access for certain operations:

```bash
sudo visudo -f /etc/sudoers.d/shophosting
```

Add:

```
# Allow provisioning worker to manage nginx and certbot
your_user ALL=(ALL) NOPASSWD: /usr/sbin/nginx
your_user ALL=(ALL) NOPASSWD: /usr/bin/systemctl reload nginx
your_user ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart nginx
your_user ALL=(ALL) NOPASSWD: /usr/bin/certbot
your_user ALL=(ALL) NOPASSWD: /bin/rm -f /etc/nginx/sites-enabled/*
your_user ALL=(ALL) NOPASSWD: /bin/rm -f /etc/nginx/sites-available/*
your_user ALL=(ALL) NOPASSWD: /bin/ln -s /etc/nginx/sites-available/* /etc/nginx/sites-enabled/*
```

## Step 11: Verify Deployment

### Check Worker Status

```bash
# Check service status
sudo systemctl status provisioning-worker

# View logs
tail -f /opt/shophosting/logs/provisioning_worker.log

# You should see:
# "ShopHosting.io Provisioning worker started"
# "Multi-server mode: Server ID 2"
# "Started heartbeat thread for server 2"
```

### Verify Heartbeat

On the main server, check that the worker is sending heartbeats:

```bash
mysql -u shophosting_app -p shophosting_db -e \
  "SELECT id, name, hostname, status, last_heartbeat,
   TIMESTAMPDIFF(SECOND, last_heartbeat, NOW()) as seconds_ago
   FROM servers;"
```

The `seconds_ago` should be less than 60 for a healthy worker.

### Check Admin Panel

Navigate to `/admin/servers` on the main server. The new worker should show:
- Status: Active
- Health: Healthy (green dot)

## Troubleshooting

### Worker Won't Connect to Redis

```bash
# Test Redis connection from worker
redis-cli -h 192.168.1.100 -p 6379 ping

# If using password:
redis-cli -h 192.168.1.100 -p 6379 -a your_password ping
```

### Worker Won't Connect to MySQL

```bash
# Test MySQL connection from worker
mysql -h 192.168.1.100 -u shophosting_app -p shophosting_db -e "SELECT 1;"
```

### Heartbeat Not Updating

Check that:
1. `SERVER_ID` in `.env` matches the database
2. MySQL credentials are correct
3. Worker has network access to MySQL

### Docker Permission Denied

```bash
# Add user to docker group
sudo usermod -aG docker $USER

# Log out and back in, or:
newgrp docker
```

### Nginx Config Errors

```bash
# Test nginx configuration
sudo nginx -t

# Check nginx error log
sudo tail -f /var/log/nginx/error.log
```

## DNS and Load Balancing

For customer domains to resolve to the correct server, you'll need one of:

1. **DNS per server**: Point customer domains to the specific server IP
2. **Central load balancer**: Use HAProxy/nginx to route based on domain
3. **DNS-based load balancing**: Use a service like Cloudflare with origin rules

This is beyond the scope of this guide but essential for production multi-server setups.

## Security Considerations

1. **Firewall**: Only allow MySQL/Redis from known worker IPs
2. **Redis Auth**: Always use a password in production
3. **TLS**: Consider TLS for MySQL connections (`--ssl-mode=REQUIRED`)
4. **VPN/Private Network**: Ideally, workers communicate over a private network

## Updating Workers

When the provisioning code is updated on the main server:

```bash
# On each worker server
MAIN_SERVER="user@main-server-ip"

# Pull updated files
scp $MAIN_SERVER:/opt/shophosting/provisioning/*.py /opt/shophosting/provisioning/
scp -r $MAIN_SERVER:/opt/shophosting/templates/ /opt/shophosting/

# Restart worker
sudo systemctl restart provisioning-worker
```

Consider using a deployment tool (Ansible, rsync scripts) for managing multiple workers.
