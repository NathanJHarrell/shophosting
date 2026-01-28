# ShopHosting Monitoring Stack

Grafana + Prometheus monitoring for ShopHosting.io

## Quick Start

### 1. Configure Environment

```bash
cd /opt/shophosting/monitoring
cp .env.example .env
# Edit .env and set a secure Grafana password
nano .env
```

### 2. Enable Metrics Endpoint Access

The webapp needs to be accessible from Docker containers. Add to `/opt/shophosting/.env`:

```bash
GUNICORN_BIND=0.0.0.0:5000
```

Then restart the webapp:
```bash
sudo systemctl restart shophosting-webapp
```

> Note: The firewall and nginx should prevent external access to port 5000.

### 3. Start the Stack

```bash
cd /opt/shophosting/monitoring
docker compose up -d
```

### 4. Configure Nginx (Optional but Recommended)

Add the Grafana proxy to your nginx config. Copy the relevant location blocks from `nginx-grafana.conf` to your server configuration:

```bash
# Add to /etc/nginx/sites-available/shophosting.io
sudo nano /etc/nginx/sites-available/shophosting.io

# Test and reload
sudo nginx -t && sudo systemctl reload nginx
```

### 5. Access Grafana

- **Direct (local only):** http://localhost:3000
- **Via nginx:** https://shophosting.io/grafana

Default credentials:
- Username: `admin`
- Password: Set in `.env` (default: `changeme`)

## Components

| Service | Port | Purpose |
|---------|------|---------|
| Grafana | 3000 | Dashboards and visualization |
| Prometheus | 9090 | Metrics collection and storage |

## Pre-built Dashboards

The stack includes a pre-provisioned dashboard:
- **ShopHosting Overview** - Main monitoring dashboard with uptime, response times, CPU/memory, and alerts

## Metrics Exposed

The webapp exposes these metrics at `/metrics`:

| Metric | Description |
|--------|-------------|
| `shophosting_customers_total` | Customer count by status |
| `shophosting_monitoring_status` | Site up/down status (1=up, 0=down) |
| `shophosting_http_response_time_ms` | HTTP response time |
| `shophosting_uptime_percent` | 24-hour uptime percentage |
| `shophosting_cpu_percent` | Container CPU usage |
| `shophosting_memory_usage_mb` | Container memory usage |
| `shophosting_alerts_total` | Alert count by type |
| `shophosting_alerts_unacknowledged` | Pending alerts count |

## Management Commands

```bash
# View logs
docker compose logs -f

# Restart services
docker compose restart

# Stop stack
docker compose down

# Update images
docker compose pull && docker compose up -d

# View Prometheus targets
curl http://localhost:9090/api/v1/targets
```

## Troubleshooting

### Prometheus can't reach webapp

1. Check webapp is running: `curl http://localhost:5000/metrics`
2. Verify GUNICORN_BIND is set to `0.0.0.0:5000`
3. Check firewall allows Docker bridge traffic
4. View Prometheus targets: http://localhost:9090/targets

### No data in Grafana

1. Check Prometheus is scraping: http://localhost:9090/targets
2. Wait a few minutes for data to accumulate
3. Verify the monitoring worker is running: `sudo systemctl status monitoring-worker`

### Reset Grafana password

```bash
docker exec -it shophosting-grafana grafana-cli admin reset-admin-password newpassword
```

## Data Retention

- Prometheus: 30 days (configurable in docker-compose.yml)
- Monitoring checks (MySQL): 48 hours (cleaned by monitoring worker)
