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
        """Insert or update incident in database"""
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
        """Mark incident as resolved with timestamp"""
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
        """Get active incidents for a specific server or global incidents"""
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
                 created_by=None, created_at=None, admin_name=None):
        self.id = id
        self.incident_id = incident_id
        self.status = status
        self.message = message
        self.created_by = created_by
        self.created_at = created_at
        self.admin_name = admin_name

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
        """Get all updates for an incident with admin name joined"""
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
            return [StatusIncidentUpdate(**row) for row in cursor.fetchall()]
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
        """Insert or update maintenance window in database"""
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
        """Get all active (non-expired) overrides as dict keyed by service_name"""
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
