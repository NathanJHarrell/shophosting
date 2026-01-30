# Status Page Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a public status page at status.shophosting.io showing per-server status with automated detection and manual incident management.

**Architecture:** Flask Blueprint with subdomain routing, reusing existing Server/monitoring models, new incident/maintenance models, dark theme matching main site.

**Tech Stack:** Flask Blueprint, MySQL, Jinja2 templates, vanilla JavaScript for auto-refresh.

---

## Task 1: Create Database Migration

**Files:**
- Create: `migrations/013_add_status_page_tables.sql`

**Step 1: Write migration file**

```sql
-- Migration: 013_add_status_page_tables.sql
-- Description: Add tables for status page incidents and maintenance
-- Date: 2026-01-30

-- Status incidents (outages, issues)
CREATE TABLE IF NOT EXISTS status_incidents (
    id INT PRIMARY KEY AUTO_INCREMENT,
    server_id INT NULL,
    title VARCHAR(200) NOT NULL,
    status ENUM('investigating', 'identified', 'monitoring', 'resolved') NOT NULL DEFAULT 'investigating',
    severity ENUM('minor', 'major', 'critical') NOT NULL DEFAULT 'minor',
    is_auto_detected BOOLEAN DEFAULT FALSE,
    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (server_id) REFERENCES servers(id) ON DELETE SET NULL,
    INDEX idx_status (status),
    INDEX idx_started (started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Incident timeline updates
CREATE TABLE IF NOT EXISTS status_incident_updates (
    id INT PRIMARY KEY AUTO_INCREMENT,
    incident_id INT NOT NULL,
    status ENUM('investigating', 'identified', 'monitoring', 'resolved') NOT NULL,
    message TEXT NOT NULL,
    created_by INT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (incident_id) REFERENCES status_incidents(id) ON DELETE CASCADE,
    FOREIGN KEY (created_by) REFERENCES admin_users(id) ON DELETE SET NULL,
    INDEX idx_incident (incident_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Scheduled maintenance windows
CREATE TABLE IF NOT EXISTS status_maintenance (
    id INT PRIMARY KEY AUTO_INCREMENT,
    server_id INT NULL,
    title VARCHAR(200) NOT NULL,
    description TEXT,
    scheduled_start TIMESTAMP NOT NULL,
    scheduled_end TIMESTAMP NOT NULL,
    status ENUM('scheduled', 'in_progress', 'completed', 'cancelled') NOT NULL DEFAULT 'scheduled',
    created_by INT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (server_id) REFERENCES servers(id) ON DELETE SET NULL,
    FOREIGN KEY (created_by) REFERENCES admin_users(id) ON DELETE SET NULL,
    INDEX idx_scheduled (scheduled_start, scheduled_end),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Status overrides (manual status for servers/services)
CREATE TABLE IF NOT EXISTS status_overrides (
    id INT PRIMARY KEY AUTO_INCREMENT,
    service_name VARCHAR(100) NOT NULL UNIQUE,
    display_status ENUM('operational', 'degraded', 'partial_outage', 'major_outage', 'maintenance') NOT NULL,
    message VARCHAR(255) NULL,
    created_by INT NULL,
    expires_at TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (created_by) REFERENCES admin_users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

**Step 2: Run migration**

Run: `mysql -u root -p shophosting < migrations/013_add_status_page_tables.sql`

**Step 3: Commit**

```bash
git add migrations/013_add_status_page_tables.sql
git commit -m "feat(status): add database migration for status page tables"
```

---

## Task 2: Create Status Models

**Files:**
- Create: `webapp/status/models.py`

**Step 1: Write the models**

```python
"""
Status Page Models
Handles incidents, maintenance windows, and status tracking
"""

from datetime import datetime, timedelta
from models import get_db_connection


class StatusIncident:
    """Incident tracking for outages and issues"""

    STATUSES = ['investigating', 'identified', 'monitoring', 'resolved']
    SEVERITIES = ['minor', 'major', 'critical']

    def __init__(self, id=None, server_id=None, title=None, status='investigating',
                 severity='minor', is_auto_detected=False, started_at=None,
                 resolved_at=None, created_at=None, updated_at=None):
        self.id = id
        self.server_id = server_id
        self.title = title
        self.status = status
        self.severity = severity
        self.is_auto_detected = is_auto_detected
        self.started_at = started_at or datetime.now()
        self.resolved_at = resolved_at
        self.created_at = created_at
        self.updated_at = updated_at

    def save(self):
        """Save incident to database"""
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            if self.id is None:
                cursor.execute("""
                    INSERT INTO status_incidents
                    (server_id, title, status, severity, is_auto_detected, started_at, resolved_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (self.server_id, self.title, self.status, self.severity,
                      self.is_auto_detected, self.started_at, self.resolved_at))
                self.id = cursor.lastrowid
            else:
                cursor.execute("""
                    UPDATE status_incidents
                    SET server_id=%s, title=%s, status=%s, severity=%s,
                        is_auto_detected=%s, started_at=%s, resolved_at=%s
                    WHERE id=%s
                """, (self.server_id, self.title, self.status, self.severity,
                      self.is_auto_detected, self.started_at, self.resolved_at, self.id))
            conn.commit()
            return self
        finally:
            cursor.close()
            conn.close()

    def resolve(self):
        """Mark incident as resolved"""
        self.status = 'resolved'
        self.resolved_at = datetime.now()
        return self.save()

    def add_update(self, message, status=None, created_by=None):
        """Add a timeline update to this incident"""
        if status:
            self.status = status
            self.save()
        return StatusIncidentUpdate.create(self.id, status or self.status, message, created_by)

    def get_updates(self):
        """Get all updates for this incident"""
        return StatusIncidentUpdate.get_by_incident(self.id)

    @staticmethod
    def get_by_id(incident_id):
        """Get incident by ID"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("SELECT * FROM status_incidents WHERE id = %s", (incident_id,))
            row = cursor.fetchone()
            return StatusIncident(**row) if row else None
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_active():
        """Get all unresolved incidents"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("""
                SELECT * FROM status_incidents
                WHERE status != 'resolved'
                ORDER BY severity DESC, started_at DESC
            """)
            return [StatusIncident(**row) for row in cursor.fetchall()]
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_recent(days=7):
        """Get incidents from the last N days"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("""
                SELECT * FROM status_incidents
                WHERE started_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
                ORDER BY started_at DESC
            """, (days,))
            return [StatusIncident(**row) for row in cursor.fetchall()]
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_active_for_server(server_id):
        """Get active incidents for a specific server"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("""
                SELECT * FROM status_incidents
                WHERE (server_id = %s OR server_id IS NULL) AND status != 'resolved'
                ORDER BY severity DESC
            """, (server_id,))
            return [StatusIncident(**row) for row in cursor.fetchall()]
        finally:
            cursor.close()
            conn.close()


class StatusIncidentUpdate:
    """Timeline updates for incidents"""

    def __init__(self, id=None, incident_id=None, status=None, message=None,
                 created_by=None, created_at=None):
        self.id = id
        self.incident_id = incident_id
        self.status = status
        self.message = message
        self.created_by = created_by
        self.created_at = created_at

    @staticmethod
    def create(incident_id, status, message, created_by=None):
        """Create a new incident update"""
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO status_incident_updates (incident_id, status, message, created_by)
                VALUES (%s, %s, %s, %s)
            """, (incident_id, status, message, created_by))
            conn.commit()
            return cursor.lastrowid
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_by_incident(incident_id):
        """Get all updates for an incident"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("""
                SELECT u.*, a.name as admin_name
                FROM status_incident_updates u
                LEFT JOIN admin_users a ON u.created_by = a.id
                WHERE u.incident_id = %s
                ORDER BY u.created_at DESC
            """, (incident_id,))
            return [StatusIncidentUpdate(**{k: v for k, v in row.items() if k != 'admin_name'})
                    for row in cursor.fetchall()]
        finally:
            cursor.close()
            conn.close()


class StatusMaintenance:
    """Scheduled maintenance windows"""

    STATUSES = ['scheduled', 'in_progress', 'completed', 'cancelled']

    def __init__(self, id=None, server_id=None, title=None, description=None,
                 scheduled_start=None, scheduled_end=None, status='scheduled',
                 created_by=None, created_at=None, updated_at=None):
        self.id = id
        self.server_id = server_id
        self.title = title
        self.description = description
        self.scheduled_start = scheduled_start
        self.scheduled_end = scheduled_end
        self.status = status
        self.created_by = created_by
        self.created_at = created_at
        self.updated_at = updated_at

    def save(self):
        """Save maintenance window to database"""
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            if self.id is None:
                cursor.execute("""
                    INSERT INTO status_maintenance
                    (server_id, title, description, scheduled_start, scheduled_end, status, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (self.server_id, self.title, self.description,
                      self.scheduled_start, self.scheduled_end, self.status, self.created_by))
                self.id = cursor.lastrowid
            else:
                cursor.execute("""
                    UPDATE status_maintenance
                    SET server_id=%s, title=%s, description=%s, scheduled_start=%s,
                        scheduled_end=%s, status=%s
                    WHERE id=%s
                """, (self.server_id, self.title, self.description,
                      self.scheduled_start, self.scheduled_end, self.status, self.id))
            conn.commit()
            return self
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_by_id(maintenance_id):
        """Get maintenance by ID"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("SELECT * FROM status_maintenance WHERE id = %s", (maintenance_id,))
            row = cursor.fetchone()
            return StatusMaintenance(**row) if row else None
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_upcoming(days=7):
        """Get scheduled maintenance in the next N days"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("""
                SELECT * FROM status_maintenance
                WHERE status IN ('scheduled', 'in_progress')
                AND scheduled_start <= DATE_ADD(NOW(), INTERVAL %s DAY)
                ORDER BY scheduled_start ASC
            """, (days,))
            return [StatusMaintenance(**row) for row in cursor.fetchall()]
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_active():
        """Get currently active maintenance windows"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("""
                SELECT * FROM status_maintenance
                WHERE status = 'in_progress'
                OR (status = 'scheduled' AND scheduled_start <= NOW() AND scheduled_end >= NOW())
            """)
            return [StatusMaintenance(**row) for row in cursor.fetchall()]
        finally:
            cursor.close()
            conn.close()


class StatusOverride:
    """Manual status overrides for services"""

    STATUSES = ['operational', 'degraded', 'partial_outage', 'major_outage', 'maintenance']

    def __init__(self, id=None, service_name=None, display_status=None, message=None,
                 created_by=None, expires_at=None, created_at=None, updated_at=None):
        self.id = id
        self.service_name = service_name
        self.display_status = display_status
        self.message = message
        self.created_by = created_by
        self.expires_at = expires_at
        self.created_at = created_at
        self.updated_at = updated_at

    @staticmethod
    def get_active():
        """Get all active (non-expired) overrides"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("""
                SELECT * FROM status_overrides
                WHERE expires_at IS NULL OR expires_at > NOW()
            """)
            return {row['service_name']: StatusOverride(**row) for row in cursor.fetchall()}
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def set_override(service_name, display_status, message=None, created_by=None, expires_hours=None):
        """Set or update a status override"""
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            expires_at = None
            if expires_hours:
                expires_at = datetime.now() + timedelta(hours=expires_hours)

            cursor.execute("""
                INSERT INTO status_overrides (service_name, display_status, message, created_by, expires_at)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    display_status = VALUES(display_status),
                    message = VALUES(message),
                    created_by = VALUES(created_by),
                    expires_at = VALUES(expires_at)
            """, (service_name, display_status, message, created_by, expires_at))
            conn.commit()
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def clear_override(service_name):
        """Remove a status override"""
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("DELETE FROM status_overrides WHERE service_name = %s", (service_name,))
            conn.commit()
        finally:
            cursor.close()
            conn.close()
```

**Step 2: Commit**

```bash
git add webapp/status/models.py
git commit -m "feat(status): add status page models for incidents and maintenance"
```

---

## Task 3: Create Health Check Logic

**Files:**
- Create: `webapp/status/health_checks.py`

**Step 1: Write health check module**

```python
"""
Status Page Health Checks
Determines status of servers and services using existing monitoring + fallback checks
"""

import requests
import logging
from datetime import datetime, timedelta
from models import Server, get_db_connection
from status.models import StatusIncident, StatusMaintenance, StatusOverride

logger = logging.getLogger(__name__)

# Backup server configuration
BACKUP_SERVER = {
    'name': 'Backup Server',
    'hostname': '15.204.249.219',
    'check_url': None,  # Will use ping/port check
    'check_port': 22
}

# Service endpoints to monitor
SERVICE_ENDPOINTS = {
    'api': {
        'name': 'API',
        'url': 'https://shophosting.io/api/health',
        'timeout': 5
    },
    'dashboard': {
        'name': 'Customer Dashboard',
        'url': 'https://shophosting.io/dashboard',
        'timeout': 5
    }
}


def is_recent(timestamp, minutes=5):
    """Check if timestamp is within the last N minutes"""
    if not timestamp:
        return False
    if isinstance(timestamp, str):
        timestamp = datetime.fromisoformat(timestamp)
    return datetime.now() - timestamp < timedelta(minutes=minutes)


def check_server_health(server):
    """
    Determine server health status.
    Primary: existing monitoring data
    Fallback: active health check
    """
    # Check for manual override first
    overrides = StatusOverride.get_active()
    if server.name in overrides:
        return overrides[server.name].display_status

    # Check for active maintenance
    active_maintenance = StatusMaintenance.get_active()
    for maint in active_maintenance:
        if maint.server_id == server.id or maint.server_id is None:
            return 'maintenance'

    # Check for active incidents
    incidents = StatusIncident.get_active_for_server(server.id)
    if incidents:
        worst_severity = max(incidents, key=lambda i: ['minor', 'major', 'critical'].index(i.severity))
        if worst_severity.severity == 'critical':
            return 'major_outage'
        elif worst_severity.severity == 'major':
            return 'partial_outage'
        else:
            return 'degraded'

    # Primary: Use existing monitoring data
    if server.last_heartbeat and is_recent(server.last_heartbeat, minutes=5):
        if server.status == 'maintenance':
            return 'maintenance'
        elif server.status == 'offline':
            return 'major_outage'
        elif server.is_healthy():
            return 'operational'
        else:
            return 'degraded'

    # Fallback: Active health check
    try:
        if server.hostname:
            response = requests.get(
                f"https://{server.hostname}/health",
                timeout=5,
                verify=True
            )
            if response.status_code == 200:
                return 'operational'
            else:
                return 'degraded'
    except requests.exceptions.Timeout:
        return 'degraded'
    except requests.exceptions.RequestException:
        return 'major_outage'

    return 'unknown'


def check_backup_server():
    """Check backup server health via socket connection"""
    import socket

    # Check for manual override
    overrides = StatusOverride.get_active()
    if 'backup_server' in overrides:
        return overrides['backup_server'].display_status

    # Check for active maintenance affecting backup
    active_maintenance = StatusMaintenance.get_active()
    for maint in active_maintenance:
        if maint.server_id is None:  # Global maintenance
            return 'maintenance'

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex((BACKUP_SERVER['hostname'], BACKUP_SERVER['check_port']))
        sock.close()

        if result == 0:
            return 'operational'
        else:
            return 'major_outage'
    except socket.error:
        return 'major_outage'


def check_service_endpoint(service_key):
    """Check a service endpoint health"""
    # Check for manual override
    overrides = StatusOverride.get_active()
    if service_key in overrides:
        return overrides[service_key].display_status

    service = SERVICE_ENDPOINTS.get(service_key)
    if not service:
        return 'unknown'

    try:
        response = requests.get(
            service['url'],
            timeout=service.get('timeout', 5),
            allow_redirects=True
        )
        if response.status_code == 200:
            return 'operational'
        elif response.status_code < 500:
            return 'degraded'
        else:
            return 'major_outage'
    except requests.exceptions.Timeout:
        return 'degraded'
    except requests.exceptions.RequestException:
        return 'major_outage'


def get_all_statuses():
    """Get status of all monitored systems"""
    statuses = {
        'servers': [],
        'backup_server': None,
        'services': [],
        'overall': 'operational'
    }

    # Get web servers from database
    servers = Server.get_all_active()
    worst_status = 'operational'

    for server in servers:
        status = check_server_health(server)
        statuses['servers'].append({
            'id': server.id,
            'name': server.name,
            'status': status
        })
        worst_status = get_worse_status(worst_status, status)

    # Check backup server
    backup_status = check_backup_server()
    statuses['backup_server'] = {
        'name': BACKUP_SERVER['name'],
        'status': backup_status
    }
    worst_status = get_worse_status(worst_status, backup_status)

    # Check service endpoints
    for key, service in SERVICE_ENDPOINTS.items():
        status = check_service_endpoint(key)
        statuses['services'].append({
            'key': key,
            'name': service['name'],
            'status': status
        })
        worst_status = get_worse_status(worst_status, status)

    statuses['overall'] = worst_status
    statuses['last_updated'] = datetime.now().isoformat()

    return statuses


def get_worse_status(current, new):
    """Compare two statuses and return the worse one"""
    order = ['operational', 'maintenance', 'degraded', 'partial_outage', 'major_outage', 'unknown']
    current_idx = order.index(current) if current in order else 0
    new_idx = order.index(new) if new in order else 0
    return order[max(current_idx, new_idx)]


def get_status_display(status):
    """Get display text and color for a status"""
    displays = {
        'operational': {'text': 'Operational', 'color': 'green', 'icon': '●'},
        'degraded': {'text': 'Degraded Performance', 'color': 'yellow', 'icon': '●'},
        'partial_outage': {'text': 'Partial Outage', 'color': 'orange', 'icon': '●'},
        'major_outage': {'text': 'Major Outage', 'color': 'red', 'icon': '●'},
        'maintenance': {'text': 'Under Maintenance', 'color': 'blue', 'icon': '●'},
        'unknown': {'text': 'Unknown', 'color': 'gray', 'icon': '○'}
    }
    return displays.get(status, displays['unknown'])


def get_overall_message(status):
    """Get the overall status banner message"""
    messages = {
        'operational': 'All Systems Operational',
        'degraded': 'Degraded Performance',
        'partial_outage': 'Partial System Outage',
        'major_outage': 'Major System Outage',
        'maintenance': 'Scheduled Maintenance In Progress',
        'unknown': 'Status Unknown'
    }
    return messages.get(status, 'Status Unknown')
```

**Step 2: Commit**

```bash
git add webapp/status/health_checks.py
git commit -m "feat(status): add health check logic with fallback active checks"
```

---

## Task 4: Create Status Blueprint and Routes

**Files:**
- Create: `webapp/status/__init__.py`
- Create: `webapp/status/routes.py`

**Step 1: Write blueprint init**

```python
"""
Status Page Blueprint
Public status page showing system health
"""

from flask import Blueprint

status_bp = Blueprint('status', __name__, template_folder='../templates/status')

from . import routes
```

**Step 2: Write routes**

```python
"""
Status Page Routes
Handles public status page and API endpoints
"""

from flask import render_template, jsonify, request
from . import status_bp
from .health_checks import get_all_statuses, get_status_display, get_overall_message
from .models import StatusIncident, StatusMaintenance


@status_bp.route('/')
def index():
    """Main status page"""
    statuses = get_all_statuses()
    active_incidents = StatusIncident.get_active()
    recent_incidents = StatusIncident.get_recent(days=7)
    upcoming_maintenance = StatusMaintenance.get_upcoming(days=7)

    # Get incident updates
    incidents_with_updates = []
    for incident in active_incidents:
        incidents_with_updates.append({
            'incident': incident,
            'updates': incident.get_updates()
        })

    return render_template('status/index.html',
                          statuses=statuses,
                          active_incidents=incidents_with_updates,
                          recent_incidents=recent_incidents,
                          upcoming_maintenance=upcoming_maintenance,
                          get_status_display=get_status_display,
                          get_overall_message=get_overall_message)


@status_bp.route('/api/status')
def api_status():
    """JSON API for current status"""
    statuses = get_all_statuses()
    return jsonify(statuses)


@status_bp.route('/api/incidents')
def api_incidents():
    """JSON API for incidents"""
    active = StatusIncident.get_active()
    recent = StatusIncident.get_recent(days=7)

    return jsonify({
        'active': [{
            'id': i.id,
            'title': i.title,
            'status': i.status,
            'severity': i.severity,
            'started_at': i.started_at.isoformat() if i.started_at else None,
            'resolved_at': i.resolved_at.isoformat() if i.resolved_at else None
        } for i in active],
        'recent': [{
            'id': i.id,
            'title': i.title,
            'status': i.status,
            'severity': i.severity,
            'started_at': i.started_at.isoformat() if i.started_at else None,
            'resolved_at': i.resolved_at.isoformat() if i.resolved_at else None
        } for i in recent]
    })


@status_bp.route('/api/incidents/<int:incident_id>')
def api_incident_detail(incident_id):
    """JSON API for single incident with updates"""
    incident = StatusIncident.get_by_id(incident_id)
    if not incident:
        return jsonify({'error': 'Incident not found'}), 404

    updates = incident.get_updates()

    return jsonify({
        'id': incident.id,
        'title': incident.title,
        'status': incident.status,
        'severity': incident.severity,
        'started_at': incident.started_at.isoformat() if incident.started_at else None,
        'resolved_at': incident.resolved_at.isoformat() if incident.resolved_at else None,
        'updates': [{
            'id': u.id,
            'status': u.status,
            'message': u.message,
            'created_at': u.created_at.isoformat() if u.created_at else None
        } for u in updates]
    })
```

**Step 3: Commit**

```bash
git add webapp/status/__init__.py webapp/status/routes.py
git commit -m "feat(status): add status blueprint with routes and API endpoints"
```

---

## Task 5: Create Status Page Template

**Files:**
- Create: `webapp/templates/status/base_status.html`
- Create: `webapp/templates/status/index.html`

**Step 1: Write base template**

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}System Status{% endblock %} - ShopHosting.io</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=DM+Sans:opsz,wght@9..40,300;9..40,400;9..40,500;9..40,600;9..40,700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-deepest: #08080a;
            --bg-deep: #0d0d10;
            --bg-base: #111114;
            --bg-elevated: #18181c;
            --bg-surface: #1e1e24;
            --bg-hover: #26262e;

            --text-primary: #f4f4f6;
            --text-secondary: #a1a1aa;
            --text-tertiary: #71717a;
            --text-muted: #52525b;

            --accent-cyan: #00d4ff;
            --accent-blue: #0088ff;
            --accent-indigo: #5b5bd6;
            --gradient-primary: linear-gradient(135deg, #00d4ff 0%, #0088ff 50%, #5b5bd6 100%);

            --status-green: #22c55e;
            --status-yellow: #f59e0b;
            --status-orange: #f97316;
            --status-red: #ef4444;
            --status-blue: #3b82f6;
            --status-gray: #6b7280;

            --border-subtle: rgba(255, 255, 255, 0.06);
            --border-default: rgba(255, 255, 255, 0.1);

            --radius-sm: 6px;
            --radius-md: 10px;
            --radius-lg: 16px;
        }

        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: 'DM Sans', -apple-system, BlinkMacSystemFont, sans-serif;
            line-height: 1.6;
            color: var(--text-primary);
            background: var(--bg-deepest);
            min-height: 100vh;
            -webkit-font-smoothing: antialiased;
        }

        body::before {
            content: '';
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            height: 100vh;
            background:
                radial-gradient(ellipse 80% 50% at 50% -20%, rgba(0, 136, 255, 0.12) 0%, transparent 50%),
                radial-gradient(ellipse 60% 40% at 100% 0%, rgba(0, 212, 255, 0.08) 0%, transparent 40%);
            pointer-events: none;
            z-index: 0;
        }

        .container {
            max-width: 800px;
            margin: 0 auto;
            padding: 40px 20px;
            position: relative;
            z-index: 1;
        }

        .header {
            text-align: center;
            margin-bottom: 40px;
        }

        .logo {
            font-size: 28px;
            font-weight: 700;
            background: var(--gradient-primary);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            text-decoration: none;
            display: inline-block;
            margin-bottom: 8px;
        }

        .logo:hover {
            opacity: 0.9;
        }

        .tagline {
            color: var(--text-tertiary);
            font-size: 14px;
        }

        .overall-status {
            text-align: center;
            padding: 32px;
            background: var(--bg-elevated);
            border-radius: var(--radius-lg);
            border: 1px solid var(--border-subtle);
            margin-bottom: 32px;
        }

        .overall-status.operational { border-color: rgba(34, 197, 94, 0.3); }
        .overall-status.degraded { border-color: rgba(245, 158, 11, 0.3); }
        .overall-status.partial_outage { border-color: rgba(249, 115, 22, 0.3); }
        .overall-status.major_outage { border-color: rgba(239, 68, 68, 0.3); }
        .overall-status.maintenance { border-color: rgba(59, 130, 246, 0.3); }

        .overall-status h1 {
            font-size: 24px;
            font-weight: 600;
            margin-bottom: 8px;
        }

        .overall-status .last-updated {
            color: var(--text-tertiary);
            font-size: 13px;
        }

        .status-icon {
            display: inline-block;
            width: 12px;
            height: 12px;
            border-radius: 50%;
            margin-right: 8px;
        }

        .status-icon.green { background: var(--status-green); box-shadow: 0 0 8px var(--status-green); }
        .status-icon.yellow { background: var(--status-yellow); box-shadow: 0 0 8px var(--status-yellow); }
        .status-icon.orange { background: var(--status-orange); box-shadow: 0 0 8px var(--status-orange); }
        .status-icon.red { background: var(--status-red); box-shadow: 0 0 8px var(--status-red); }
        .status-icon.blue { background: var(--status-blue); box-shadow: 0 0 8px var(--status-blue); }
        .status-icon.gray { background: var(--status-gray); }

        .section {
            background: var(--bg-elevated);
            border-radius: var(--radius-lg);
            border: 1px solid var(--border-subtle);
            margin-bottom: 24px;
            overflow: hidden;
        }

        .section-header {
            padding: 16px 20px;
            border-bottom: 1px solid var(--border-subtle);
            font-weight: 600;
            font-size: 14px;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-secondary);
        }

        .status-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 16px 20px;
            border-bottom: 1px solid var(--border-subtle);
        }

        .status-row:last-child {
            border-bottom: none;
        }

        .status-row .name {
            font-weight: 500;
        }

        .status-row .status {
            display: flex;
            align-items: center;
            font-size: 14px;
            color: var(--text-secondary);
        }

        .server-group {
            padding-left: 24px;
            border-left: 2px solid var(--border-subtle);
            margin-left: 20px;
        }

        .incident-card {
            padding: 20px;
            border-bottom: 1px solid var(--border-subtle);
        }

        .incident-card:last-child {
            border-bottom: none;
        }

        .incident-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 12px;
        }

        .incident-title {
            font-weight: 600;
            font-size: 16px;
        }

        .severity-badge {
            padding: 4px 10px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 600;
            text-transform: uppercase;
        }

        .severity-badge.minor { background: rgba(245, 158, 11, 0.15); color: var(--status-yellow); }
        .severity-badge.major { background: rgba(249, 115, 22, 0.15); color: var(--status-orange); }
        .severity-badge.critical { background: rgba(239, 68, 68, 0.15); color: var(--status-red); }

        .incident-meta {
            font-size: 13px;
            color: var(--text-tertiary);
            margin-bottom: 16px;
        }

        .incident-timeline {
            border-left: 2px solid var(--border-default);
            padding-left: 16px;
            margin-left: 8px;
        }

        .timeline-item {
            position: relative;
            padding-bottom: 16px;
        }

        .timeline-item::before {
            content: '';
            position: absolute;
            left: -21px;
            top: 6px;
            width: 10px;
            height: 10px;
            border-radius: 50%;
            background: var(--bg-surface);
            border: 2px solid var(--accent-blue);
        }

        .timeline-item:last-child {
            padding-bottom: 0;
        }

        .timeline-status {
            font-size: 12px;
            font-weight: 600;
            text-transform: uppercase;
            color: var(--accent-blue);
            margin-bottom: 4px;
        }

        .timeline-message {
            font-size: 14px;
            color: var(--text-secondary);
            margin-bottom: 4px;
        }

        .timeline-time {
            font-size: 12px;
            color: var(--text-muted);
        }

        .maintenance-card {
            padding: 16px 20px;
            border-bottom: 1px solid var(--border-subtle);
        }

        .maintenance-card:last-child {
            border-bottom: none;
        }

        .maintenance-title {
            font-weight: 600;
            margin-bottom: 4px;
        }

        .maintenance-time {
            font-size: 13px;
            color: var(--text-tertiary);
            margin-bottom: 8px;
        }

        .maintenance-description {
            font-size: 14px;
            color: var(--text-secondary);
        }

        .empty-state {
            padding: 32px;
            text-align: center;
            color: var(--text-tertiary);
        }

        .footer {
            text-align: center;
            padding: 32px 0;
            color: var(--text-muted);
            font-size: 14px;
        }

        .footer a {
            color: var(--accent-blue);
            text-decoration: none;
        }

        .footer a:hover {
            text-decoration: underline;
        }

        .refresh-indicator {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            font-size: 12px;
            color: var(--text-muted);
        }

        .refresh-indicator .dot {
            width: 6px;
            height: 6px;
            border-radius: 50%;
            background: var(--status-green);
            animation: pulse 2s infinite;
        }

        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
    </style>
</head>
<body>
    {% block content %}{% endblock %}

    {% block scripts %}{% endblock %}
</body>
</html>
```

**Step 2: Write main index template**

```html
{% extends "status/base_status.html" %}

{% block content %}
<div class="container">
    <header class="header">
        <a href="https://shophosting.io" class="logo">ShopHosting.io</a>
        <p class="tagline">System Status</p>
    </header>

    <!-- Overall Status Banner -->
    <div class="overall-status {{ statuses.overall }}">
        <h1>
            <span class="status-icon {{ get_status_display(statuses.overall).color }}"></span>
            {{ get_overall_message(statuses.overall) }}
        </h1>
        <p class="last-updated">
            <span class="refresh-indicator">
                <span class="dot"></span>
                Auto-refreshing
            </span>
            &nbsp;·&nbsp; Last checked: <span id="last-updated">{{ statuses.last_updated }}</span>
        </p>
    </div>

    <!-- Active Incidents -->
    {% if active_incidents %}
    <section class="section">
        <div class="section-header">Active Incidents</div>
        {% for item in active_incidents %}
        <div class="incident-card">
            <div class="incident-header">
                <span class="incident-title">{{ item.incident.title }}</span>
                <span class="severity-badge {{ item.incident.severity }}">{{ item.incident.severity }}</span>
            </div>
            <div class="incident-meta">
                Started {{ item.incident.started_at.strftime('%B %d, %Y at %H:%M UTC') if item.incident.started_at else 'Unknown' }}
                · Status: {{ item.incident.status | capitalize }}
            </div>
            {% if item.updates %}
            <div class="incident-timeline">
                {% for update in item.updates %}
                <div class="timeline-item">
                    <div class="timeline-status">{{ update.status | capitalize }}</div>
                    <div class="timeline-message">{{ update.message }}</div>
                    <div class="timeline-time">{{ update.created_at.strftime('%B %d, %H:%M UTC') if update.created_at else '' }}</div>
                </div>
                {% endfor %}
            </div>
            {% endif %}
        </div>
        {% endfor %}
    </section>
    {% endif %}

    <!-- Current Status -->
    <section class="section">
        <div class="section-header">Current Status</div>

        <!-- Web Servers -->
        <div class="status-row">
            <span class="name">Web Servers</span>
            <span class="status">
                {% set worst_server = namespace(status='operational') %}
                {% for server in statuses.servers %}
                    {% if get_status_display(server.status).color in ['red', 'orange'] or (get_status_display(server.status).color == 'yellow' and worst_server.status == 'operational') %}
                        {% set worst_server.status = server.status %}
                    {% endif %}
                {% endfor %}
                <span class="status-icon {{ get_status_display(worst_server.status).color }}"></span>
                {{ get_status_display(worst_server.status).text }}
            </span>
        </div>
        <div class="server-group">
            {% for server in statuses.servers %}
            <div class="status-row">
                <span class="name">{{ server.name }}</span>
                <span class="status">
                    <span class="status-icon {{ get_status_display(server.status).color }}"></span>
                    {{ get_status_display(server.status).text }}
                </span>
            </div>
            {% endfor %}
        </div>

        <!-- Backup Server -->
        <div class="status-row">
            <span class="name">{{ statuses.backup_server.name }}</span>
            <span class="status">
                <span class="status-icon {{ get_status_display(statuses.backup_server.status).color }}"></span>
                {{ get_status_display(statuses.backup_server.status).text }}
            </span>
        </div>

        <!-- Services -->
        {% for service in statuses.services %}
        <div class="status-row">
            <span class="name">{{ service.name }}</span>
            <span class="status">
                <span class="status-icon {{ get_status_display(service.status).color }}"></span>
                {{ get_status_display(service.status).text }}
            </span>
        </div>
        {% endfor %}
    </section>

    <!-- Scheduled Maintenance -->
    {% if upcoming_maintenance %}
    <section class="section">
        <div class="section-header">Scheduled Maintenance</div>
        {% for maint in upcoming_maintenance %}
        <div class="maintenance-card">
            <div class="maintenance-title">{{ maint.title }}</div>
            <div class="maintenance-time">
                {{ maint.scheduled_start.strftime('%B %d, %Y %H:%M') }} - {{ maint.scheduled_end.strftime('%H:%M UTC') }}
            </div>
            {% if maint.description %}
            <div class="maintenance-description">{{ maint.description }}</div>
            {% endif %}
        </div>
        {% endfor %}
    </section>
    {% endif %}

    <!-- Recent Incidents (resolved) -->
    {% set resolved_incidents = recent_incidents | selectattr('status', 'equalto', 'resolved') | list %}
    {% if resolved_incidents %}
    <section class="section">
        <div class="section-header">Past Incidents (Last 7 Days)</div>
        {% for incident in resolved_incidents[:5] %}
        <div class="incident-card">
            <div class="incident-header">
                <span class="incident-title">{{ incident.title }}</span>
                <span class="severity-badge {{ incident.severity }}">{{ incident.severity }}</span>
            </div>
            <div class="incident-meta">
                {{ incident.started_at.strftime('%B %d, %Y') if incident.started_at else '' }}
                · Resolved in {{ ((incident.resolved_at - incident.started_at).total_seconds() / 60) | int }} minutes
            </div>
        </div>
        {% endfor %}
    </section>
    {% endif %}

    <footer class="footer">
        <p><a href="https://shophosting.io">← Back to ShopHosting.io</a></p>
        <p style="margin-top: 8px;">© 2026 ShopHosting.io. All rights reserved.</p>
    </footer>
</div>
{% endblock %}

{% block scripts %}
<script>
// Auto-refresh status every 60 seconds
setInterval(function() {
    fetch('/api/status')
        .then(response => response.json())
        .then(data => {
            document.getElementById('last-updated').textContent = data.last_updated;
            // For a full refresh, reload the page
            // For partial updates, you could update individual elements
        })
        .catch(err => console.error('Failed to refresh status:', err));
}, 60000);

// Format timestamps to local time
document.querySelectorAll('.timeline-time, .incident-meta, .maintenance-time').forEach(el => {
    // Could add local timezone conversion here
});
</script>
{% endblock %}
```

**Step 3: Commit**

```bash
git add webapp/templates/status/base_status.html webapp/templates/status/index.html
git commit -m "feat(status): add status page templates with dark theme"
```

---

## Task 6: Register Blueprint and Add Subdomain Routing

**Files:**
- Modify: `webapp/app.py`

**Step 1: Add imports and register blueprint**

Add after existing blueprint imports (around line 338):

```python
# Register status blueprint for subdomain
from status import status_bp

# Subdomain routing for status page
@app.before_request
def route_by_subdomain():
    """Route requests based on subdomain"""
    host = request.host.split(':')[0]  # Remove port if present

    if host.startswith('status.'):
        # Rewrite the request to use status blueprint
        if request.path == '/' or request.path.startswith('/api/'):
            request.url_rule = None  # Clear to allow status blueprint to handle

app.register_blueprint(status_bp, subdomain='status')

# Also register at /status path for direct access
app.register_blueprint(status_bp, url_prefix='/status')
```

**Step 2: Update app config for subdomains**

Add after `app = Flask(__name__)` (around line 160):

```python
# Enable subdomain matching
app.config['SERVER_NAME'] = os.getenv('SERVER_NAME', 'shophosting.io')
```

**Note:** SERVER_NAME may cause issues in development. Alternative approach - check host in routes:

Instead, modify the blueprint registration to work without SERVER_NAME:

```python
# Register status blueprint
from status import status_bp

# Mount at /status for direct access and testing
app.register_blueprint(status_bp, url_prefix='/status')
```

Then handle subdomain via nginx (recommended for production).

**Step 3: Commit**

```bash
git add webapp/app.py
git commit -m "feat(status): register status blueprint in main app"
```

---

## Task 7: Add Admin Routes for Incident Management

**Files:**
- Modify: `webapp/admin/routes.py`

**Step 1: Add imports at top of file**

```python
from status.models import StatusIncident, StatusIncidentUpdate, StatusMaintenance, StatusOverride
```

**Step 2: Add admin routes for status management**

Add at end of file:

```python
# =============================================================================
# Status Page Management
# =============================================================================

@admin_bp.route('/status')
@admin_login_required
def status_management():
    """Status page management dashboard"""
    active_incidents = StatusIncident.get_active()
    recent_incidents = StatusIncident.get_recent(days=30)
    upcoming_maintenance = StatusMaintenance.get_upcoming(days=30)
    overrides = StatusOverride.get_active()

    return render_template('admin/status_management.html',
                          active_incidents=active_incidents,
                          recent_incidents=recent_incidents,
                          upcoming_maintenance=upcoming_maintenance,
                          overrides=overrides)


@admin_bp.route('/status/incident/create', methods=['GET', 'POST'])
@admin_login_required
def create_incident():
    """Create a new incident"""
    if request.method == 'POST':
        incident = StatusIncident(
            server_id=request.form.get('server_id') or None,
            title=request.form['title'],
            status=request.form.get('status', 'investigating'),
            severity=request.form.get('severity', 'minor'),
            is_auto_detected=False
        )
        incident.save()

        # Add initial update
        message = request.form.get('message', 'We are investigating this issue.')
        incident.add_update(message, incident.status, session.get('admin_id'))

        flash('Incident created successfully', 'success')
        return redirect(url_for('admin.status_management'))

    servers = Server.get_all()
    return render_template('admin/incident_form.html', servers=servers, incident=None)


@admin_bp.route('/status/incident/<int:incident_id>/update', methods=['POST'])
@admin_login_required
def update_incident(incident_id):
    """Add update to an incident"""
    incident = StatusIncident.get_by_id(incident_id)
    if not incident:
        flash('Incident not found', 'error')
        return redirect(url_for('admin.status_management'))

    message = request.form['message']
    new_status = request.form.get('status')

    if new_status == 'resolved':
        incident.resolve()
    elif new_status:
        incident.status = new_status
        incident.save()

    incident.add_update(message, new_status or incident.status, session.get('admin_id'))

    flash('Incident updated', 'success')
    return redirect(url_for('admin.status_management'))


@admin_bp.route('/status/maintenance/create', methods=['GET', 'POST'])
@admin_login_required
def create_maintenance():
    """Create scheduled maintenance"""
    if request.method == 'POST':
        maintenance = StatusMaintenance(
            server_id=request.form.get('server_id') or None,
            title=request.form['title'],
            description=request.form.get('description'),
            scheduled_start=request.form['scheduled_start'],
            scheduled_end=request.form['scheduled_end'],
            created_by=session.get('admin_id')
        )
        maintenance.save()

        flash('Maintenance window scheduled', 'success')
        return redirect(url_for('admin.status_management'))

    servers = Server.get_all()
    return render_template('admin/maintenance_form.html', servers=servers, maintenance=None)


@admin_bp.route('/status/override', methods=['POST'])
@admin_login_required
def set_status_override():
    """Set a manual status override"""
    service_name = request.form['service_name']
    display_status = request.form['display_status']
    message = request.form.get('message')
    expires_hours = request.form.get('expires_hours')

    StatusOverride.set_override(
        service_name=service_name,
        display_status=display_status,
        message=message,
        created_by=session.get('admin_id'),
        expires_hours=int(expires_hours) if expires_hours else None
    )

    flash(f'Status override set for {service_name}', 'success')
    return redirect(url_for('admin.status_management'))


@admin_bp.route('/status/override/<service_name>/clear', methods=['POST'])
@admin_login_required
def clear_status_override(service_name):
    """Clear a status override"""
    StatusOverride.clear_override(service_name)
    flash(f'Status override cleared for {service_name}', 'success')
    return redirect(url_for('admin.status_management'))
```

**Step 3: Commit**

```bash
git add webapp/admin/routes.py
git commit -m "feat(status): add admin routes for incident and maintenance management"
```

---

## Task 8: Create Admin Templates for Status Management

**Files:**
- Create: `webapp/templates/admin/status_management.html`
- Create: `webapp/templates/admin/incident_form.html`
- Create: `webapp/templates/admin/maintenance_form.html`

**Step 1: Write status management template**

```html
{% extends "admin/base_admin.html" %}

{% block title %}Status Management{% endblock %}

{% block content %}
<div class="page-header">
    <h1>Status Page Management</h1>
    <div class="header-actions">
        <a href="{{ url_for('admin.create_incident') }}" class="btn btn-primary">Report Incident</a>
        <a href="{{ url_for('admin.create_maintenance') }}" class="btn btn-secondary">Schedule Maintenance</a>
    </div>
</div>

<!-- Active Incidents -->
<div class="card">
    <div class="card-header">
        <h2>Active Incidents</h2>
    </div>
    <div class="card-body">
        {% if active_incidents %}
        <table class="table">
            <thead>
                <tr>
                    <th>Title</th>
                    <th>Severity</th>
                    <th>Status</th>
                    <th>Started</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody>
                {% for incident in active_incidents %}
                <tr>
                    <td>{{ incident.title }}</td>
                    <td><span class="badge badge-{{ incident.severity }}">{{ incident.severity }}</span></td>
                    <td>{{ incident.status | capitalize }}</td>
                    <td>{{ incident.started_at.strftime('%Y-%m-%d %H:%M') if incident.started_at else '-' }}</td>
                    <td>
                        <button class="btn btn-sm btn-secondary" onclick="showUpdateModal({{ incident.id }}, '{{ incident.status }}')">
                            Update
                        </button>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        {% else %}
        <p class="text-muted">No active incidents</p>
        {% endif %}
    </div>
</div>

<!-- Upcoming Maintenance -->
<div class="card">
    <div class="card-header">
        <h2>Scheduled Maintenance</h2>
    </div>
    <div class="card-body">
        {% if upcoming_maintenance %}
        <table class="table">
            <thead>
                <tr>
                    <th>Title</th>
                    <th>Scheduled</th>
                    <th>Status</th>
                </tr>
            </thead>
            <tbody>
                {% for maint in upcoming_maintenance %}
                <tr>
                    <td>{{ maint.title }}</td>
                    <td>{{ maint.scheduled_start.strftime('%Y-%m-%d %H:%M') }} - {{ maint.scheduled_end.strftime('%H:%M') }}</td>
                    <td>{{ maint.status | capitalize }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        {% else %}
        <p class="text-muted">No scheduled maintenance</p>
        {% endif %}
    </div>
</div>

<!-- Status Overrides -->
<div class="card">
    <div class="card-header">
        <h2>Manual Status Overrides</h2>
    </div>
    <div class="card-body">
        <form method="POST" action="{{ url_for('admin.set_status_override') }}" class="form-inline mb-3">
            <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
            <input type="text" name="service_name" placeholder="Service name" class="form-control mr-2" required>
            <select name="display_status" class="form-control mr-2">
                <option value="operational">Operational</option>
                <option value="degraded">Degraded</option>
                <option value="partial_outage">Partial Outage</option>
                <option value="major_outage">Major Outage</option>
                <option value="maintenance">Maintenance</option>
            </select>
            <input type="text" name="message" placeholder="Message (optional)" class="form-control mr-2">
            <input type="number" name="expires_hours" placeholder="Expires in hours" class="form-control mr-2" style="width: 150px;">
            <button type="submit" class="btn btn-primary">Set Override</button>
        </form>

        {% if overrides %}
        <table class="table">
            <thead>
                <tr>
                    <th>Service</th>
                    <th>Status</th>
                    <th>Message</th>
                    <th>Expires</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody>
                {% for name, override in overrides.items() %}
                <tr>
                    <td>{{ name }}</td>
                    <td>{{ override.display_status }}</td>
                    <td>{{ override.message or '-' }}</td>
                    <td>{{ override.expires_at.strftime('%Y-%m-%d %H:%M') if override.expires_at else 'Never' }}</td>
                    <td>
                        <form method="POST" action="{{ url_for('admin.clear_status_override', service_name=name) }}" style="display: inline;">
                            <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                            <button type="submit" class="btn btn-sm btn-danger">Clear</button>
                        </form>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        {% else %}
        <p class="text-muted">No active overrides</p>
        {% endif %}
    </div>
</div>

<!-- Update Incident Modal -->
<div id="updateModal" class="modal" style="display: none;">
    <div class="modal-content">
        <h3>Update Incident</h3>
        <form method="POST" id="updateForm">
            <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
            <div class="form-group">
                <label>Status</label>
                <select name="status" class="form-control" id="updateStatus">
                    <option value="investigating">Investigating</option>
                    <option value="identified">Identified</option>
                    <option value="monitoring">Monitoring</option>
                    <option value="resolved">Resolved</option>
                </select>
            </div>
            <div class="form-group">
                <label>Update Message</label>
                <textarea name="message" class="form-control" rows="3" required></textarea>
            </div>
            <button type="submit" class="btn btn-primary">Post Update</button>
            <button type="button" class="btn btn-secondary" onclick="hideUpdateModal()">Cancel</button>
        </form>
    </div>
</div>

<script>
function showUpdateModal(incidentId, currentStatus) {
    document.getElementById('updateForm').action = '/admin/status/incident/' + incidentId + '/update';
    document.getElementById('updateStatus').value = currentStatus;
    document.getElementById('updateModal').style.display = 'flex';
}

function hideUpdateModal() {
    document.getElementById('updateModal').style.display = 'none';
}
</script>

<style>
.modal {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background: rgba(0,0,0,0.5);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
}
.modal-content {
    background: var(--bg-elevated);
    padding: 24px;
    border-radius: 8px;
    min-width: 400px;
}
.badge-minor { background: #f59e0b; color: #000; }
.badge-major { background: #f97316; color: #fff; }
.badge-critical { background: #ef4444; color: #fff; }
</style>
{% endblock %}
```

**Step 2: Write incident form template**

```html
{% extends "admin/base_admin.html" %}

{% block title %}{% if incident %}Edit{% else %}Create{% endif %} Incident{% endblock %}

{% block content %}
<div class="page-header">
    <h1>{% if incident %}Edit{% else %}Report{% endif %} Incident</h1>
</div>

<div class="card">
    <div class="card-body">
        <form method="POST">
            <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">

            <div class="form-group">
                <label>Title</label>
                <input type="text" name="title" class="form-control" required
                       value="{{ incident.title if incident else '' }}"
                       placeholder="Brief description of the issue">
            </div>

            <div class="form-group">
                <label>Affected Server (optional)</label>
                <select name="server_id" class="form-control">
                    <option value="">All Systems / Global</option>
                    {% for server in servers %}
                    <option value="{{ server.id }}" {% if incident and incident.server_id == server.id %}selected{% endif %}>
                        {{ server.name }}
                    </option>
                    {% endfor %}
                </select>
            </div>

            <div class="form-row">
                <div class="form-group col-md-6">
                    <label>Severity</label>
                    <select name="severity" class="form-control">
                        <option value="minor">Minor - Performance degradation</option>
                        <option value="major">Major - Partial outage</option>
                        <option value="critical">Critical - Full outage</option>
                    </select>
                </div>
                <div class="form-group col-md-6">
                    <label>Status</label>
                    <select name="status" class="form-control">
                        <option value="investigating">Investigating</option>
                        <option value="identified">Identified</option>
                        <option value="monitoring">Monitoring</option>
                    </select>
                </div>
            </div>

            <div class="form-group">
                <label>Initial Update Message</label>
                <textarea name="message" class="form-control" rows="3"
                          placeholder="We are currently investigating this issue..."></textarea>
            </div>

            <button type="submit" class="btn btn-primary">Create Incident</button>
            <a href="{{ url_for('admin.status_management') }}" class="btn btn-secondary">Cancel</a>
        </form>
    </div>
</div>
{% endblock %}
```

**Step 3: Write maintenance form template**

```html
{% extends "admin/base_admin.html" %}

{% block title %}Schedule Maintenance{% endblock %}

{% block content %}
<div class="page-header">
    <h1>Schedule Maintenance</h1>
</div>

<div class="card">
    <div class="card-body">
        <form method="POST">
            <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">

            <div class="form-group">
                <label>Title</label>
                <input type="text" name="title" class="form-control" required
                       placeholder="Scheduled server maintenance">
            </div>

            <div class="form-group">
                <label>Affected Server (optional)</label>
                <select name="server_id" class="form-control">
                    <option value="">All Systems</option>
                    {% for server in servers %}
                    <option value="{{ server.id }}">{{ server.name }}</option>
                    {% endfor %}
                </select>
            </div>

            <div class="form-row">
                <div class="form-group col-md-6">
                    <label>Start Time</label>
                    <input type="datetime-local" name="scheduled_start" class="form-control" required>
                </div>
                <div class="form-group col-md-6">
                    <label>End Time</label>
                    <input type="datetime-local" name="scheduled_end" class="form-control" required>
                </div>
            </div>

            <div class="form-group">
                <label>Description</label>
                <textarea name="description" class="form-control" rows="3"
                          placeholder="Details about the maintenance..."></textarea>
            </div>

            <button type="submit" class="btn btn-primary">Schedule Maintenance</button>
            <a href="{{ url_for('admin.status_management') }}" class="btn btn-secondary">Cancel</a>
        </form>
    </div>
</div>
{% endblock %}
```

**Step 4: Commit**

```bash
git add webapp/templates/admin/status_management.html webapp/templates/admin/incident_form.html webapp/templates/admin/maintenance_form.html
git commit -m "feat(status): add admin templates for incident and maintenance management"
```

---

## Task 9: Create Nginx Configuration

**Files:**
- Create: `webapp/nginx-status.conf`

**Step 1: Write nginx config**

```nginx
# Status page subdomain configuration
# Add this to /etc/nginx/sites-available/ and symlink to sites-enabled

server {
    listen 80;
    server_name status.shophosting.io;
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name status.shophosting.io;

    # SSL configuration (use same certs as main site)
    ssl_certificate /etc/letsencrypt/live/shophosting.io/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/shophosting.io/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;

    # Proxy to Flask app with status prefix
    location / {
        proxy_pass http://127.0.0.1:5000/status;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # API routes
    location /api/ {
        proxy_pass http://127.0.0.1:5000/status/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

**Step 2: Commit**

```bash
git add webapp/nginx-status.conf
git commit -m "feat(status): add nginx configuration for status subdomain"
```

---

## Task 10: Update README

**Files:**
- Modify: `README.md`

**Step 1: Add status page section to README**

Add section after existing documentation:

```markdown
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
sudo ln -s /etc/nginx/sites-available/status.shophosting.io /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

### DNS Setup

Add DNS record:
```
status.shophosting.io  A  147.135.8.170
```
```

**Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add status page documentation to README"
```

---

## Task 11: Final Testing and Verification

**Step 1: Run migration**

```bash
mysql -u root -p shophosting < migrations/013_add_status_page_tables.sql
```

**Step 2: Test status page locally**

```bash
curl http://localhost:5000/status/
curl http://localhost:5000/status/api/status
```

**Step 3: Deploy nginx config**

```bash
sudo cp webapp/nginx-status.conf /etc/nginx/sites-available/status.shophosting.io
sudo ln -s /etc/nginx/sites-available/status.shophosting.io /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

**Step 4: Test production**

```bash
curl https://status.shophosting.io/
curl https://status.shophosting.io/api/status
```

**Step 5: Final commit**

```bash
git add -A
git commit -m "feat(status): complete status page implementation"
```

---

## Summary

This plan creates a complete public status page with:
1. Database tables for incidents, maintenance, and overrides
2. Models for all status-related data
3. Health check logic using existing monitoring + fallback checks
4. Public-facing status page with dark theme matching main site
5. Admin panel for incident/maintenance management
6. Nginx configuration for subdomain routing
7. Updated README documentation
