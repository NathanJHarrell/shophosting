# ShopHosting.io Hosting System Guide

A comprehensive guide to understanding how the ShopHosting.io multi-tenant Docker hosting platform works.

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Architecture](#architecture)
3. [Core Components](#core-components)
4. [How Provisioning Works](#how-provisioning-works)
5. [User Journey](#user-journey)
6. [Configuration & Setup](#configuration--setup)
7. [Troubleshooting](#troubleshooting)
8. [Database Schema](#database-schema)

---

## System Overview

**ShopHosting.io** is a SaaS (Software-as-a-Service) platform that automates the deployment and management of containerized e-commerce stores. It allows customers to provision WooCommerce or Magento stores with a single click, and the system handles all infrastructure setup automatically.

### Key Features

- **Multi-Tenant Architecture**: Multiple customers share the same host, each in isolated Docker containers
- **Automated Provisioning**: One-click deployment with complete infrastructure setup
- **Reverse Proxy Integration**: Nginx automatically routes customer domains to their containers
- **SSL/TLS Support**: Automatic Let's Encrypt certificate provisioning via Certbot
- **Database Isolation**: Each customer has dedicated MySQL databases
- **Job Queue System**: Background provisioning tasks via Redis Queue (RQ)
- **Resource Management**: Per-customer CPU and memory limits

### Technology Stack

| Layer | Technology | Purpose |
|-------|------------|---------|
| Web Framework | Flask 3.0 | HTTP APIs and customer portal |
| Database | MySQL 8.0 | Customer accounts, credentials, status |
| Cache/Queue | Redis | Background job queue, caching |
| Job Worker | RQ (Python-RQ) | Async provisioning orchestration |
| Containerization | Docker + Docker Compose | Application isolation |
| Reverse Proxy | Nginx | Domain routing, SSL termination |
| SSL/TLS | Certbot + Let's Encrypt | HTTPS certificate provisioning |
| Languages | Python 3.10 | Application code |

---

## Architecture

### System Diagram

```
                           EXTERNAL USERS
                                 │
                    ┌────────────┴────────────┐
                    │                         │
                    ▼                         ▼
            ┌──────────────────┐      ┌──────────────────┐
            │  User Dashboard  │      │ Customer Stores  │
            │   (port 5000)    │      │  (ports 8001+)   │
            │    HTTP/HTTPS    │      │    HTTP/HTTPS    │
            └────────┬─────────┘      └────────┬─────────┘
                     │                         │
                     └────────────┬────────────┘
                                  │
                         ┌────────▼──────────┐
                         │  Nginx Reverse    │
                         │  Proxy            │
                         │  :80, :443        │
                         └────────┬──────────┘
                                  │
            ┌─────────────┬────────┴───────┬──────────────┐
            │             │                │              │
            ▼             ▼                ▼              ▼
    ┌──────────────┐ ┌──────────┐  ┌──────────┐  ┌──────────┐
    │ Flask App    │ │Customer-1│  │Customer-2│  │Customer-N│
    │ :5000        │ │  Store   │  │  Store   │  │  Store   │
    │ (Gunicorn)   │ │ :8001    │  │ :8002    │  │ :800N    │
    └──────┬───────┘ └──────────┘  └──────────┘  └──────────┘
           │
    ┌──────┴────────┬────────────┐
    │               │            │
    ▼               ▼            ▼
 ┌─────────┐  ┌──────────┐  ┌────────────┐
 │  MySQL  │  │  Redis   │  │  RQ Worker │
 │Database │  │  Queue   │  │(Provisioner)│
 │         │  │          │  │            │
 └─────────┘  └──────────┘  └────────────┘
```

### Component Interactions

1. **User Creates Account** → Flask Web App → MySQL stores customer record
2. **User Orders Store** → Web App → Creates job in Redis Queue
3. **RQ Worker Picks Job** → Provisions entire infrastructure
4. **Provisioning Complete** → Updates status in MySQL, sends welcome email
5. **Customer Accesses Store** → Nginx routes to correct Docker container

---

## Core Components

### 1. Flask Web Application (`webapp/app.py`)

**Purpose**: Serves the customer-facing portal and admin dashboard.

**Key Routes**:
- `GET /` - Homepage with signup/login
- `POST /register` - Customer registration
- `POST /login` - Customer authentication
- `GET /dashboard` - Customer dashboard (requires login)
- `POST /provision` - Order new store (creates provisioning job)
- `GET /status/<customer_id>` - Check provisioning status

**Key Functions**:
- User authentication and session management
- Form validation (email, password, domain)
- Integration with ProvisioningQueue to enqueue jobs
- Real-time status updates via AJAX

**Files**:
- `app.py` - Main Flask application
- `models.py` - Database ORM models
- `templates/` - Jinja2 HTML templates

### 2. Provisioning Worker (`provisioning/provisioning_worker.py`)

**Purpose**: Background job processor that automates complete store deployment.

**How It Works**:
- Listens on Redis Queue for provisioning jobs
- Executes 9-step provisioning pipeline
- Handles errors with automatic rollback
- Updates database with status at each stage

**Key Methods**:
```python
provision_customer()              # Main orchestrator
├── create_customer_directory()   # Creates /var/customers/customer-{id}/
├── generate_docker_compose()     # Creates docker-compose.yml
├── start_containers()            # Runs docker-compose up
├── configure_reverse_proxy()     # Sets up Nginx config
├── install_application()         # Verifies app readiness
├── save_customer_credentials()   # Encrypts and stores credentials
├── send_welcome_email()          # Sends customer welcome email
└── update_customer_status()      # Updates DB with current state
```

**What It Does Per Customer**:
1. Creates isolated directory structure
2. Generates secure random passwords
3. Creates Docker Compose file (MySQL + Web server)
4. Starts Docker containers with resource limits
5. Configures Nginx to route domain to container
6. Attempts SSL certificate provisioning via Certbot
7. Verifies application is running
8. Stores encrypted credentials in database
9. Sends welcome email with login information

### 3. MySQL Database (`schema.sql`)

**Purpose**: Persistent storage for customer accounts and credentials.

**Main Tables**:

**`customers` table**:
```sql
id                  INT         - Primary key, auto-increment
email              VARCHAR     - Customer email (unique)
password_hash      VARCHAR     - Hashed password
company_name       VARCHAR     - Customer's company
domain             VARCHAR     - Customer's store domain (unique)
platform           ENUM        - 'woocommerce' or 'magento'
status             ENUM        - pending|provisioning|active|suspended|failed
web_port           INT         - Assigned container port (8001-8100)
db_name            VARCHAR     - Database name for this customer
db_user            VARCHAR     - Database user for this customer
db_password        VARCHAR     - Database password (encrypted)
admin_user         VARCHAR     - Store admin username
admin_password     VARCHAR     - Store admin password (encrypted)
error_message      TEXT        - Error details if provisioning fails
created_at         TIMESTAMP   - Account creation time
updated_at         TIMESTAMP   - Last update time
```

**`port_assignments` table** (optional):
- Tracks which ports (8001-8100) are assigned to which customers
- Ensures no port conflicts

### 4. Redis Queue (`provisioning/enqueue_provisioning.py`)

**Purpose**: Manages async job queue for provisioning tasks.

**How It Works**:
- Flask app enqueues provisioning jobs to Redis
- RQ worker continuously polls Redis for new jobs
- Processes jobs one at a time (or in parallel with multiple workers)
- Stores job status and results

**Job Structure**:
```python
{
    'customer_id': 123,
    'email': 'user@example.com',
    'domain': 'mystore.shophosting.io',
    'platform': 'woocommerce',  # or 'magento'
    'site_title': 'My Awesome Store',
    'admin_user': 'admin',
    'web_port': 8001,
    'memory_limit': '1g',
    'cpu_limit': '1.0'
}
```

### 5. Nginx Reverse Proxy

**Purpose**: Routes customer domains to correct Docker containers and handles SSL.

**Configuration Per Customer** (`/etc/nginx/sites-available/customer-{id}.conf`):
- Listens on port 80 (HTTP) and 443 (HTTPS)
- Routes domain to `localhost:PORT` where PORT is customer's assigned port
- Sets proxy headers (Host, X-Real-IP, X-Forwarded-For)
- Handles large file uploads (100MB limit)
- Terminates SSL/TLS connections
- Automatically configures HTTPS after Certbot obtains certificate

**Example Route**:
```
Domain: mystore.shophosting.io
  ↓
Nginx Port 80/443
  ↓
Proxy Pass: http://localhost:8001
  ↓
Docker Container running WordPress
```

### 6. Docker Containers (Per Customer)

**Each Customer Gets**:

**Web Container**:
- Image: WordPress with WooCommerce OR Magento pre-installed
- Exposed Port: 8001-8100 (unique per customer)
- Mounted Volume: `/var/customers/customer-{id}/www`
- Environment Variables: Database credentials, admin user, etc.

**Database Container**:
- Image: MySQL 8.0
- Exposed Port: 3306 (internal, not accessible externally)
- Mounted Volume: `/var/customers/customer-{id}/db` for persistence
- Database: `customer_{id}` with dedicated user

**Network**: Both containers on isolated Docker network named `customer-{id}-net`

---

## How Provisioning Works

### Step-by-Step Process

#### Phase 1: Queue Creation (Flask Web App)
1. Customer submits provisioning form with:
   - Email, password, domain, platform (WooCommerce/Magento)
   - Site title, admin username (optional)
2. Flask app validates inputs
3. Customer record created in database with status `pending`
4. Provisioning job enqueued to Redis with customer details
5. User redirected to status page

#### Phase 2: Job Processing (RQ Worker)

**Step 1: Directory Creation**
```
/var/customers/
└── customer-123/
    ├── docker-compose.yml
    ├── www/          # Web files mount here
    ├── db/           # Database files mount here
    └── nginx.conf    # Nginx configuration
```

**Step 2: Password Generation**
- Generate cryptographically secure passwords for:
  - Database root user
  - Database application user
  - Store admin user
- Each 20+ character, random mix of letters/numbers/symbols

**Step 3: Docker Compose Generation**
- Create `docker-compose.yml` from templates
- Templates in `/opt/shophosting.io/templates/`
  - `woocommerce-compose.yml.j2`
  - `magento-compose.yml.j2`
- Substitute customer-specific values:
  - Customer ID, port, database credentials, admin password
- Use Jinja2 template rendering

**Step 4: Start Containers**
```bash
cd /var/customers/customer-123/
docker-compose up -d
```
- Creates and starts Web and Database containers
- Containers automatically configured with environment variables
- Database initialized with customer-specific schema
- WordPress/Magento auto-setup begins

**Step 5: Configure Nginx**
- Create Nginx config: `/etc/nginx/sites-available/customer-123.conf`
- Create symlink: `/etc/nginx/sites-enabled/customer-123.conf` (enables site)
- Test config: `nginx -t`
- Reload: `systemctl reload nginx`
- Now traffic to `domain.shophosting.io` routes to customer's container

**Step 6: SSL Certificate Provisioning**
- Run Certbot: `certbot certonly --nginx -d domain.shophosting.io`
- Certbot validates domain ownership via Nginx challenge
- If successful: certificate obtained from Let's Encrypt
- Update Nginx config to enable HTTPS
- Reload Nginx
- If fails: Site still accessible via HTTP, can retry later

**Step 7: Verify Application**
- Wait for containers to fully initialize (up to 2 minutes)
- Run health checks:
  - WordPress: Verify web container responds to requests
  - Magento: Run PHP health check
- If checks fail: provisioning fails and triggers rollback

**Step 8: Save Credentials**
- Encrypt database password and admin password
- Store in database `customers` table:
  - `db_name`, `db_user`, `db_password`
  - `admin_user`, `admin_password`
- Credentials never stored in plaintext

**Step 9: Send Welcome Email**
- Email template includes:
  - Store URL: `http://domain.shophosting.io`
  - Admin URL: `http://domain.shophosting.io/wp-admin`
  - Admin username and temporary password
  - Security note: Change password after first login
  - Support contact information

#### Phase 3: Status Updates
- Database `status` column updated at each stage:
  - `pending` → `provisioning` → `active` (success)
  - `pending` → `provisioning` → `failed` (error)
- Flask dashboard polls database and shows real-time status to user

### Error Handling & Rollback

If provisioning fails at ANY step:

1. **Containers Stopped & Removed**:
   ```bash
   cd /var/customers/customer-123/
   docker-compose down -v  # -v removes volumes
   ```

2. **Directory Deleted**:
   ```bash
   rm -rf /var/customers/customer-123/
   ```

3. **Nginx Config Removed**:
   ```bash
   rm /etc/nginx/sites-enabled/customer-123.conf
   rm /etc/nginx/sites-available/customer-123.conf
   systemctl reload nginx
   ```

4. **Database Updated**:
   - Status set to `failed`
   - `error_message` field populated with error details
   - Email sent to admin with failure details

---

## User Journey

### Scenario: Customer Signs Up for WooCommerce Store

**Day 1: Sign Up**
1. User visits `https://shophosting.io`
2. Clicks "Sign Up" button
3. Fills form:
   - Email: `john@example.com`
   - Password: `SecurePass123!`
   - Company: `John's Gadgets`
   - Domain: `johngadgets.shophosting.io`
   - Platform: `WooCommerce`
   - Admin Username: `admin` (auto-filled)
4. Clicks "Create Store"
5. Flask app validates and creates customer record (status: `pending`)
6. Provisioning job queued to Redis
7. User redirected to status page showing "Provisioning in progress..."

**While Provisioning (10-30 minutes)**
- RQ worker picks up job
- Executes 9-step provisioning pipeline
- Dashboard updates real-time:
  - ✓ Directory created
  - ✓ Containers started
  - ✓ Nginx configured
  - ✓ SSL certificate obtained
  - ✓ Application verified
  - ✓ Welcome email sent
  - Status: ACTIVE

**Day 1: Provisioning Complete**
1. User receives welcome email with:
   - Store URL: `http://johngadgets.shophosting.io`
   - Admin URL: `http://johngadgets.shophosting.io/wp-admin`
   - Admin username: `admin`
   - Temporary password: (random secure password)
2. User visits store URL
3. Nginx routes to Docker container on port 8001
4. WooCommerce prompts for initial setup
5. User logs in with provided credentials
6. User completes WooCommerce setup (products, payment methods, etc.)

**Going Forward**
- User can log in to dashboard to:
  - View store statistics
  - Manage billing
  - Access support
- Store automatically gets:
  - Daily backups
  - Automatic updates
  - Free SSL/TLS with auto-renewal
  - Monitoring and alerts

---

## Configuration & Setup

### Environment Variables (`.env` file)

```dotenv
# Flask Configuration
SECRET_KEY=your-very-long-random-secret-key-here
FLASK_DEBUG=False

# Database Configuration
DB_HOST=localhost
DB_USER=shophosting_app
DB_PASSWORD=SecurePass123!
DB_NAME=shophosting_db

# Redis Configuration
REDIS_HOST=localhost
REDIS_PORT=6379

# SMTP Configuration (email)
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-password
SMTP_FROM=noreply@shophosting.io

# Domain Configuration
BASE_DOMAIN=shophosting.io
ADMIN_EMAIL=admin@shophosting.io

# Customer Container Settings
CUSTOMERS_BASE_PATH=/var/customers
PORT_RANGE_START=8001
PORT_RANGE_END=8100
```

### Initial Setup

1. **Install Dependencies**:
   ```bash
   pip install -r webapp/requirements.txt
   pip install -r provisioning/requirements.txt
   ```

2. **Initialize Database**:
   ```bash
   mysql -u root -p < schema.sql
   ```

3. **Create Directories**:
   ```bash
   mkdir -p /var/customers
   mkdir -p /opt/shophosting.io/logs
   chmod 777 /var/customers
   chmod 777 /opt/shophosting.io/logs
   ```

4. **Start Services**:
   ```bash
   # Terminal 1: Flask web app
   cd /opt/shophosting.io/webapp
   gunicorn -w 4 -b 0.0.0.0:5000 app:app
   
   # Terminal 2: RQ worker
   cd /opt/shophosting.io/provisioning
   python provisioning_worker.py
   ```

### Systemd Service Configuration

**For Production Deployment**:

1. **Flask Web App** (`shophosting-webapp.service`):
   ```bash
   sudo systemctl start shophosting-webapp
   sudo systemctl enable shophosting-webapp
   ```

2. **Provisioning Worker** (`provisioning-worker.service`):
   ```bash
   sudo systemctl start provisioning-worker
   sudo systemctl enable provisioning-worker
   ```

3. **View Logs**:
   ```bash
   sudo journalctl -u shophosting-webapp -f
   sudo journalctl -u provisioning-worker -f
   ```

---

## Troubleshooting

### Problem: Provisioning Stuck on "Provisioning"

**Symptoms**: Status shows "provisioning" but doesn't complete

**Causes & Solutions**:
1. **RQ Worker Not Running**
   ```bash
   # Check if worker is running
   ps aux | grep provisioning_worker
   
   # Restart worker
   sudo systemctl restart provisioning-worker
   ```

2. **Redis Connection Failed**
   ```bash
   # Check Redis status
   redis-cli ping
   # Should return: PONG
   
   # Restart Redis if needed
   sudo systemctl restart redis-server
   ```

3. **Database Connection Issue**
   ```bash
   # Test database connection
   mysql -h localhost -u shophosting_app -p shophosting_db
   
   # Check MySQL logs
   tail -f /var/log/mysql/error.log
   ```

### Problem: Provisioning Failed

**Check Error Message**:
```bash
# In Flask web app, customer status shows error
SELECT status, error_message FROM customers WHERE id = 123;
```

**Common Errors & Fixes**:

1. **"Container startup failed"**
   - Insufficient disk space: `df -h /var/customers`
   - Out of memory: `free -h`
   - Docker daemon issues: `sudo systemctl restart docker`

2. **"Nginx configuration failed"**
   - Port already in use: `lsof -i :PORT`
   - Nginx config syntax error: `sudo nginx -t`
   - Reload Nginx: `sudo systemctl reload nginx`

3. **"SSL certificate failed"**
   - Domain DNS not pointing to server: `nslookup domain.shophosting.io`
   - Certbot issues: Check `/var/log/letsencrypt/letsencrypt.log`
   - Site still works over HTTP

4. **"Database connection failed"**
   - MySQL not running: `sudo systemctl start mysql`
   - Password incorrect: verify `.env` file
   - Database doesn't exist: Run `schema.sql`

### Problem: Customer Can't Access Store

**Diagnosis**:
1. **Check if domain resolves**: `ping domain.shophosting.io`
2. **Check if container is running**: `docker ps | grep customer-ID`
3. **Check Nginx config**: `sudo nginx -T | grep customer-ID`
4. **Check container logs**: `docker logs customer-ID-web`

**Common Fixes**:
```bash
# Restart container
docker restart customer-ID-web

# Restart Nginx
sudo systemctl reload nginx

# Check container connectivity
docker exec customer-ID-web curl http://localhost:80/
```

### Problem: Port Conflicts

**Symptom**: "Port already in use" error

**Solution**:
```bash
# Find what's using the port
lsof -i :8001

# Kill process (if needed)
kill -9 PID

# Or assign customer a different port in database
UPDATE customers SET web_port = 8050 WHERE id = 123;
```

### Checking Logs

**Flask Web App Logs**:
```bash
tail -f /opt/shophosting.io/logs/webapp.log
```

**Provisioning Worker Logs**:
```bash
tail -f /opt/shophosting.io/logs/provisioning_worker.log
```

**Nginx Access/Error**:
```bash
tail -f /var/log/nginx/customer-123-access.log
tail -f /var/log/nginx/customer-123-error.log
```

**Docker Container Logs**:
```bash
docker logs customer-123-web
docker logs customer-123-db
```

---

## Database Schema

### Customers Table

```sql
CREATE TABLE customers (
    id INT AUTO_INCREMENT PRIMARY KEY,
    
    -- User Information
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    company_name VARCHAR(255) NOT NULL,
    
    -- Store Configuration
    domain VARCHAR(255) NOT NULL UNIQUE,
    platform ENUM('woocommerce', 'magento') NOT NULL,
    web_port INT UNIQUE,
    
    -- Provisioning Status
    status ENUM('pending', 'provisioning', 'active', 'suspended', 'failed') DEFAULT 'pending',
    error_message TEXT,
    
    -- Customer Database Credentials (populated during provisioning)
    db_name VARCHAR(100),
    db_user VARCHAR(100),
    db_password VARCHAR(255),  -- Encrypted
    
    -- Store Admin Credentials (populated during provisioning)
    admin_user VARCHAR(100),
    admin_password VARCHAR(255),  -- Encrypted
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    -- Indexes
    INDEX idx_email (email),
    INDEX idx_domain (domain),
    INDEX idx_status (status),
    INDEX idx_web_port (web_port)
);
```

### Port Assignments Table

```sql
CREATE TABLE port_assignments (
    port INT PRIMARY KEY,
    customer_id INT NOT NULL,
    assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
);
```

---

## Summary: Data Flow

```
User Registration Form
        ↓
   Flask App
        ↓
   Validate Input
        ↓
   Create Customer Record (status: pending)
        ↓
   Enqueue Provisioning Job to Redis
        ↓
   Return Status Page
        ↓
   RQ Worker Picks Job
        ↓
   Provision Customer (9 steps)
        ↓
   Update Database (status: active)
        ↓
   Send Welcome Email
        ↓
   Customer Accesses Store
        ↓
   Nginx Routes to Docker Container
        ↓
   Customer Uses Store
```

---

## Key Files Reference

| File | Purpose |
|------|---------|
| `webapp/app.py` | Flask web application and routes |
| `webapp/models.py` | Database models (Customer, PortManager) |
| `provisioning/provisioning_worker.py` | Background provisioning orchestrator |
| `provisioning/enqueue_provisioning.py` | Job queue interface |
| `templates/woocommerce-compose.yml.j2` | WooCommerce Docker Compose template |
| `templates/magento-compose.yml.j2` | Magento Docker Compose template |
| `schema.sql` | Database initialization script |
| `.env` | Environment configuration |
| `shophosting-webapp.service` | Systemd service for Flask app |
| `provisioning-worker.service` | Systemd service for RQ worker |

---

## Further Reading

- [Flask Documentation](https://flask.palletsprojects.com/)
- [Docker Compose Documentation](https://docs.docker.com/compose/)
- [Nginx Documentation](https://nginx.org/en/docs/)
- [Let's Encrypt / Certbot](https://certbot.eff.org/)
- [RQ (Python-RQ) Documentation](https://python-rq.org/)
- [MySQL Documentation](https://dev.mysql.com/doc/)

