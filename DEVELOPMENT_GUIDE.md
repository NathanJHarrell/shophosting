# ShopHosting.io Hosting - Infrastructure Development Guide

## Project Overview

ShopHosting.io is a **multi-tenant Docker hosting platform** that automatically provisions containerized e-commerce stores (WooCommerce and Magento) on-demand. It provides a self-service SaaS experience where customers sign up and receive a fully configured online store.

### Technology Stack

| Layer | Technology |
|-------|------------|
| Backend | Python 3.10, Flask 3.0.0 |
| Database | MySQL 8.0 |
| Queue/Cache | Redis, RQ (Python-RQ) |
| Containerization | Docker, Docker Compose |
| Reverse Proxy | Nginx |
| SSL/TLS | Certbot (Let's Encrypt) |
| Process Manager | Systemd |

---

## Project Timeline Summary

| Phase | Duration | Cumulative |
|-------|----------|------------|
| Stage 1-5: Initial Setup | 1-2 days | Day 1-2 |
| Phase 1: Core Functionality | 1-2 days | Day 2-4 |
| Phase 2: Provisioning Pipeline | 2-3 days | Day 4-7 |
| Phase 3: Production Hardening | 3-5 days | Day 7-12 |
| Phase 4: Feature Enhancements | 5-10 days | Day 12-22 |
| **Total** | **2-3 weeks** | |

> *Estimates assume 1 developer with Claude assistance, familiar with Linux and the stack.*

---

## Architecture Overview

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

---

## Directory Structure Reference

```
/opt/shophosting.io/
├── webapp/                     # Flask web application
│   ├── app.py                  # Main entry point, routes
│   ├── models.py               # Database models (Customer, PortManager)
│   ├── templates/              # Jinja2 HTML templates
│   └── requirements.txt
│
├── provisioning/               # Background worker
│   ├── provisioning_worker.py  # Container orchestration logic
│   ├── enqueue_provisioning.py # Job queue interface
│   └── requirements.txt
│
├── templates/                  # Docker Compose templates
│   ├── woocommerce-compose.yml
│   └── magento-compose.yml
│
├── logs/                       # Application logs
├── schema.sql                  # Database schema
├── .env                        # Environment config
└── *.service                   # Systemd unit files
```

---

## Development Stages

### Stage 1: Infrastructure Prerequisites ⏱️ 2-4 hours

Before any development, ensure these system dependencies are installed:

**Required Services:**
- [ ] Docker Engine + Docker Compose *(30 min)*
- [ ] MySQL Server 8.0 *(20 min)*
- [ ] Redis Server *(10 min)*
- [ ] Nginx *(15 min)*
- [ ] Certbot (for SSL) *(15 min)*
- [ ] Python 3.10+ *(10 min)*

**System Configuration:**
- [ ] Create `shophostingio` system user *(5 min)*
- [ ] Create directories: `/opt/shophosting.io/logs/`, `/var/customers/` *(5 min)*
- [ ] Set proper permissions on `/var/customers/` *(5 min)*
- [ ] Configure Nginx to proxy to Flask app *(30 min)*

**Commands:**
```bash
# Create system user
sudo useradd -r -s /bin/false shophosting

# Create directories
sudo mkdir -p /opt/shophosting.io/logs /var/customers
sudo chown -R shophosting:shophosting /opt/shophosting.io /var/customers
```

---

### Stage 2: Database Setup ⏱️ 30 minutes

**Initialize the database:**

```bash
# Create database and user
mysql -u root -p < /opt/shophosting.io/schema.sql
```

**Schema creates:**
- `customers` table - Customer accounts, domains, credentials, status
- `provisioning_jobs` table - Background job tracking
- `audit_log` table - Activity logging

---

### Stage 3: Environment Configuration ⏱️ 1 hour

**Copy and configure environment:**

```bash
cp /opt/shophosting.io/.env.example /opt/shophosting.io/.env
```

**Critical variables to configure:**

| Variable | Purpose | Priority | Time |
|----------|---------|----------|------|
| `SECRET_KEY` | Flask session encryption | **Critical** | 5 min |
| `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME` | MySQL connection | **Critical** | 10 min |
| `REDIS_HOST`, `REDIS_PORT` | Redis connection | **Critical** | 5 min |
| `BASE_DOMAIN` | Customer store domains | Before production | 10 min |
| `SMTP_*` | Email notifications | Optional | 15 min |
| `PORT_RANGE_START`, `PORT_RANGE_END` | Customer ports | Before production | 5 min |
| `DEFAULT_MEMORY_LIMIT`, `DEFAULT_CPU_LIMIT` | Container resources | Before production | 5 min |

---

### Stage 4: Application Setup ⏱️ 30 minutes

**Install Python dependencies:**

```bash
# Web application
cd /opt/shophosting.io/webapp
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Provisioning worker
cd /opt/shophosting.io/provisioning
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

### Stage 5: Service Deployment ⏱️ 1 hour

**Install and enable systemd services:**

```bash
sudo cp /opt/shophosting.io/shophosting-webapp.service /etc/systemd/system/
sudo cp /opt/shophosting.io/provisioning-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable shophosting-webapp provisioning-worker
sudo systemctl start shophosting-webapp provisioning-worker
```

*Include time for initial debugging and verification.*

---

## Component Development Guide

### Web Application (`webapp/`)

**Key Files:**
- `app.py` - Flask routes and request handling
- `models.py` - Database ORM and business logic

**Routes:**

| Route | Method | Purpose |
|-------|--------|---------|
| `/` | GET | Landing page |
| `/signup` | GET/POST | Customer registration |
| `/login` | GET/POST | Authentication |
| `/logout` | GET | Logout |
| `/dashboard` | GET | Customer control panel |
| `/api/status` | GET | Provisioning status JSON |
| `/api/credentials` | GET | Store credentials JSON |

**Development workflow:**
```bash
cd /opt/shophosting.io/webapp
source venv/bin/activate
export FLASK_DEBUG=true
python app.py
```

---

### Provisioning Worker (`provisioning/`)

**Key Files:**
- `provisioning_worker.py` - Main orchestration logic
- `enqueue_provisioning.py` - Job queue interface

**Provisioning Steps (in order):**
1. Create customer directory (`/var/customers/customer-{id}/`)
2. Generate secure passwords
3. Render docker-compose.yml from template
4. Start Docker containers
5. Configure Nginx reverse proxy
6. Obtain SSL certificate (Certbot)
7. Install application (wp-cli for WordPress)
8. Save credentials to database
9. Send welcome email
10. Update customer status

**Development workflow:**
```bash
cd /opt/shophosting.io/provisioning
source venv/bin/activate
rq worker provisioning --with-scheduler
```

---

### Docker Templates (`templates/`)

**WooCommerce Stack:**
- WordPress (latest)
- MySQL 8.0
- Redis (caching)

**Magento Stack:**
- Bitnami Magento
- MySQL 8.0
- Elasticsearch 7.17.9
- Redis

**Template variables available:**
- `{{ customer_id }}` - Unique customer ID
- `{{ domain }}` - Customer domain
- `{{ port }}` - Assigned port (8001-8010)
- `{{ db_name }}`, `{{ db_user }}`, `{{ db_password }}`
- `{{ admin_password }}`
- `{{ memory_limit }}`, `{{ cpu_limit }}`

---

## Customer Lifecycle States

```
┌─────────┐     ┌──────────────┐     ┌────────┐
│ Pending │ ──► │ Provisioning │ ──► │ Active │
└─────────┘     └──────────────┘     └────────┘
                      │
                      │ (on error)
                      ▼
                ┌────────┐
                │ Failed │
                └────────┘
```

| Status | Description |
|--------|-------------|
| `pending` | Account created, waiting for provisioning |
| `provisioning` | Background job running |
| `active` | Store fully operational |
| `failed` | Provisioning failed (error stored) |
| `suspended` | Manually suspended |

---

## Development Priorities

### Phase 1: Core Functionality (Foundation) ⏱️ 1-2 days

| Task | Estimate | Risk |
|------|----------|------|
| Verify database schema is applied | 1 hour | Low |
| Test Flask app runs and serves pages | 2 hours | Low |
| Test RQ worker connects to Redis | 1 hour | Low |
| Test database connections from both services | 2 hours | Medium |
| Debug and fix connection issues | 2-4 hours | Medium |

### Phase 2: Provisioning Pipeline ⏱️ 2-3 days

| Task | Estimate | Risk |
|------|----------|------|
| Test WooCommerce template renders correctly | 2 hours | Low |
| Test Docker Compose brings up containers | 3 hours | Medium |
| Test Nginx configuration generation | 2 hours | Medium |
| Test full provisioning flow end-to-end | 4 hours | **High** |
| Debug provisioning failures | 4-8 hours | **High** |
| Test Magento template (if needed) | 3 hours | Medium |

### Phase 3: Production Hardening ⏱️ 3-5 days

| Task | Estimate | Risk |
|------|----------|------|
| Configure proper SECRET_KEY | 30 min | Low |
| Set up HTTPS for main application | 2-3 hours | Medium |
| Configure email notifications | 2 hours | Low |
| Implement customer deletion/cleanup | 4-6 hours | Medium |
| Add monitoring and alerting | 4-6 hours | Medium |
| Set up log rotation | 1 hour | Low |
| Security audit and fixes | 4-8 hours | Medium |

### Phase 4: Feature Enhancements ⏱️ 5-10 days

| Task | Estimate | Risk |
|------|----------|------|
| Add admin dashboard | 2-3 days | Medium |
| Implement billing integration | 2-4 days | **High** |
| Add store backup functionality | 1-2 days | Medium |
| Add resource usage monitoring | 1 day | Low |
| Implement auto-scaling (expand port range) | 1 day | Medium |

---

## Testing Checklist

Since there's no automated test suite, use this manual testing checklist:

### Unit Testing Points ⏱️ 4-6 hours total
- [ ] `models.py` - Customer CRUD operations *(1 hour)*
- [ ] `models.py` - Port allocation/release *(1 hour)*
- [ ] `models.py` - Password hashing/verification *(30 min)*
- [ ] `app.py` - Form validation *(1 hour)*
- [ ] `provisioning_worker.py` - Template rendering *(1 hour)*

### Integration Testing Points ⏱️ 1-2 days total
- [ ] Signup flow creates customer and enqueues job *(2 hours)*
- [ ] Dashboard shows correct status for each state *(1 hour)*
- [ ] Provisioning worker picks up and processes jobs *(2 hours)*
- [ ] Docker containers start and are accessible *(2 hours)*
- [ ] Nginx proxies requests correctly *(2 hours)*
- [ ] SSL certificates are obtained (staging first) *(2 hours)*

### System Constraints
- Port range limits to 10 concurrent customers (8001-8010)
- MySQL connection pool: 5 connections
- Gunicorn workers: 4

---

## Troubleshooting

### Common Issues

**Permission denied on /var/customers/**
```bash
sudo chown -R shophosting:shophosting /var/customers/
sudo chmod 755 /var/customers/
```

**Worker not processing jobs**
```bash
# Check Redis is running
redis-cli ping

# Check worker status
sudo systemctl status provisioning-worker

# View worker logs
tail -f /opt/shophosting.io/logs/provisioning_worker.log
```

**Docker containers fail to start**
```bash
# Check Docker daemon
sudo systemctl status docker

# Check container logs
docker logs customer-{id}-web
```

**Database connection issues**
```bash
# Test MySQL connection
mysql -u shophosting_user -p -h localhost shophosting_db
```

### Log Locations

| Component | Log File |
|-----------|----------|
| Flask App | `/opt/shophosting.io/logs/webapp.log` |
| Provisioning Worker | `/opt/shophosting.io/logs/provisioning_worker.log` |
| Nginx | `/var/log/nginx/error.log` |
| Customer Containers | `docker logs customer-{id}-web` |

---

## Security Considerations

### Already Implemented
- CSRF protection on forms
- Password hashing (Werkzeug)
- Parameterized SQL queries
- Systemd hardening (NoNewPrivileges, PrivateTmp)

### Needs Implementation ⏱️ 2-3 days total
- [ ] Rate limiting on signup/login *(2-3 hours)*
- [ ] Input sanitization audit *(2-4 hours)*
- [ ] Network isolation between customer containers *(3-4 hours)*
- [ ] Secrets management (consider Vault) *(4-6 hours)*
- [ ] Regular security updates for base images *(1-2 hours setup)*
- [ ] WAF rules for customer stores *(4-6 hours)*

---

## Quick Reference Commands

```bash
# Start services
sudo systemctl start shophosting-webapp provisioning-worker

# Stop services
sudo systemctl stop shophosting-webapp provisioning-worker

# Restart services
sudo systemctl restart shophosting-webapp provisioning-worker

# View webapp logs
tail -f /opt/shophosting.io/logs/webapp.log

# View worker logs
tail -f /opt/shophosting.io/logs/provisioning_worker.log

# Check Redis queue
redis-cli
> KEYS rq:*
> LLEN rq:queue:provisioning

# Manual provisioning test (development)
cd /opt/shophosting.io/provisioning
source venv/bin/activate
python -c "from enqueue_provisioning import ProvisioningQueue; ProvisioningQueue.enqueue(customer_id=1)"

# Check service status
sudo systemctl status shophosting-webapp
sudo systemctl status provisioning-worker
```

---

## Risk Assessment

| Risk Area | Likelihood | Impact | Mitigation |
|-----------|------------|--------|------------|
| Docker networking issues | Medium | High | Test early, have fallback configs |
| SSL certificate failures | Medium | Medium | Use staging certs first, have manual process |
| Permission/ownership issues | High | Low | Document all required permissions |
| Resource exhaustion (10 customer limit) | Low | High | Plan scaling strategy early |
| Provisioning race conditions | Low | Medium | Add proper locking mechanisms |

---

## Next Steps

1. **Day 1 Morning:** Verify Prerequisites - Ensure all system dependencies are installed
2. **Day 1 Afternoon:** Initialize Database - Run schema.sql, configure .env
3. **Day 2:** Start Services - Enable systemd services, verify connectivity
4. **Day 2-3:** Test Signup Flow - Create test customer, verify provisioning
5. **Day 3-4:** Debug Issues - Review logs, fix any provisioning failures
6. **Week 2:** Production Hardening - HTTPS, monitoring, security
7. **Week 2-3:** Feature Development - Admin dashboard, billing (if required)

---

## Remaining Docker Provisioning Work

The following tasks need to be completed to make Docker provisioning fully functional:

### Completed
- [x] Template files renamed to `.yml.j2` extension (required by provisioning worker)
- [x] Custom WordPress Dockerfile created at `/opt/shophosting.io/docker/wordpress/Dockerfile`

### Still Required

#### 1. Create Entrypoint Wrapper Script ⏱️ 30 min
Create `/opt/shophosting.io/docker/wordpress/entrypoint-wrapper.sh`:
```bash
#!/bin/bash
set -e

# Wait for MySQL to be ready
until mysql -h"$WORDPRESS_DB_HOST" -u"$WORDPRESS_DB_USER" -p"$WORDPRESS_DB_PASSWORD" -e "SELECT 1" &>/dev/null; do
    echo "Waiting for MySQL..."
    sleep 2
done

# Run original WordPress entrypoint
exec docker-entrypoint.sh "$@"
```

#### 2. Build Custom WordPress Image ⏱️ 10 min
```bash
cd /opt/shophosting.io/docker/wordpress
docker build -t shophosting/wordpress:latest .
```

#### 3. Update WooCommerce Template ⏱️ 20 min
Update `/opt/shophosting.io/templates/woocommerce-compose.yml.j2`:
- Change `image: wordpress:latest` to `image: shophosting/wordpress:latest`
- Add MySQL healthcheck
- Add `depends_on` with condition for db health

#### 4. Fix Provisioning Worker ⏱️ 1 hour
Update `/opt/shophosting.io/provisioning/provisioning_worker.py`:

**Line 150-153**: Templates already fixed (now .yml.j2)

**install_application method (line 343-383)**: Modify to:
- Wait for container health check to pass before running wp-cli
- Add retry logic for WP-CLI commands
- Handle case where WordPress is already installed

Example fix:
```python
def wait_for_container_health(self, container_name, timeout=300):
    """Wait for container to be healthy"""
    import time
    start = time.time()
    while time.time() - start < timeout:
        result = subprocess.run(
            ['docker', 'inspect', '--format', '{{.State.Health.Status}}', container_name],
            capture_output=True, text=True
        )
        if result.stdout.strip() == 'healthy':
            return True
        time.sleep(5)
    return False
```

#### 5. Add phpMyAdmin (Optional) ⏱️ 30 min
Add to WooCommerce template:
```yaml
phpmyadmin:
  image: phpmyadmin:latest
  container_name: {{ container_prefix }}-phpmyadmin
  restart: unless-stopped
  environment:
    PMA_HOST: db
    PMA_USER: {{ db_user }}
    PMA_PASSWORD: {{ db_password }}
  ports:
    - "{{ web_port | int + 1000 }}:80"
  depends_on:
    - db
  networks:
    - {{ container_prefix }}-network
```

#### 6. Update Magento Template ⏱️ 30 min
Add healthchecks to all services in `/opt/shophosting.io/templates/magento-compose.yml.j2`

#### 7. Environment Variable for Image ⏱️ 15 min
Add to `.env.example`:
```
WORDPRESS_IMAGE=shophosting/wordpress:latest
MAGENTO_IMAGE=bitnami/magento:latest
```

### Quick Reference: File Locations

| File | Purpose |
|------|---------|
| `/opt/shophosting.io/docker/wordpress/Dockerfile` | Custom WP image with WP-CLI |
| `/opt/shophosting.io/docker/wordpress/entrypoint-wrapper.sh` | MySQL wait script |
| `/opt/shophosting.io/templates/woocommerce-compose.yml.j2` | WooCommerce stack template |
| `/opt/shophosting.io/templates/magento-compose.yml.j2` | Magento stack template |
| `/opt/shophosting.io/provisioning/provisioning_worker.py` | Provisioning orchestration |

### Estimated Time to Complete: 3-4 hours

### Testing After Changes
1. Build the custom WordPress image
2. Create a test customer via signup
3. Monitor `/opt/shophosting.io/logs/provisioning_worker.log`
4. Verify containers start: `docker ps`
5. Access the store via assigned port
