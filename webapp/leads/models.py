"""
Lead Funnel Models
Handles database operations for site scans and migration preview requests
"""

from datetime import datetime, timedelta
from models import get_db_connection


class SiteScan:
    """Model for site performance scan results"""

    def __init__(self, id=None, url=None, email=None, performance_score=None,
                 load_time_ms=None, ttfb_ms=None, pagespeed_data=None,
                 custom_probe_data=None, estimated_revenue_loss=None,
                 ip_address=None, converted_to_lead_at=None, created_at=None):
        self.id = id
        self.url = url
        self.email = email
        self.performance_score = performance_score
        self.load_time_ms = load_time_ms
        self.ttfb_ms = ttfb_ms
        self.pagespeed_data = pagespeed_data
        self.custom_probe_data = custom_probe_data
        self.estimated_revenue_loss = estimated_revenue_loss
        self.ip_address = ip_address
        self.converted_to_lead_at = converted_to_lead_at
        self.created_at = created_at or datetime.now()

    # =========================================================================
    # Query Methods
    # =========================================================================

    @staticmethod
    def get_by_id(scan_id):
        """Get site scan by ID"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("SELECT * FROM site_scans WHERE id = %s", (scan_id,))
            row = cursor.fetchone()

            if row:
                return SiteScan(**row)
            return None

        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_by_email(email):
        """Get all site scans for an email address"""
        conn = get_db_connection(read_only=True)
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute(
                "SELECT * FROM site_scans WHERE email = %s ORDER BY created_at DESC",
                (email,)
            )
            rows = cursor.fetchall()
            return [SiteScan(**row) for row in rows]

        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_recent(limit=50):
        """Get recent site scans"""
        conn = get_db_connection(read_only=True)
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute(
                "SELECT * FROM site_scans ORDER BY created_at DESC LIMIT %s",
                (limit,)
            )
            rows = cursor.fetchall()
            return [SiteScan(**row) for row in rows]

        finally:
            cursor.close()
            conn.close()

    # =========================================================================
    # Create and Update Methods
    # =========================================================================

    @staticmethod
    def create(url, ip_address):
        """Create a new site scan record"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO site_scans (url, ip_address, created_at)
                VALUES (%s, %s, NOW())
            """, (url, ip_address))
            conn.commit()

            scan_id = cursor.lastrowid
            return SiteScan.get_by_id(scan_id)

        finally:
            cursor.close()
            conn.close()

    def update_results(self, performance_score, load_time_ms, ttfb_ms,
                       pagespeed_data, custom_probe_data, estimated_revenue_loss):
        """Update scan with results from PageSpeed API and custom probes"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                UPDATE site_scans
                SET performance_score = %s,
                    load_time_ms = %s,
                    ttfb_ms = %s,
                    pagespeed_data = %s,
                    custom_probe_data = %s,
                    estimated_revenue_loss = %s
                WHERE id = %s
            """, (
                performance_score,
                load_time_ms,
                ttfb_ms,
                pagespeed_data,
                custom_probe_data,
                estimated_revenue_loss,
                self.id
            ))
            conn.commit()

            # Update local object
            self.performance_score = performance_score
            self.load_time_ms = load_time_ms
            self.ttfb_ms = ttfb_ms
            self.pagespeed_data = pagespeed_data
            self.custom_probe_data = custom_probe_data
            self.estimated_revenue_loss = estimated_revenue_loss

            return True

        finally:
            cursor.close()
            conn.close()

    def set_email(self, email):
        """Set email and mark as converted to lead"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                UPDATE site_scans
                SET email = %s, converted_to_lead_at = NOW()
                WHERE id = %s
            """, (email, self.id))
            conn.commit()

            # Update local object
            self.email = email
            self.converted_to_lead_at = datetime.now()

            return True

        finally:
            cursor.close()
            conn.close()

    # =========================================================================
    # Analytics Methods
    # =========================================================================

    @staticmethod
    def get_stats():
        """Get analytics statistics for scans"""
        conn = get_db_connection(read_only=True)
        cursor = conn.cursor(dictionary=True)

        try:
            stats = {}

            # Total scans
            cursor.execute("SELECT COUNT(*) as total FROM site_scans")
            stats['total_scans'] = cursor.fetchone()['total']

            # Scans with email (converted to leads)
            cursor.execute(
                "SELECT COUNT(*) as total FROM site_scans WHERE email IS NOT NULL"
            )
            stats['total_leads'] = cursor.fetchone()['total']

            # Conversion rate
            if stats['total_scans'] > 0:
                stats['conversion_rate'] = round(
                    (stats['total_leads'] / stats['total_scans']) * 100, 2
                )
            else:
                stats['conversion_rate'] = 0

            # Scans today
            cursor.execute("""
                SELECT COUNT(*) as total FROM site_scans
                WHERE DATE(created_at) = CURDATE()
            """)
            stats['scans_today'] = cursor.fetchone()['total']

            # Scans this week
            cursor.execute("""
                SELECT COUNT(*) as total FROM site_scans
                WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)
            """)
            stats['scans_this_week'] = cursor.fetchone()['total']

            # Leads today
            cursor.execute("""
                SELECT COUNT(*) as total FROM site_scans
                WHERE email IS NOT NULL AND DATE(converted_to_lead_at) = CURDATE()
            """)
            stats['leads_today'] = cursor.fetchone()['total']

            # Scans per day (last 30 days)
            cursor.execute("""
                SELECT DATE(created_at) as date, COUNT(*) as count
                FROM site_scans
                WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
                GROUP BY DATE(created_at)
                ORDER BY date DESC
            """)
            stats['scans_per_day'] = cursor.fetchall()

            # Average performance score
            cursor.execute("""
                SELECT AVG(performance_score) as avg_score
                FROM site_scans
                WHERE performance_score IS NOT NULL
            """)
            result = cursor.fetchone()
            stats['avg_performance_score'] = round(result['avg_score'], 1) if result['avg_score'] else None

            return stats

        finally:
            cursor.close()
            conn.close()


class MigrationPreviewRequest:
    """Model for migration preview requests from leads"""

    STATUSES = ['pending', 'contacted', 'migrating', 'completed', 'rejected']
    PLATFORMS = ['woocommerce', 'magento', 'unknown']

    def __init__(self, id=None, site_scan_id=None, email=None, store_url=None,
                 store_platform=None, monthly_revenue=None, current_host=None,
                 status='pending', notes=None, assigned_admin_id=None,
                 created_at=None, updated_at=None):
        self.id = id
        self.site_scan_id = site_scan_id
        self.email = email
        self.store_url = store_url
        self.store_platform = store_platform or 'unknown'
        self.monthly_revenue = monthly_revenue
        self.current_host = current_host
        self.status = status
        self.notes = notes
        self.assigned_admin_id = assigned_admin_id
        self.created_at = created_at or datetime.now()
        self.updated_at = updated_at or datetime.now()

    # =========================================================================
    # Query Methods
    # =========================================================================

    @staticmethod
    def get_by_id(request_id):
        """Get migration preview request by ID"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute(
                "SELECT * FROM migration_preview_requests WHERE id = %s",
                (request_id,)
            )
            row = cursor.fetchone()

            if row:
                return MigrationPreviewRequest(**row)
            return None

        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_by_scan_id(site_scan_id):
        """Get migration preview request by site scan ID"""
        conn = get_db_connection(read_only=True)
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute(
                "SELECT * FROM migration_preview_requests WHERE site_scan_id = %s",
                (site_scan_id,)
            )
            row = cursor.fetchone()

            if row:
                return MigrationPreviewRequest(**row)
            return None

        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_all(status_filter=None, limit=50):
        """Get all migration preview requests with optional status filter"""
        conn = get_db_connection(read_only=True)
        cursor = conn.cursor(dictionary=True)

        try:
            if status_filter:
                cursor.execute("""
                    SELECT * FROM migration_preview_requests
                    WHERE status = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                """, (status_filter, limit))
            else:
                cursor.execute("""
                    SELECT * FROM migration_preview_requests
                    ORDER BY created_at DESC
                    LIMIT %s
                """, (limit,))

            rows = cursor.fetchall()
            return [MigrationPreviewRequest(**row) for row in rows]

        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_pending():
        """Get all pending migration preview requests"""
        return MigrationPreviewRequest.get_all(status_filter='pending')

    # =========================================================================
    # Create and Update Methods
    # =========================================================================

    @staticmethod
    def create(site_scan_id, email, store_url, store_platform, monthly_revenue, current_host):
        """Create a new migration preview request"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            # Validate platform
            platform = store_platform if store_platform in MigrationPreviewRequest.PLATFORMS else 'unknown'

            cursor.execute("""
                INSERT INTO migration_preview_requests
                (site_scan_id, email, store_url, store_platform, monthly_revenue,
                 current_host, status, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, 'pending', NOW(), NOW())
            """, (site_scan_id, email, store_url, platform, monthly_revenue, current_host))
            conn.commit()

            request_id = cursor.lastrowid
            return MigrationPreviewRequest.get_by_id(request_id)

        finally:
            cursor.close()
            conn.close()

    def update_status(self, status):
        """Update the status of the migration preview request"""
        if status not in MigrationPreviewRequest.STATUSES:
            raise ValueError(f"Invalid status: {status}. Must be one of {MigrationPreviewRequest.STATUSES}")

        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                UPDATE migration_preview_requests
                SET status = %s, updated_at = NOW()
                WHERE id = %s
            """, (status, self.id))
            conn.commit()

            # Update local object
            self.status = status
            self.updated_at = datetime.now()

            return True

        finally:
            cursor.close()
            conn.close()

    def add_note(self, note):
        """Add a note to the migration preview request (appends to existing notes)"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            # Get current notes and append
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')
            new_note = f"[{timestamp}] {note}"

            if self.notes:
                updated_notes = f"{self.notes}\n\n{new_note}"
            else:
                updated_notes = new_note

            cursor.execute("""
                UPDATE migration_preview_requests
                SET notes = %s, updated_at = NOW()
                WHERE id = %s
            """, (updated_notes, self.id))
            conn.commit()

            # Update local object
            self.notes = updated_notes
            self.updated_at = datetime.now()

            return True

        finally:
            cursor.close()
            conn.close()

    def assign_admin(self, admin_id):
        """Assign an admin to handle this migration preview request"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                UPDATE migration_preview_requests
                SET assigned_admin_id = %s, updated_at = NOW()
                WHERE id = %s
            """, (admin_id, self.id))
            conn.commit()

            # Update local object
            self.assigned_admin_id = admin_id
            self.updated_at = datetime.now()

            return True

        finally:
            cursor.close()
            conn.close()

    # =========================================================================
    # Analytics Methods
    # =========================================================================

    @staticmethod
    def get_stats():
        """Get analytics statistics for migration preview requests"""
        conn = get_db_connection(read_only=True)
        cursor = conn.cursor(dictionary=True)

        try:
            stats = {}

            # Total requests
            cursor.execute("SELECT COUNT(*) as total FROM migration_preview_requests")
            stats['total_requests'] = cursor.fetchone()['total']

            # Requests by status
            cursor.execute("""
                SELECT status, COUNT(*) as count
                FROM migration_preview_requests
                GROUP BY status
            """)
            stats['by_status'] = {row['status']: row['count'] for row in cursor.fetchall()}

            # Pending count
            stats['pending_count'] = stats['by_status'].get('pending', 0)

            # Requests today
            cursor.execute("""
                SELECT COUNT(*) as total FROM migration_preview_requests
                WHERE DATE(created_at) = CURDATE()
            """)
            stats['requests_today'] = cursor.fetchone()['total']

            # Completion rate
            completed = stats['by_status'].get('completed', 0)
            if stats['total_requests'] > 0:
                stats['completion_rate'] = round(
                    (completed / stats['total_requests']) * 100, 2
                )
            else:
                stats['completion_rate'] = 0

            return stats

        finally:
            cursor.close()
            conn.close()
