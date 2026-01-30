# Status Page Design

**Date:** 2026-01-30
**Branch:** feature/status-page

## Overview

Public status page at `status.shophosting.io` showing per-server status including the backup server, with automated detection plus manual incident/maintenance announcements. Matches main site dark theme.

## Architecture

```
status.shophosting.io
        │
        ▼
┌─────────────────────┐
│  Status Blueprint   │
│  (webapp/status/)   │
└─────────────────────┘
        │
        ▼
┌─────────────────────┐      ┌─────────────────────┐
│  Existing Models    │      │  New Models         │
│  - Server           │      │  - StatusIncident   │
│  - ServerSelector   │      │  - StatusMaintenance│
│  - MonitoringCheck  │      │  - StatusIncidentUpdate│
└─────────────────────┘      └─────────────────────┘
        │                            │
        ▼                            ▼
┌─────────────────────────────────────────────────┐
│              MySQL Database                      │
└─────────────────────────────────────────────────┘
```

Nginx routes `status.shophosting.io` to Flask, which detects the subdomain and serves the status blueprint.

## Data Models

### StatusIncident

| Field | Type | Description |
|-------|------|-------------|
| id | INT | Primary key |
| server_id | INT | FK to servers (nullable for global incidents) |
| title | VARCHAR(200) | "Database connectivity issues" |
| status | ENUM | `investigating`, `identified`, `monitoring`, `resolved` |
| severity | ENUM | `minor`, `major`, `critical` |
| is_auto_detected | BOOL | True if system detected, false if manual |
| started_at | TIMESTAMP | When incident began |
| resolved_at | TIMESTAMP | When resolved (nullable) |
| created_at | TIMESTAMP | Record creation |

### StatusIncidentUpdate

| Field | Type | Description |
|-------|------|-------------|
| id | INT | Primary key |
| incident_id | INT | FK to incidents |
| status | ENUM | Status at time of update |
| message | TEXT | "We've identified the root cause..." |
| created_by | INT | FK to admin_users |
| created_at | TIMESTAMP | When posted |

### StatusMaintenance

| Field | Type | Description |
|-------|------|-------------|
| id | INT | Primary key |
| server_id | INT | FK to servers (nullable for global) |
| title | VARCHAR(200) | "Scheduled server upgrades" |
| description | TEXT | Details of the maintenance |
| scheduled_start | TIMESTAMP | When maintenance begins |
| scheduled_end | TIMESTAMP | Expected completion |
| status | ENUM | `scheduled`, `in_progress`, `completed` |

## Status Detection Logic

### Primary: Existing Monitoring Data

- `Server.status` field (active/maintenance/offline)
- `Server.is_healthy()` - checks heartbeat within 2 minutes
- `Server.last_heartbeat` - staleness detection

### Fallback: Active Health Checks

When monitoring data is stale (>5 minutes), perform live checks:

```python
def get_server_status(server):
    # 1. Check existing data first
    if server.last_heartbeat and is_recent(server.last_heartbeat, minutes=5):
        return 'operational' if server.is_healthy() else 'degraded'

    # 2. Fallback: active check
    try:
        response = requests.get(f"https://{server.hostname}/health", timeout=5)
        return 'operational' if response.status_code == 200 else 'degraded'
    except:
        return 'down'
```

### Status Mapping

| Condition | Display Status | Color |
|-----------|---------------|-------|
| Healthy + no incidents | Operational | Green |
| Healthy + minor incident | Degraded | Yellow |
| Unhealthy or major incident | Partial Outage | Orange |
| Down or critical incident | Major Outage | Red |
| Scheduled maintenance active | Maintenance | Blue |

### Auto-Incident Creation

When a server transitions from healthy → unhealthy for >2 consecutive checks, automatically create an incident with `is_auto_detected=True`. Auto-resolve when healthy again.

## Servers to Monitor

| Display Name | Check Target | Type |
|--------------|--------------|------|
| Web Servers | From `servers` table | Dynamic |
| Backup Server | 15.204.249.219 | Static |
| API | shophosting.io/api/health | Endpoint |
| Customer Dashboard | shophosting.io/dashboard | Endpoint |

## Page Layout

### Header
- ShopHosting.io logo (links to main site)
- Overall status banner: "All Systems Operational" / "Partial Outage" / etc.
- Last updated timestamp with auto-refresh indicator

### Current Status Section

```
┌─────────────────────────────────────────────────────────┐
│  ● Web Servers                              Operational │
│    ├── seers                                     ●      │
│    └── [other servers...]                        ●      │
├─────────────────────────────────────────────────────────┤
│  ● Backup Server                            Operational │
├─────────────────────────────────────────────────────────┤
│  ● API                                      Operational │
├─────────────────────────────────────────────────────────┤
│  ● Customer Dashboard                       Operational │
└─────────────────────────────────────────────────────────┘
```

### Active Incidents Section
- Only shown if incidents exist
- Incident title, severity badge, status
- Timeline of updates (newest first)
- "Started X hours ago" / "Resolved after X minutes"

### Scheduled Maintenance Section
- Upcoming 7 days
- Date/time, affected systems, description

### Footer
- "Subscribe to updates" (future: email/webhook)
- Link back to shophosting.io

## File Structure

```
webapp/
├── status/
│   ├── __init__.py          # Blueprint registration
│   ├── routes.py            # Status page routes
│   ├── health_checks.py     # Active health check logic
│   └── models.py            # StatusIncident, StatusMaintenance
├── templates/
│   └── status/
│       ├── index.html       # Main status page
│       └── base_status.html # Status page base template
migrations/
└── 013_add_status_page_tables.sql
```

## Routes

| Route | Method | Description |
|-------|--------|-------------|
| `/` | GET | Main status page (HTML) |
| `/api/status` | GET | JSON status for all systems |
| `/api/incidents` | GET | Current/recent incidents |
| `/api/incidents/<id>` | GET | Single incident with updates |

## Admin Management

New section in admin panel at `/admin/status`:

- **Incidents list** - View all active/recent incidents, filter by status
- **Create incident** - Manually report an issue with server, severity, title
- **Update incident** - Add timeline updates, change status, resolve
- **Maintenance scheduler** - Create/edit scheduled maintenance windows
- **Override status** - Manually set a server's display status

### Incident Workflow

```
[Auto-detected or Manual Create]
            │
            ▼
      Investigating
            │
            ▼
       Identified ──────► Post update explaining cause
            │
            ▼
       Monitoring ──────► Fix deployed, watching
            │
            ▼
        Resolved ───────► Auto-resolve or manual close
```

## Nginx Configuration

```nginx
server {
    server_name status.shophosting.io;
    listen 443 ssl http2;

    ssl_certificate /etc/letsencrypt/live/shophosting.io/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/shophosting.io/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

## Future Enhancements

- Email/webhook subscriptions for status updates
- Historical uptime graphs (90-day view)
- Response time metrics display
- RSS feed for incidents
