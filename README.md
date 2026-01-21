# ShopHosting.io

A multi-tenant Docker hosting platform that automatically provisions containerized e-commerce stores (WooCommerce and Magento) on-demand.

## Features

- **Automated Provisioning**: Customers sign up and receive a fully configured online store within minutes
- **Multi-Platform Support**: WooCommerce (WordPress) and Magento 2 with Varnish caching
- **Self-Service Dashboard**: Customers can view store status, credentials, and manage their stores
- **Background Job Processing**: Redis-backed queue system for reliable provisioning
- **SSL/TLS Support**: Automatic certificate management with Let's Encrypt
- **Resource Isolation**: Each customer gets isolated Docker containers with configurable resource limits

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
sudo git clone https://github.com/YOUR_USERNAME/shophosting.git
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

### 8. Install Systemd Services

```bash
sudo cp /opt/shophosting/shophosting-webapp.service /etc/systemd/system/
sudo cp /opt/shophosting/provisioning-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable shophosting-webapp provisioning-worker
sudo systemctl start shophosting-webapp provisioning-worker
```

### 9. Configure Nginx

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
│   └── templates/          # Jinja2 HTML templates
├── provisioning/           # Background worker
│   └── provisioning_worker.py
├── templates/              # Docker Compose templates
│   ├── woocommerce-compose.yml.j2
│   └── magento-compose.yml.j2
├── docker/                 # Custom Docker images
├── migrations/             # Database migrations
├── scripts/                # Utility scripts
├── logs/                   # Application logs
├── schema.sql              # Database schema
└── .env                    # Environment configuration
```

## Documentation

- [Development Guide](DEVELOPMENT_GUIDE.md) - Detailed development instructions
- [System Guide](SYSTEM_GUIDE.md) - System architecture and operations

## License

Copyright (c) 2026. All rights reserved.
