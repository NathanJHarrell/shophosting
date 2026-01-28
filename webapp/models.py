"""
ShopHosting.io - Database Models
Handles database connections and customer data operations
"""

import os
import mysql.connector
from mysql.connector import pooling
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

# Database connection pool
db_pool = None


def init_db_pool():
    """Initialize database connection pool"""
    global db_pool

    # Validate required database configuration
    db_password = os.getenv('DB_PASSWORD')
    if not db_password:
        raise RuntimeError(
            "CRITICAL: DB_PASSWORD environment variable is required. "
            "Please set it in /opt/shophosting/.env"
        )

    db_config = {
        'host': os.getenv('DB_HOST', 'localhost'),
        'user': os.getenv('DB_USER', 'shophosting_app'),
        'password': db_password,
        'database': os.getenv('DB_NAME', 'shophosting_db'),
        'pool_name': 'shophosting_pool',
        'pool_size': int(os.getenv('DB_POOL_SIZE', '5'))
    }

    db_pool = pooling.MySQLConnectionPool(**db_config)
    return db_pool


def get_db_connection():
    """Get a connection from the pool"""
    global db_pool
    if db_pool is None:
        init_db_pool()
    return db_pool.get_connection()


# =============================================================================
# Port Manager
# =============================================================================

class PortManager:
    """Manages port allocation for customer containers"""

    PORT_RANGE_START = 8001
    PORT_RANGE_END = 8100

    @staticmethod
    def get_next_available_port():
        """Get the next available port for a new customer"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            # Get all currently used ports
            cursor.execute("SELECT web_port FROM customers WHERE web_port IS NOT NULL")
            used_ports = {row[0] for row in cursor.fetchall()}

            # Find first available port in range
            for port in range(PortManager.PORT_RANGE_START, PortManager.PORT_RANGE_END + 1):
                if port not in used_ports:
                    return port

            return None  # No ports available

        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def is_port_available(port):
        """Check if a specific port is available"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("SELECT COUNT(*) FROM customers WHERE web_port = %s", (port,))
            count = cursor.fetchone()[0]
            return count == 0
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_port_usage():
        """Get current port usage statistics"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("SELECT web_port FROM customers WHERE web_port IS NOT NULL")
            used_ports = [row[0] for row in cursor.fetchall()]

            total_ports = PortManager.PORT_RANGE_END - PortManager.PORT_RANGE_START + 1
            used_count = len(used_ports)

            return {
                'total': total_ports,
                'used': used_count,
                'available': total_ports - used_count,
                'used_ports': used_ports
            }
        finally:
            cursor.close()
            conn.close()


# =============================================================================
# Customer Model
# =============================================================================

class Customer:
    """Customer model for database operations"""

    def __init__(self, id=None, email=None, password_hash=None, company_name=None,
                 domain=None, platform=None, status='pending', web_port=None,
                 server_id=None, db_name=None, db_user=None, db_password=None,
                 admin_user=None, admin_password=None, error_message=None,
                 stripe_customer_id=None, plan_id=None, staging_count=None,
                 created_at=None, updated_at=None):
        self.id = id
        self.email = email
        self.password_hash = password_hash
        self.company_name = company_name
        self.domain = domain
        self.platform = platform
        self.status = status
        self.web_port = web_port
        self.server_id = server_id
        self.db_name = db_name
        self.db_user = db_user
        self.db_password = db_password
        self.admin_user = admin_user
        self.admin_password = admin_password
        self.error_message = error_message
        self.stripe_customer_id = stripe_customer_id
        self.plan_id = plan_id
        self.staging_count = staging_count or 0
        self.created_at = created_at or datetime.now()
        self.updated_at = updated_at or datetime.now()

    # =========================================================================
    # Password Methods
    # =========================================================================

    def set_password(self, password):
        """Hash and set password"""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        """Verify password"""
        return check_password_hash(self.password_hash, password)

    # =========================================================================
    # Flask-Login Required Properties
    # =========================================================================

    @property
    def is_authenticated(self):
        return True

    @property
    def is_active(self):
        return self.status != 'suspended'

    @property
    def is_anonymous(self):
        return False

    def get_id(self):
        return str(self.id)

    # =========================================================================
    # Database Operations
    # =========================================================================

    def save(self):
        """Save customer to database (insert or update)"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            if self.id is None:
                # Insert new customer
                cursor.execute("""
                    INSERT INTO customers
                    (email, password_hash, company_name, domain, platform, status, web_port,
                     server_id, stripe_customer_id, plan_id, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    self.email, self.password_hash, self.company_name,
                    self.domain, self.platform, self.status, self.web_port,
                    self.server_id, self.stripe_customer_id, self.plan_id,
                    self.created_at, self.updated_at
                ))
                self.id = cursor.lastrowid
            else:
                # Update existing customer
                cursor.execute("""
                    UPDATE customers SET
                        email = %s, password_hash = %s, company_name = %s,
                        domain = %s, platform = %s, status = %s, web_port = %s,
                        server_id = %s, stripe_customer_id = %s, plan_id = %s, updated_at = %s
                    WHERE id = %s
                """, (
                    self.email, self.password_hash, self.company_name,
                    self.domain, self.platform, self.status, self.web_port,
                    self.server_id, self.stripe_customer_id, self.plan_id,
                    datetime.now(), self.id
                ))

            conn.commit()
            return self

        finally:
            cursor.close()
            conn.close()

    def delete(self):
        """Delete customer from database"""
        if self.id is None:
            return False
            
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("DELETE FROM customers WHERE id = %s", (self.id,))
            conn.commit()
            return cursor.rowcount > 0
        finally:
            cursor.close()
            conn.close()

    # =========================================================================
    # Static Query Methods
    # =========================================================================

    @staticmethod
    def get_by_id(customer_id):
        """Get customer by ID"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("SELECT * FROM customers WHERE id = %s", (customer_id,))
            row = cursor.fetchone()

            if row:
                return Customer(**row)
            return None

        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_by_email(email):
        """Get customer by email"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("SELECT * FROM customers WHERE email = %s", (email,))
            row = cursor.fetchone()

            if row:
                return Customer(**row)
            return None

        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_by_domain(domain):
        """Get customer by domain"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("SELECT * FROM customers WHERE domain = %s", (domain,))
            row = cursor.fetchone()

            if row:
                return Customer(**row)
            return None

        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_all():
        """Get all customers"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("SELECT * FROM customers ORDER BY created_at DESC")
            rows = cursor.fetchall()
            return [Customer(**row) for row in rows]

        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_by_status(status):
        """Get all customers with a specific status"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("SELECT * FROM customers WHERE status = %s ORDER BY created_at DESC", (status,))
            rows = cursor.fetchall()
            return [Customer(**row) for row in rows]

        finally:
            cursor.close()
            conn.close()

    # =========================================================================
    # Validation Methods
    # =========================================================================

    @staticmethod
    def email_exists(email):
        """Check if email already exists"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("SELECT COUNT(*) FROM customers WHERE email = %s", (email,))
            count = cursor.fetchone()[0]
            return count > 0

        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def domain_exists(domain):
        """Check if domain already exists"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("SELECT COUNT(*) FROM customers WHERE domain = %s", (domain,))
            count = cursor.fetchone()[0]
            return count > 0

        finally:
            cursor.close()
            conn.close()

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def get_credentials(self):
        """Get store credentials for display"""
        if self.status != 'active':
            return None

        if self.platform == 'woocommerce':
            admin_url = f"https://{self.domain}/wp-admin"
        else:
            admin_url = f"https://{self.domain}/admin"

        return {
            'store_url': f"https://{self.domain}",
            'admin_url': admin_url,
            'admin_user': self.admin_user,
            'admin_password': self.admin_password
        }

    def to_dict(self):
        """Convert to dictionary (excluding sensitive fields)"""
        return {
            'id': self.id,
            'email': self.email,
            'company_name': self.company_name,
            'domain': self.domain,
            'platform': self.platform,
            'status': self.status,
            'web_port': self.web_port,
            'server_id': self.server_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

    def get_server(self):
        """Get the server this customer is hosted on"""
        if not self.server_id:
            return None
        return Server.get_by_id(self.server_id)

    def __repr__(self):
        return f"<Customer {self.id}: {self.email}>"

    @staticmethod
    def get_by_stripe_customer_id(stripe_customer_id):
        """Get customer by Stripe customer ID"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("SELECT * FROM customers WHERE stripe_customer_id = %s", (stripe_customer_id,))
            row = cursor.fetchone()

            if row:
                return Customer(**row)
            return None

        finally:
            cursor.close()
            conn.close()


# =============================================================================
# PricingPlan Model
# =============================================================================

class PricingPlan:
    """Pricing plan model for subscription tiers"""

    def __init__(self, id=None, name=None, slug=None, platform=None, tier_type=None,
                 price_monthly=None, store_limit=1, stripe_product_id=None,
                 stripe_price_id=None, features=None, memory_limit='1g',
                 cpu_limit='1.0', disk_limit_gb=25, bandwidth_limit_gb=250,
                 is_active=True, display_order=0,
                 created_at=None, updated_at=None):
        self.id = id
        self.name = name
        self.slug = slug
        self.platform = platform
        self.tier_type = tier_type
        self.price_monthly = price_monthly
        self.store_limit = store_limit
        self.stripe_product_id = stripe_product_id
        self.stripe_price_id = stripe_price_id
        self.features = features if features else {}
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit
        self.disk_limit_gb = disk_limit_gb
        self.bandwidth_limit_gb = bandwidth_limit_gb
        self.is_active = is_active
        self.display_order = display_order
        self.created_at = created_at
        self.updated_at = updated_at

    @staticmethod
    def get_by_id(plan_id):
        """Get plan by ID"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("SELECT * FROM pricing_plans WHERE id = %s", (plan_id,))
            row = cursor.fetchone()
            if row:
                import json
                if row.get('features') and isinstance(row['features'], str):
                    row['features'] = json.loads(row['features'])
                return PricingPlan(**row)
            return None
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_by_slug(slug):
        """Get plan by slug"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("SELECT * FROM pricing_plans WHERE slug = %s", (slug,))
            row = cursor.fetchone()
            if row:
                import json
                if row.get('features') and isinstance(row['features'], str):
                    row['features'] = json.loads(row['features'])
                return PricingPlan(**row)
            return None
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_all():
        """Get all pricing plans (including inactive)"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("""
                SELECT * FROM pricing_plans
                ORDER BY platform, display_order
            """)
            rows = cursor.fetchall()
            import json
            plans = []
            for row in rows:
                if row.get('features') and isinstance(row['features'], str):
                    row['features'] = json.loads(row['features'])
                plans.append(PricingPlan(**row))
            return plans
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_all_active():
        """Get all active pricing plans"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("""
                SELECT * FROM pricing_plans
                WHERE is_active = TRUE
                ORDER BY platform, display_order
            """)
            rows = cursor.fetchall()
            import json
            plans = []
            for row in rows:
                if row.get('features') and isinstance(row['features'], str):
                    row['features'] = json.loads(row['features'])
                plans.append(PricingPlan(**row))
            return plans
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_by_platform(platform):
        """Get all active plans for a specific platform"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("""
                SELECT * FROM pricing_plans
                WHERE is_active = TRUE AND platform = %s
                ORDER BY display_order
            """, (platform,))
            rows = cursor.fetchall()
            import json
            plans = []
            for row in rows:
                if row.get('features') and isinstance(row['features'], str):
                    row['features'] = json.loads(row['features'])
                plans.append(PricingPlan(**row))
            return plans
        finally:
            cursor.close()
            conn.close()

    def has_feature(self, feature_name):
        """Check if plan has a specific feature"""
        return self.features.get(feature_name, False)

    def to_dict(self):
        """Convert to dictionary"""
        return {
            'id': self.id,
            'name': self.name,
            'slug': self.slug,
            'platform': self.platform,
            'tier_type': self.tier_type,
            'price_monthly': float(self.price_monthly) if self.price_monthly else 0,
            'store_limit': self.store_limit,
            'features': self.features,
            'memory_limit': self.memory_limit,
            'cpu_limit': self.cpu_limit
        }

    def update(self):
        """Update pricing plan in database"""
        import json
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            features_json = json.dumps(self.features) if self.features else '{}'
            cursor.execute("""
                UPDATE pricing_plans SET
                    name = %s,
                    price_monthly = %s,
                    store_limit = %s,
                    features = %s,
                    memory_limit = %s,
                    cpu_limit = %s,
                    is_active = %s,
                    display_order = %s
                WHERE id = %s
            """, (
                self.name,
                self.price_monthly,
                self.store_limit,
                features_json,
                self.memory_limit,
                self.cpu_limit,
                self.is_active,
                self.display_order,
                self.id
            ))
            conn.commit()
            return True
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cursor.close()
            conn.close()

    def __repr__(self):
        return f"<PricingPlan {self.id}: {self.slug}>"


# =============================================================================
# ResourceUsage Model
# =============================================================================

class ResourceUsage:
    """Daily resource usage snapshot for a customer"""

    def __init__(self, id=None, customer_id=None, date=None,
                 disk_used_bytes=0, bandwidth_used_bytes=0, created_at=None):
        self.id = id
        self.customer_id = customer_id
        self.date = date
        self.disk_used_bytes = disk_used_bytes
        self.bandwidth_used_bytes = bandwidth_used_bytes
        self.created_at = created_at

    def save(self):
        """Save or update resource usage record"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            # Use INSERT ... ON DUPLICATE KEY UPDATE for upsert
            cursor.execute("""
                INSERT INTO resource_usage (customer_id, date, disk_used_bytes, bandwidth_used_bytes)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    disk_used_bytes = VALUES(disk_used_bytes),
                    bandwidth_used_bytes = VALUES(bandwidth_used_bytes)
            """, (self.customer_id, self.date, self.disk_used_bytes, self.bandwidth_used_bytes))
            conn.commit()
            if self.id is None:
                self.id = cursor.lastrowid
            return self
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_for_customer(customer_id, date):
        """Get usage for a specific customer and date"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute(
                "SELECT * FROM resource_usage WHERE customer_id = %s AND date = %s",
                (customer_id, date)
            )
            row = cursor.fetchone()
            if row:
                return ResourceUsage(**row)
            return None
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_monthly_bandwidth(customer_id):
        """Get total bandwidth used in current billing month"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT COALESCE(SUM(bandwidth_used_bytes), 0)
                FROM resource_usage
                WHERE customer_id = %s
                AND date >= DATE_FORMAT(NOW(), '%%Y-%%m-01')
            """, (customer_id,))
            result = cursor.fetchone()
            return result[0] if result else 0
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_current_disk_usage(customer_id):
        """Get most recent disk usage for customer"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT disk_used_bytes FROM resource_usage
                WHERE customer_id = %s
                ORDER BY date DESC LIMIT 1
            """, (customer_id,))
            result = cursor.fetchone()
            return result[0] if result else 0
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_usage_history(customer_id, days=30):
        """Get usage history for last N days"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("""
                SELECT * FROM resource_usage
                WHERE customer_id = %s
                AND date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
                ORDER BY date ASC
            """, (customer_id, days))
            rows = cursor.fetchall()
            return [ResourceUsage(**row) for row in rows]
        finally:
            cursor.close()
            conn.close()


# =============================================================================
# ResourceAlert Model
# =============================================================================

class ResourceAlert:
    """Resource limit alert record"""

    ALERT_TYPES = ['disk_warning', 'disk_critical', 'bandwidth_warning', 'bandwidth_critical']

    def __init__(self, id=None, customer_id=None, alert_type=None,
                 threshold_percent=None, current_usage_bytes=None,
                 limit_bytes=None, notified_at=None):
        self.id = id
        self.customer_id = customer_id
        self.alert_type = alert_type
        self.threshold_percent = threshold_percent
        self.current_usage_bytes = current_usage_bytes
        self.limit_bytes = limit_bytes
        self.notified_at = notified_at

    def save(self):
        """Save alert record"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO resource_alerts
                (customer_id, alert_type, threshold_percent, current_usage_bytes, limit_bytes)
                VALUES (%s, %s, %s, %s, %s)
            """, (self.customer_id, self.alert_type, self.threshold_percent,
                  self.current_usage_bytes, self.limit_bytes))
            conn.commit()
            self.id = cursor.lastrowid
            return self
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def was_recently_sent(customer_id, alert_type, hours=24):
        """Check if this alert type was sent recently"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT COUNT(*) FROM resource_alerts
                WHERE customer_id = %s
                AND alert_type = %s
                AND notified_at > DATE_SUB(NOW(), INTERVAL %s HOUR)
            """, (customer_id, alert_type, hours))
            count = cursor.fetchone()[0]
            return count > 0
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_recent_for_customer(customer_id, limit=10):
        """Get recent alerts for a customer"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("""
                SELECT * FROM resource_alerts
                WHERE customer_id = %s
                ORDER BY notified_at DESC
                LIMIT %s
            """, (customer_id, limit))
            rows = cursor.fetchall()
            return [ResourceAlert(**row) for row in rows]
        finally:
            cursor.close()
            conn.close()


# =============================================================================
# Subscription Model
# =============================================================================

class Subscription:
    """Subscription model for customer subscriptions"""

    def __init__(self, id=None, customer_id=None, plan_id=None,
                 stripe_subscription_id=None, stripe_customer_id=None,
                 status='incomplete', current_period_start=None,
                 current_period_end=None, cancel_at=None, canceled_at=None,
                 created_at=None, updated_at=None):
        self.id = id
        self.customer_id = customer_id
        self.plan_id = plan_id
        self.stripe_subscription_id = stripe_subscription_id
        self.stripe_customer_id = stripe_customer_id
        self.status = status
        self.current_period_start = current_period_start
        self.current_period_end = current_period_end
        self.cancel_at = cancel_at
        self.canceled_at = canceled_at
        self.created_at = created_at or datetime.now()
        self.updated_at = updated_at or datetime.now()

    def save(self):
        """Save subscription to database"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            if self.id is None:
                cursor.execute("""
                    INSERT INTO subscriptions
                    (customer_id, plan_id, stripe_subscription_id, stripe_customer_id,
                     status, current_period_start, current_period_end,
                     cancel_at, canceled_at, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    self.customer_id, self.plan_id, self.stripe_subscription_id,
                    self.stripe_customer_id, self.status,
                    self.current_period_start, self.current_period_end,
                    self.cancel_at, self.canceled_at,
                    self.created_at, self.updated_at
                ))
                self.id = cursor.lastrowid
            else:
                cursor.execute("""
                    UPDATE subscriptions SET
                        status = %s, current_period_start = %s, current_period_end = %s,
                        cancel_at = %s, canceled_at = %s, updated_at = %s
                    WHERE id = %s
                """, (
                    self.status, self.current_period_start, self.current_period_end,
                    self.cancel_at, self.canceled_at, datetime.now(), self.id
                ))

            conn.commit()
            return self
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_by_customer_id(customer_id):
        """Get subscription by customer ID"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("""
                SELECT * FROM subscriptions
                WHERE customer_id = %s
                ORDER BY created_at DESC LIMIT 1
            """, (customer_id,))
            row = cursor.fetchone()
            if row:
                return Subscription(**row)
            return None
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_by_stripe_subscription_id(stripe_subscription_id):
        """Get subscription by Stripe subscription ID"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("""
                SELECT * FROM subscriptions
                WHERE stripe_subscription_id = %s
            """, (stripe_subscription_id,))
            row = cursor.fetchone()
            if row:
                return Subscription(**row)
            return None
        finally:
            cursor.close()
            conn.close()

    def __repr__(self):
        return f"<Subscription {self.id}: {self.stripe_subscription_id}>"


# =============================================================================
# Invoice Model
# =============================================================================

class Invoice:
    """Invoice model for payment history"""

    def __init__(self, id=None, customer_id=None, subscription_id=None,
                 stripe_invoice_id=None, stripe_payment_intent_id=None,
                 amount_due=0, amount_paid=0, currency='usd', status='draft',
                 invoice_pdf_url=None, hosted_invoice_url=None,
                 period_start=None, period_end=None, paid_at=None,
                 created_at=None):
        self.id = id
        self.customer_id = customer_id
        self.subscription_id = subscription_id
        self.stripe_invoice_id = stripe_invoice_id
        self.stripe_payment_intent_id = stripe_payment_intent_id
        self.amount_due = amount_due
        self.amount_paid = amount_paid
        self.currency = currency
        self.status = status
        self.invoice_pdf_url = invoice_pdf_url
        self.hosted_invoice_url = hosted_invoice_url
        self.period_start = period_start
        self.period_end = period_end
        self.paid_at = paid_at
        self.created_at = created_at or datetime.now()

    def save(self):
        """Save invoice to database"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            if self.id is None:
                cursor.execute("""
                    INSERT INTO invoices
                    (customer_id, subscription_id, stripe_invoice_id, stripe_payment_intent_id,
                     amount_due, amount_paid, currency, status, invoice_pdf_url,
                     hosted_invoice_url, period_start, period_end, paid_at, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    self.customer_id, self.subscription_id, self.stripe_invoice_id,
                    self.stripe_payment_intent_id, self.amount_due, self.amount_paid,
                    self.currency, self.status, self.invoice_pdf_url,
                    self.hosted_invoice_url, self.period_start, self.period_end,
                    self.paid_at, self.created_at
                ))
                self.id = cursor.lastrowid
            else:
                cursor.execute("""
                    UPDATE invoices SET
                        amount_paid = %s, status = %s, paid_at = %s,
                        invoice_pdf_url = %s, hosted_invoice_url = %s
                    WHERE id = %s
                """, (
                    self.amount_paid, self.status, self.paid_at,
                    self.invoice_pdf_url, self.hosted_invoice_url, self.id
                ))

            conn.commit()
            return self
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_by_customer_id(customer_id, limit=10):
        """Get invoices for a customer"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("""
                SELECT * FROM invoices
                WHERE customer_id = %s
                ORDER BY created_at DESC
                LIMIT %s
            """, (customer_id, limit))
            rows = cursor.fetchall()
            return [Invoice(**row) for row in rows]
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_by_stripe_invoice_id(stripe_invoice_id):
        """Get invoice by Stripe invoice ID"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("""
                SELECT * FROM invoices
                WHERE stripe_invoice_id = %s
            """, (stripe_invoice_id,))
            row = cursor.fetchone()
            if row:
                return Invoice(**row)
            return None
        finally:
            cursor.close()
            conn.close()

    def __repr__(self):
        return f"<Invoice {self.id}: {self.stripe_invoice_id}>"


# =============================================================================
# WebhookEvent Model
# =============================================================================

class WebhookEvent:
    """Webhook event model for idempotency tracking"""

    def __init__(self, id=None, stripe_event_id=None, event_type=None,
                 payload=None, processed=False, error_message=None,
                 created_at=None, processed_at=None):
        self.id = id
        self.stripe_event_id = stripe_event_id
        self.event_type = event_type
        self.payload = payload
        self.processed = processed
        self.error_message = error_message
        self.created_at = created_at or datetime.now()
        self.processed_at = processed_at

    def save(self):
        """Save webhook event to database"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            import json
            payload_json = json.dumps(self.payload) if self.payload else None

            if self.id is None:
                cursor.execute("""
                    INSERT INTO stripe_webhook_events
                    (stripe_event_id, event_type, payload, processed, error_message, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (
                    self.stripe_event_id, self.event_type, payload_json,
                    self.processed, self.error_message, self.created_at
                ))
                self.id = cursor.lastrowid
            else:
                cursor.execute("""
                    UPDATE stripe_webhook_events SET
                        processed = %s, error_message = %s, processed_at = %s
                    WHERE id = %s
                """, (
                    self.processed, self.error_message, self.processed_at, self.id
                ))

            conn.commit()
            return self
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def exists(stripe_event_id):
        """Check if event already exists (for idempotency)"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT COUNT(*) FROM stripe_webhook_events
                WHERE stripe_event_id = %s
            """, (stripe_event_id,))
            count = cursor.fetchone()[0]
            return count > 0
        finally:
            cursor.close()
            conn.close()

    def mark_processed(self):
        """Mark event as processed"""
        self.processed = True
        self.processed_at = datetime.now()
        self.save()

    def mark_error(self, error_message):
        """Mark event as failed with error"""
        self.error_message = error_message
        self.processed_at = datetime.now()
        self.save()

    def __repr__(self):
        return f"<WebhookEvent {self.id}: {self.stripe_event_id}>"


# =============================================================================
# TicketCategory Model
# =============================================================================

class TicketCategory:
    """Ticket category model for organizing support tickets"""

    def __init__(self, id=None, name=None, slug=None, description=None,
                 color='#0088ff', display_order=0, is_active=True,
                 created_at=None, updated_at=None):
        self.id = id
        self.name = name
        self.slug = slug
        self.description = description
        self.color = color
        self.display_order = display_order
        self.is_active = is_active
        self.created_at = created_at
        self.updated_at = updated_at

    @staticmethod
    def get_all_active():
        """Get all active categories ordered by display_order"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("""
                SELECT * FROM ticket_categories
                WHERE is_active = TRUE
                ORDER BY display_order
            """)
            return [TicketCategory(**row) for row in cursor.fetchall()]
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_by_id(category_id):
        """Get category by ID"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("SELECT * FROM ticket_categories WHERE id = %s", (category_id,))
            row = cursor.fetchone()
            return TicketCategory(**row) if row else None
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_by_slug(slug):
        """Get category by slug"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("SELECT * FROM ticket_categories WHERE slug = %s", (slug,))
            row = cursor.fetchone()
            return TicketCategory(**row) if row else None
        finally:
            cursor.close()
            conn.close()

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'slug': self.slug,
            'description': self.description,
            'color': self.color
        }

    def __repr__(self):
        return f"<TicketCategory {self.id}: {self.slug}>"


# =============================================================================
# Ticket Model
# =============================================================================

class Ticket:
    """Support ticket model"""

    STATUSES = ['open', 'in_progress', 'waiting_customer', 'resolved', 'closed']
    PRIORITIES = ['low', 'medium', 'high', 'urgent']

    def __init__(self, id=None, ticket_number=None, customer_id=None,
                 category_id=None, assigned_admin_id=None, subject=None,
                 status='open', priority='medium', created_at=None,
                 updated_at=None, resolved_at=None, closed_at=None):
        self.id = id
        self.ticket_number = ticket_number
        self.customer_id = customer_id
        self.category_id = category_id
        self.assigned_admin_id = assigned_admin_id
        self.subject = subject
        self.status = status
        self.priority = priority
        self.created_at = created_at or datetime.now()
        self.updated_at = updated_at or datetime.now()
        self.resolved_at = resolved_at
        self.closed_at = closed_at

    @staticmethod
    def generate_ticket_number():
        """Generate unique ticket number like TKT-001234"""
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT MAX(id) FROM tickets")
            max_id = cursor.fetchone()[0] or 0
            return f"TKT-{(max_id + 1):06d}"
        finally:
            cursor.close()
            conn.close()

    def save(self):
        """Save ticket to database (insert or update)"""
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            if self.id is None:
                # Generate ticket number for new tickets
                if not self.ticket_number:
                    self.ticket_number = Ticket.generate_ticket_number()

                cursor.execute("""
                    INSERT INTO tickets
                    (ticket_number, customer_id, category_id, assigned_admin_id,
                     subject, status, priority, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    self.ticket_number, self.customer_id, self.category_id,
                    self.assigned_admin_id, self.subject, self.status,
                    self.priority, self.created_at, self.updated_at
                ))
                self.id = cursor.lastrowid
            else:
                cursor.execute("""
                    UPDATE tickets SET
                        category_id = %s, assigned_admin_id = %s, subject = %s,
                        status = %s, priority = %s, updated_at = %s,
                        resolved_at = %s, closed_at = %s
                    WHERE id = %s
                """, (
                    self.category_id, self.assigned_admin_id, self.subject,
                    self.status, self.priority, datetime.now(),
                    self.resolved_at, self.closed_at, self.id
                ))
            conn.commit()
            return self
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_by_id(ticket_id):
        """Get ticket by ID"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("SELECT * FROM tickets WHERE id = %s", (ticket_id,))
            row = cursor.fetchone()
            return Ticket(**row) if row else None
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_by_ticket_number(ticket_number):
        """Get ticket by ticket number"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("SELECT * FROM tickets WHERE ticket_number = %s", (ticket_number,))
            row = cursor.fetchone()
            return Ticket(**row) if row else None
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_by_customer(customer_id, status=None, page=1, per_page=20):
        """Get tickets for a customer with optional status filter"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            where = "t.customer_id = %s"
            params = [customer_id]

            if status:
                where += " AND t.status = %s"
                params.append(status)

            # Get count
            cursor.execute(f"SELECT COUNT(*) as count FROM tickets t WHERE {where}", params)
            total = cursor.fetchone()['count']

            # Get paginated results
            offset = (page - 1) * per_page
            cursor.execute(f"""
                SELECT t.*, tc.name as category_name, tc.color as category_color
                FROM tickets t
                LEFT JOIN ticket_categories tc ON t.category_id = tc.id
                WHERE {where}
                ORDER BY
                    CASE t.status
                        WHEN 'open' THEN 1
                        WHEN 'in_progress' THEN 2
                        WHEN 'waiting_customer' THEN 3
                        ELSE 4
                    END,
                    t.updated_at DESC
                LIMIT %s OFFSET %s
            """, params + [per_page, offset])

            tickets = cursor.fetchall()
            return tickets, total
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_all_filtered(status=None, priority=None, category_id=None,
                         assigned_admin_id=None, customer_id=None,
                         search=None, page=1, per_page=20):
        """Get all tickets with filters (for admin)"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            where_clauses = ["1=1"]
            params = []

            if status:
                where_clauses.append("t.status = %s")
                params.append(status)
            if priority:
                where_clauses.append("t.priority = %s")
                params.append(priority)
            if category_id:
                where_clauses.append("t.category_id = %s")
                params.append(category_id)
            if assigned_admin_id:
                if assigned_admin_id == 'unassigned':
                    where_clauses.append("t.assigned_admin_id IS NULL")
                else:
                    where_clauses.append("t.assigned_admin_id = %s")
                    params.append(assigned_admin_id)
            if customer_id:
                where_clauses.append("t.customer_id = %s")
                params.append(customer_id)
            if search:
                where_clauses.append("(t.ticket_number LIKE %s OR t.subject LIKE %s OR c.email LIKE %s)")
                search_param = f"%{search}%"
                params.extend([search_param, search_param, search_param])

            where_sql = " AND ".join(where_clauses)

            # Get count
            cursor.execute(f"""
                SELECT COUNT(*) as count
                FROM tickets t
                LEFT JOIN customers c ON t.customer_id = c.id
                WHERE {where_sql}
            """, params)
            total = cursor.fetchone()['count']

            # Get paginated results
            offset = (page - 1) * per_page
            cursor.execute(f"""
                SELECT t.*,
                       tc.name as category_name, tc.color as category_color,
                       c.email as customer_email, c.company_name as customer_company,
                       c.domain as customer_domain,
                       a.full_name as assigned_admin_name
                FROM tickets t
                LEFT JOIN ticket_categories tc ON t.category_id = tc.id
                LEFT JOIN customers c ON t.customer_id = c.id
                LEFT JOIN admin_users a ON t.assigned_admin_id = a.id
                WHERE {where_sql}
                ORDER BY
                    CASE t.priority
                        WHEN 'urgent' THEN 1
                        WHEN 'high' THEN 2
                        WHEN 'medium' THEN 3
                        ELSE 4
                    END,
                    CASE t.status
                        WHEN 'open' THEN 1
                        WHEN 'in_progress' THEN 2
                        WHEN 'waiting_customer' THEN 3
                        ELSE 4
                    END,
                    t.updated_at DESC
                LIMIT %s OFFSET %s
            """, params + [per_page, offset])

            tickets = cursor.fetchall()
            return tickets, total
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_stats():
        """Get ticket statistics for admin dashboard"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) as open_count,
                    SUM(CASE WHEN status = 'in_progress' THEN 1 ELSE 0 END) as in_progress_count,
                    SUM(CASE WHEN status = 'waiting_customer' THEN 1 ELSE 0 END) as waiting_count,
                    SUM(CASE WHEN priority = 'urgent' THEN 1 ELSE 0 END) as urgent_count,
                    SUM(CASE WHEN assigned_admin_id IS NULL AND status NOT IN ('resolved', 'closed') THEN 1 ELSE 0 END) as unassigned_count
                FROM tickets
            """)
            return cursor.fetchone()
        finally:
            cursor.close()
            conn.close()

    def get_messages(self, include_internal=False):
        """Get all messages for this ticket"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            where = "tm.ticket_id = %s"
            params = [self.id]

            if not include_internal:
                where += " AND tm.is_internal_note = FALSE"

            cursor.execute(f"""
                SELECT tm.*,
                       c.email as customer_email, c.company_name as customer_name,
                       a.full_name as admin_name
                FROM ticket_messages tm
                LEFT JOIN customers c ON tm.customer_id = c.id
                LEFT JOIN admin_users a ON tm.admin_user_id = a.id
                WHERE {where}
                ORDER BY tm.created_at ASC
            """, params)
            return cursor.fetchall()
        finally:
            cursor.close()
            conn.close()

    def get_attachments(self):
        """Get all attachments for this ticket"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("""
                SELECT * FROM ticket_attachments
                WHERE ticket_id = %s
                ORDER BY created_at ASC
            """, (self.id,))
            return cursor.fetchall()
        finally:
            cursor.close()
            conn.close()

    def to_dict(self):
        return {
            'id': self.id,
            'ticket_number': self.ticket_number,
            'customer_id': self.customer_id,
            'category_id': self.category_id,
            'assigned_admin_id': self.assigned_admin_id,
            'subject': self.subject,
            'status': self.status,
            'priority': self.priority,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

    def __repr__(self):
        return f"<Ticket {self.ticket_number}: {self.subject}>"


# =============================================================================
# TicketMessage Model
# =============================================================================

class TicketMessage:
    """Ticket message/reply model"""

    def __init__(self, id=None, ticket_id=None, customer_id=None,
                 admin_user_id=None, message=None, is_internal_note=False,
                 created_at=None, updated_at=None):
        self.id = id
        self.ticket_id = ticket_id
        self.customer_id = customer_id
        self.admin_user_id = admin_user_id
        self.message = message
        self.is_internal_note = is_internal_note
        self.created_at = created_at or datetime.now()
        self.updated_at = updated_at

    def save(self):
        """Save message to database"""
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            if self.id is None:
                cursor.execute("""
                    INSERT INTO ticket_messages
                    (ticket_id, customer_id, admin_user_id, message, is_internal_note, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (
                    self.ticket_id, self.customer_id, self.admin_user_id,
                    self.message, self.is_internal_note, self.created_at
                ))
                self.id = cursor.lastrowid

                # Update ticket's updated_at timestamp
                cursor.execute(
                    "UPDATE tickets SET updated_at = %s WHERE id = %s",
                    (datetime.now(), self.ticket_id)
                )
            conn.commit()
            return self
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_by_id(message_id):
        """Get message by ID"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("SELECT * FROM ticket_messages WHERE id = %s", (message_id,))
            row = cursor.fetchone()
            return TicketMessage(**row) if row else None
        finally:
            cursor.close()
            conn.close()

    def __repr__(self):
        return f"<TicketMessage {self.id}>"


# =============================================================================
# TicketAttachment Model
# =============================================================================

class TicketAttachment:
    """Ticket attachment model"""

    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'txt', 'doc', 'docx', 'zip'}
    MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

    def __init__(self, id=None, ticket_id=None, message_id=None,
                 filename=None, original_filename=None, file_path=None,
                 file_size=None, mime_type=None, uploaded_by_customer_id=None,
                 uploaded_by_admin_id=None, created_at=None):
        self.id = id
        self.ticket_id = ticket_id
        self.message_id = message_id
        self.filename = filename
        self.original_filename = original_filename
        self.file_path = file_path
        self.file_size = file_size
        self.mime_type = mime_type
        self.uploaded_by_customer_id = uploaded_by_customer_id
        self.uploaded_by_admin_id = uploaded_by_admin_id
        self.created_at = created_at or datetime.now()

    def save(self):
        """Save attachment record to database"""
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO ticket_attachments
                (ticket_id, message_id, filename, original_filename, file_path,
                 file_size, mime_type, uploaded_by_customer_id, uploaded_by_admin_id, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                self.ticket_id, self.message_id, self.filename, self.original_filename,
                self.file_path, self.file_size, self.mime_type,
                self.uploaded_by_customer_id, self.uploaded_by_admin_id, self.created_at
            ))
            self.id = cursor.lastrowid
            conn.commit()
            return self
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_by_id(attachment_id):
        """Get attachment by ID"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("SELECT * FROM ticket_attachments WHERE id = %s", (attachment_id,))
            row = cursor.fetchone()
            return TicketAttachment(**row) if row else None
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_by_ticket(ticket_id):
        """Get all attachments for a ticket"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("""
                SELECT * FROM ticket_attachments
                WHERE ticket_id = %s
                ORDER BY created_at ASC
            """, (ticket_id,))
            return [TicketAttachment(**row) for row in cursor.fetchall()]
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def allowed_file(filename):
        """Check if file extension is allowed"""
        return '.' in filename and \
               filename.rsplit('.', 1)[1].lower() in TicketAttachment.ALLOWED_EXTENSIONS

    def __repr__(self):
        return f"<TicketAttachment {self.id}: {self.original_filename}>"


class ConsultationAppointment:
    """Model for consultation appointments scheduled via the website"""

    def __init__(self, id=None, first_name=None, last_name=None, email=None,
                 phone=None, scheduled_date=None, scheduled_time=None,
                 timezone='EST', status='pending', notes=None,
                 assigned_admin_id=None, created_at=None, updated_at=None):
        self.id = id
        self.first_name = first_name
        self.last_name = last_name
        self.email = email
        self.phone = phone
        self.scheduled_date = scheduled_date
        self.scheduled_time = scheduled_time
        self.timezone = timezone
        self.status = status
        self.notes = notes
        self.assigned_admin_id = assigned_admin_id
        self.created_at = created_at
        self.updated_at = updated_at

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    def save(self):
        """Insert or update appointment"""
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            if self.id is None:
                cursor.execute("""
                    INSERT INTO consultation_appointments
                    (first_name, last_name, email, phone, scheduled_date, scheduled_time,
                     timezone, status, notes, assigned_admin_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (self.first_name, self.last_name, self.email, self.phone,
                      self.scheduled_date, self.scheduled_time, self.timezone,
                      self.status, self.notes, self.assigned_admin_id))
                self.id = cursor.lastrowid
            else:
                cursor.execute("""
                    UPDATE consultation_appointments SET
                        first_name = %s, last_name = %s, email = %s, phone = %s,
                        scheduled_date = %s, scheduled_time = %s, timezone = %s,
                        status = %s, notes = %s, assigned_admin_id = %s
                    WHERE id = %s
                """, (self.first_name, self.last_name, self.email, self.phone,
                      self.scheduled_date, self.scheduled_time, self.timezone,
                      self.status, self.notes, self.assigned_admin_id, self.id))
            conn.commit()
            return self
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_by_id(appointment_id):
        """Get appointment by ID"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("SELECT * FROM consultation_appointments WHERE id = %s", (appointment_id,))
            row = cursor.fetchone()
            return ConsultationAppointment(**row) if row else None
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_all_filtered(status=None, search=None, date_from=None, date_to=None,
                         page=1, per_page=20):
        """Get appointments with filtering and pagination"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            where_clauses = ["1=1"]
            params = []

            if status:
                where_clauses.append("status = %s")
                params.append(status)
            if search:
                where_clauses.append(
                    "(first_name LIKE %s OR last_name LIKE %s OR email LIKE %s OR phone LIKE %s)"
                )
                search_param = f"%{search}%"
                params.extend([search_param, search_param, search_param, search_param])
            if date_from:
                where_clauses.append("scheduled_date >= %s")
                params.append(date_from)
            if date_to:
                where_clauses.append("scheduled_date <= %s")
                params.append(date_to)

            where_sql = " AND ".join(where_clauses)

            # Get count
            cursor.execute(f"SELECT COUNT(*) as count FROM consultation_appointments WHERE {where_sql}", params)
            total = cursor.fetchone()['count']

            # Get paginated results
            offset = (page - 1) * per_page
            cursor.execute(f"""
                SELECT * FROM consultation_appointments
                WHERE {where_sql}
                ORDER BY scheduled_date DESC, scheduled_time DESC
                LIMIT %s OFFSET %s
            """, params + [per_page, offset])

            appointments = [ConsultationAppointment(**row) for row in cursor.fetchall()]
            return appointments, total
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_stats():
        """Get appointment statistics"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending,
                    SUM(CASE WHEN status = 'confirmed' THEN 1 ELSE 0 END) as confirmed,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                    SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) as cancelled,
                    SUM(CASE WHEN status = 'no_show' THEN 1 ELSE 0 END) as no_show,
                    SUM(CASE WHEN scheduled_date = CURDATE() THEN 1 ELSE 0 END) as today,
                    SUM(CASE WHEN scheduled_date > CURDATE() AND scheduled_date <= DATE_ADD(CURDATE(), INTERVAL 7 DAY) THEN 1 ELSE 0 END) as this_week
                FROM consultation_appointments
            """)
            return cursor.fetchone()
        finally:
            cursor.close()
            conn.close()

    def to_dict(self):
        """Convert to dictionary for JSON serialization"""
        return {
            'id': self.id,
            'first_name': self.first_name,
            'last_name': self.last_name,
            'full_name': self.full_name,
            'email': self.email,
            'phone': self.phone,
            'scheduled_date': str(self.scheduled_date) if self.scheduled_date else None,
            'scheduled_time': self.scheduled_time,
            'timezone': self.timezone,
            'status': self.status,
            'notes': self.notes,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

    def __repr__(self):
        return f"<ConsultationAppointment {self.id}: {self.full_name} on {self.scheduled_date}>"


# =============================================================================
# Customer Backup Job Model
# =============================================================================

class CustomerBackupJob:
    """Tracks customer backup and restore operations"""

    def __init__(self, id=None, customer_id=None, job_type=None, backup_type=None,
                 snapshot_id=None, status='pending', error_message=None,
                 created_at=None, completed_at=None):
        self.id = id
        self.customer_id = customer_id
        self.job_type = job_type  # 'backup' or 'restore'
        self.backup_type = backup_type  # 'db', 'files', or 'both'
        self.snapshot_id = snapshot_id
        self.status = status
        self.error_message = error_message
        self.created_at = created_at or datetime.now()
        self.completed_at = completed_at

    def save(self):
        """Save job to database"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            if self.id is None:
                cursor.execute("""
                    INSERT INTO customer_backup_jobs
                    (customer_id, job_type, backup_type, snapshot_id, status,
                     error_message, created_at, completed_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    self.customer_id, self.job_type, self.backup_type,
                    self.snapshot_id, self.status, self.error_message,
                    self.created_at, self.completed_at
                ))
                self.id = cursor.lastrowid
            else:
                cursor.execute("""
                    UPDATE customer_backup_jobs SET
                        status = %s, error_message = %s, completed_at = %s,
                        snapshot_id = %s
                    WHERE id = %s
                """, (self.status, self.error_message, self.completed_at,
                      self.snapshot_id, self.id))

            conn.commit()
            return self
        finally:
            cursor.close()
            conn.close()

    def update_status(self, status, error_message=None):
        """Update job status"""
        self.status = status
        self.error_message = error_message
        if status in ('completed', 'failed'):
            self.completed_at = datetime.now()
        self.save()

    @staticmethod
    def get_by_id(job_id):
        """Get job by ID"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("SELECT * FROM customer_backup_jobs WHERE id = %s", (job_id,))
            row = cursor.fetchone()
            if row:
                return CustomerBackupJob(**row)
            return None
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_active_job(customer_id):
        """Get active (pending/running) job for customer"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("""
                SELECT * FROM customer_backup_jobs
                WHERE customer_id = %s AND status IN ('pending', 'running')
                ORDER BY created_at DESC LIMIT 1
            """, (customer_id,))
            row = cursor.fetchone()
            if row:
                return CustomerBackupJob(**row)
            return None
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_recent_jobs(customer_id, limit=10):
        """Get recent jobs for customer"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("""
                SELECT * FROM customer_backup_jobs
                WHERE customer_id = %s
                ORDER BY created_at DESC LIMIT %s
            """, (customer_id, limit))
            return [CustomerBackupJob(**row) for row in cursor.fetchall()]
        finally:
            cursor.close()
            conn.close()

    def to_dict(self):
        """Convert to dictionary"""
        return {
            'id': self.id,
            'customer_id': self.customer_id,
            'job_type': self.job_type,
            'backup_type': self.backup_type,
            'snapshot_id': self.snapshot_id,
            'status': self.status,
            'error_message': self.error_message,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None
        }

    def __repr__(self):
        return f"<CustomerBackupJob {self.id}: {self.job_type} - {self.status}>"


class StagingPortManager:
    """Manages port allocation for staging environment containers"""
    # Using 10001-10100 to avoid conflict with phpMyAdmin ports (9001-9100)
    PORT_RANGE_START = 10001
    PORT_RANGE_END = 10100

    @staticmethod
    def get_next_available_port():
        """Get the next available port for a new staging environment"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            # Get all currently used staging ports
            cursor.execute("SELECT web_port FROM staging_environments WHERE web_port IS NOT NULL AND status != 'deleted'")
            used_ports = {row[0] for row in cursor.fetchall()}

            # Find first available port in range
            for port in range(StagingPortManager.PORT_RANGE_START, StagingPortManager.PORT_RANGE_END + 1):
                if port not in used_ports:
                    return port

            return None  # No ports available

        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def is_port_available(port):
        """Check if a specific port is available"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("SELECT COUNT(*) FROM staging_environments WHERE web_port = %s AND status != 'deleted'", (port,))
            count = cursor.fetchone()[0]
            return count == 0
        finally:
            cursor.close()
            conn.close()


# =============================================================================
# Staging Environment Model
# =============================================================================

class StagingEnvironment:
    """Staging environment model for customer staging sites"""

    MAX_STAGING_PER_CUSTOMER = 3
    STATUSES = ['creating', 'active', 'syncing', 'failed', 'deleted']

    def __init__(self, id=None, customer_id=None, name=None, staging_domain=None,
                 status='creating', web_port=None, db_name=None, db_user=None,
                 db_password=None, source_snapshot_date=None, last_push_date=None,
                 created_at=None, updated_at=None):
        self.id = id
        self.customer_id = customer_id
        self.name = name
        self.staging_domain = staging_domain
        self.status = status
        self.web_port = web_port
        self.db_name = db_name
        self.db_user = db_user
        self.db_password = db_password
        self.source_snapshot_date = source_snapshot_date
        self.last_push_date = last_push_date
        self.created_at = created_at or datetime.now()
        self.updated_at = updated_at or datetime.now()

    # =========================================================================
    # Database Operations
    # =========================================================================

    def save(self):
        """Save staging environment to database (insert or update)"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            if self.id is None:
                cursor.execute("""
                    INSERT INTO staging_environments
                    (customer_id, name, staging_domain, status, web_port,
                     db_name, db_user, db_password, source_snapshot_date,
                     created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    self.customer_id, self.name, self.staging_domain, self.status,
                    self.web_port, self.db_name, self.db_user, self.db_password,
                    self.source_snapshot_date, self.created_at, self.updated_at
                ))
                self.id = cursor.lastrowid

                # Update customer's staging count
                cursor.execute("""
                    UPDATE customers SET staging_count = (
                        SELECT COUNT(*) FROM staging_environments
                        WHERE customer_id = %s AND status != 'deleted'
                    ) WHERE id = %s
                """, (self.customer_id, self.customer_id))
            else:
                cursor.execute("""
                    UPDATE staging_environments SET
                        name = %s, staging_domain = %s, status = %s, web_port = %s,
                        db_name = %s, db_user = %s, db_password = %s,
                        source_snapshot_date = %s, last_push_date = %s, updated_at = %s
                    WHERE id = %s
                """, (
                    self.name, self.staging_domain, self.status, self.web_port,
                    self.db_name, self.db_user, self.db_password,
                    self.source_snapshot_date, self.last_push_date,
                    datetime.now(), self.id
                ))

            conn.commit()
            return self

        finally:
            cursor.close()
            conn.close()

    def update_status(self, status):
        """Update staging environment status"""
        self.status = status
        self.updated_at = datetime.now()
        return self.save()

    def mark_deleted(self):
        """Mark staging environment as deleted"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            self.status = 'deleted'
            cursor.execute("""
                UPDATE staging_environments SET status = 'deleted', updated_at = %s
                WHERE id = %s
            """, (datetime.now(), self.id))

            # Update customer's staging count
            cursor.execute("""
                UPDATE customers SET staging_count = (
                    SELECT COUNT(*) FROM staging_environments
                    WHERE customer_id = %s AND status != 'deleted'
                ) WHERE id = %s
            """, (self.customer_id, self.customer_id))

            conn.commit()
            return True
        finally:
            cursor.close()
            conn.close()

    # =========================================================================
    # Static Query Methods
    # =========================================================================

    @staticmethod
    def get_by_id(staging_id):
        """Get staging environment by ID"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("SELECT * FROM staging_environments WHERE id = %s", (staging_id,))
            row = cursor.fetchone()

            if row:
                return StagingEnvironment(**row)
            return None

        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_by_customer(customer_id, include_deleted=False):
        """Get all staging environments for a customer"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            if include_deleted:
                cursor.execute("""
                    SELECT * FROM staging_environments
                    WHERE customer_id = %s
                    ORDER BY created_at DESC
                """, (customer_id,))
            else:
                cursor.execute("""
                    SELECT * FROM staging_environments
                    WHERE customer_id = %s AND status != 'deleted'
                    ORDER BY created_at DESC
                """, (customer_id,))

            rows = cursor.fetchall()
            return [StagingEnvironment(**row) for row in rows]

        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_by_domain(staging_domain):
        """Get staging environment by domain"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("SELECT * FROM staging_environments WHERE staging_domain = %s", (staging_domain,))
            row = cursor.fetchone()

            if row:
                return StagingEnvironment(**row)
            return None

        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def count_by_customer(customer_id):
        """Count active staging environments for a customer"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT COUNT(*) FROM staging_environments
                WHERE customer_id = %s AND status != 'deleted'
            """, (customer_id,))
            return cursor.fetchone()[0]

        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def can_create_staging(customer_id):
        """Check if customer can create another staging environment"""
        count = StagingEnvironment.count_by_customer(customer_id)
        return count < StagingEnvironment.MAX_STAGING_PER_CUSTOMER

    @staticmethod
    def get_all(include_deleted=False, page=1, per_page=20):
        """Get all staging environments (for admin)"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            where = "1=1" if include_deleted else "se.status != 'deleted'"
            offset = (page - 1) * per_page

            # Get count
            cursor.execute(f"SELECT COUNT(*) as count FROM staging_environments se WHERE {where}")
            total = cursor.fetchone()['count']

            # Get paginated results with customer info
            cursor.execute(f"""
                SELECT se.*, c.email as customer_email, c.domain as production_domain,
                       c.company_name as customer_company, c.platform
                FROM staging_environments se
                JOIN customers c ON se.customer_id = c.id
                WHERE {where}
                ORDER BY se.created_at DESC
                LIMIT %s OFFSET %s
            """, (per_page, offset))

            rows = cursor.fetchall()
            return rows, total

        finally:
            cursor.close()
            conn.close()

    # =========================================================================
    # Sync History Methods
    # =========================================================================

    def log_sync(self, sync_type, status='pending'):
        """Log a sync operation"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO staging_sync_history
                (staging_id, sync_type, status, started_at, created_at)
                VALUES (%s, %s, %s, %s, %s)
            """, (self.id, sync_type, status, datetime.now(), datetime.now()))

            conn.commit()
            return cursor.lastrowid

        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def update_sync_status(sync_id, status, error_message=None):
        """Update sync operation status"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            if status in ['completed', 'failed']:
                cursor.execute("""
                    UPDATE staging_sync_history
                    SET status = %s, completed_at = %s, error_message = %s
                    WHERE id = %s
                """, (status, datetime.now(), error_message, sync_id))
            else:
                cursor.execute("""
                    UPDATE staging_sync_history SET status = %s WHERE id = %s
                """, (status, sync_id))

            conn.commit()

        finally:
            cursor.close()
            conn.close()

    def get_sync_history(self, limit=10):
        """Get sync history for this staging environment"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("""
                SELECT * FROM staging_sync_history
                WHERE staging_id = %s
                ORDER BY created_at DESC
                LIMIT %s
            """, (self.id, limit))

            return cursor.fetchall()

        finally:
            cursor.close()
            conn.close()

    # =========================================================================
    # Utility Methods
    # =========================================================================

    @staticmethod
    def generate_staging_domain(customer_id, staging_number):
        """Generate staging domain for a customer"""
        return f"cust{customer_id}-staging-{staging_number}.shophosting.io"

    def get_staging_url(self):
        """Get full staging site URL"""
        return f"https://{self.staging_domain}"

    def to_dict(self):
        """Convert to dictionary"""
        return {
            'id': self.id,
            'customer_id': self.customer_id,
            'name': self.name,
            'staging_domain': self.staging_domain,
            'staging_url': self.get_staging_url(),
            'status': self.status,
            'web_port': self.web_port,
            'source_snapshot_date': self.source_snapshot_date.isoformat() if self.source_snapshot_date else None,
            'last_push_date': self.last_push_date.isoformat() if self.last_push_date else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

    def __repr__(self):
        return f"<StagingEnvironment {self.id}: {self.staging_domain}>"


# =============================================================================
# Server Model
# =============================================================================

class Server:
    """Server model for multi-server provisioning"""

    STATUSES = ['active', 'maintenance', 'offline']
    HEARTBEAT_TIMEOUT_SECONDS = 120  # 2 minutes

    def __init__(self, id=None, name=None, hostname=None, ip_address=None,
                 status='active', max_customers=50, port_range_start=8001,
                 port_range_end=8100, redis_queue_name=None, last_heartbeat=None,
                 created_at=None, updated_at=None):
        self.id = id
        self.name = name
        self.hostname = hostname
        self.ip_address = ip_address
        self.status = status
        self.max_customers = max_customers
        self.port_range_start = port_range_start
        self.port_range_end = port_range_end
        self.redis_queue_name = redis_queue_name
        self.last_heartbeat = last_heartbeat
        self.created_at = created_at
        self.updated_at = updated_at

    def save(self):
        """Save server to database (insert or update)"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            if self.id is None:
                cursor.execute("""
                    INSERT INTO servers
                    (name, hostname, ip_address, status, max_customers,
                     port_range_start, port_range_end, redis_queue_name)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    self.name, self.hostname, self.ip_address, self.status,
                    self.max_customers, self.port_range_start, self.port_range_end,
                    self.redis_queue_name
                ))
                self.id = cursor.lastrowid
            else:
                cursor.execute("""
                    UPDATE servers SET
                        name = %s, hostname = %s, ip_address = %s, status = %s,
                        max_customers = %s, port_range_start = %s, port_range_end = %s,
                        redis_queue_name = %s
                    WHERE id = %s
                """, (
                    self.name, self.hostname, self.ip_address, self.status,
                    self.max_customers, self.port_range_start, self.port_range_end,
                    self.redis_queue_name, self.id
                ))

            conn.commit()
            return self
        finally:
            cursor.close()
            conn.close()

    def delete(self):
        """Delete server from database (only if no customers assigned)"""
        if self.id is None:
            return False

        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            # Check for assigned customers
            cursor.execute("SELECT COUNT(*) FROM customers WHERE server_id = %s", (self.id,))
            if cursor.fetchone()[0] > 0:
                raise ValueError("Cannot delete server with assigned customers")

            cursor.execute("DELETE FROM servers WHERE id = %s", (self.id,))
            conn.commit()
            return cursor.rowcount > 0
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_by_id(server_id):
        """Get server by ID"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("SELECT * FROM servers WHERE id = %s", (server_id,))
            row = cursor.fetchone()
            return Server(**row) if row else None
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_by_hostname(hostname):
        """Get server by hostname"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("SELECT * FROM servers WHERE hostname = %s", (hostname,))
            row = cursor.fetchone()
            return Server(**row) if row else None
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_all():
        """Get all servers"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("SELECT * FROM servers ORDER BY name")
            return [Server(**row) for row in cursor.fetchall()]
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_active():
        """Get all active servers"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("SELECT * FROM servers WHERE status = 'active' ORDER BY name")
            return [Server(**row) for row in cursor.fetchall()]
        finally:
            cursor.close()
            conn.close()

    def get_customer_count(self):
        """Get count of customers assigned to this server"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("SELECT COUNT(*) FROM customers WHERE server_id = %s", (self.id,))
            return cursor.fetchone()[0]
        finally:
            cursor.close()
            conn.close()

    def get_used_ports(self):
        """Get list of ports in use on this server"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT web_port FROM customers
                WHERE server_id = %s AND web_port IS NOT NULL
            """, (self.id,))
            return [row[0] for row in cursor.fetchall()]
        finally:
            cursor.close()
            conn.close()

    def get_available_port(self):
        """Get next available port for this server"""
        used_ports = set(self.get_used_ports())

        for port in range(self.port_range_start, self.port_range_end + 1):
            if port not in used_ports:
                return port

        return None  # No ports available

    def get_port_capacity(self):
        """Get port utilization info"""
        used_ports = self.get_used_ports()
        total_ports = self.port_range_end - self.port_range_start + 1

        return {
            'total': total_ports,
            'used': len(used_ports),
            'available': total_ports - len(used_ports),
            'used_ports': used_ports
        }

    def has_capacity(self):
        """Check if server has capacity for more customers"""
        customer_count = self.get_customer_count()
        available_port = self.get_available_port()

        return (customer_count < self.max_customers and
                available_port is not None)

    def update_heartbeat(self):
        """Update server heartbeat timestamp"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute(
                "UPDATE servers SET last_heartbeat = NOW() WHERE id = %s",
                (self.id,)
            )
            conn.commit()
            self.last_heartbeat = datetime.now()
        finally:
            cursor.close()
            conn.close()

    def is_healthy(self):
        """Check if server heartbeat is recent"""
        if not self.last_heartbeat:
            return False

        age = (datetime.now() - self.last_heartbeat).total_seconds()
        return age < self.HEARTBEAT_TIMEOUT_SECONDS

    def get_queue_name(self):
        """Get Redis queue name for this server"""
        if self.redis_queue_name:
            return self.redis_queue_name
        return f"provisioning:server-{self.id}"

    def to_dict(self):
        """Convert to dictionary"""
        return {
            'id': self.id,
            'name': self.name,
            'hostname': self.hostname,
            'ip_address': self.ip_address,
            'status': self.status,
            'max_customers': self.max_customers,
            'port_range_start': self.port_range_start,
            'port_range_end': self.port_range_end,
            'redis_queue_name': self.redis_queue_name,
            'last_heartbeat': self.last_heartbeat.isoformat() if self.last_heartbeat else None,
            'is_healthy': self.is_healthy(),
            'customer_count': self.get_customer_count(),
            'has_capacity': self.has_capacity(),
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

    def __repr__(self):
        return f"<Server {self.id}: {self.name} ({self.hostname})>"


# =============================================================================
# Server Selector
# =============================================================================

class ServerSelector:
    """Selects the best server for new customer provisioning"""

    @staticmethod
    def select_server(require_healthy=True):
        """
        Select the best available server for a new customer.

        Strategy:
        1. Filter to active servers
        2. Optionally filter to healthy servers (recent heartbeat)
        3. Filter to servers with available capacity
        4. Sort by current customer count (ascending)
        5. Return server with lowest load

        Args:
            require_healthy: If True, only consider servers with recent heartbeat

        Returns:
            Server object, or None if no suitable server found
        """
        servers = Server.get_active()

        if not servers:
            return None

        # Filter and score servers
        candidates = []
        for server in servers:
            # Skip unhealthy servers if required
            if require_healthy and not server.is_healthy():
                continue

            # Skip servers at capacity
            if not server.has_capacity():
                continue

            customer_count = server.get_customer_count()
            candidates.append((server, customer_count))

        if not candidates:
            # If no healthy servers, try again without health requirement
            if require_healthy:
                return ServerSelector.select_server(require_healthy=False)
            return None

        # Sort by customer count (lowest first) and return best
        candidates.sort(key=lambda x: x[1])
        return candidates[0][0]

    @staticmethod
    def get_server_stats():
        """Get statistics for all servers"""
        servers = Server.get_all()

        stats = {
            'total': len(servers),
            'active': 0,
            'maintenance': 0,
            'offline': 0,
            'healthy': 0,
            'total_capacity': 0,
            'total_customers': 0,
            'servers': []
        }

        for server in servers:
            if server.status == 'active':
                stats['active'] += 1
            elif server.status == 'maintenance':
                stats['maintenance'] += 1
            else:
                stats['offline'] += 1

            if server.is_healthy():
                stats['healthy'] += 1

            customer_count = server.get_customer_count()
            stats['total_capacity'] += server.max_customers
            stats['total_customers'] += customer_count

            stats['servers'].append({
                'id': server.id,
                'name': server.name,
                'status': server.status,
                'is_healthy': server.is_healthy(),
                'customer_count': customer_count,
                'max_customers': server.max_customers,
                'utilization': round(customer_count / server.max_customers * 100, 1) if server.max_customers > 0 else 0
            })

        return stats


# =============================================================================
# Monitoring Models
# =============================================================================

class MonitoringCheck:
    """Individual monitoring check result"""

    def __init__(self, id=None, customer_id=None, check_type=None, status=None,
                 response_time_ms=None, details=None, checked_at=None):
        self.id = id
        self.customer_id = customer_id
        self.check_type = check_type
        self.status = status
        self.response_time_ms = response_time_ms
        self.details = details if details else {}
        self.checked_at = checked_at or datetime.now()

    def save(self):
        """Store check result in database"""
        import json
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            details_json = json.dumps(self.details) if self.details else None
            cursor.execute("""
                INSERT INTO monitoring_checks
                (customer_id, check_type, status, response_time_ms, details, checked_at)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                self.customer_id, self.check_type, self.status,
                self.response_time_ms, details_json, self.checked_at
            ))
            self.id = cursor.lastrowid
            conn.commit()
            return self
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_recent_by_customer(customer_id, hours=24):
        """Get recent checks for a customer"""
        import json
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("""
                SELECT * FROM monitoring_checks
                WHERE customer_id = %s AND checked_at > DATE_SUB(NOW(), INTERVAL %s HOUR)
                ORDER BY checked_at DESC
            """, (customer_id, hours))
            rows = cursor.fetchall()
            checks = []
            for row in rows:
                if row.get('details') and isinstance(row['details'], str):
                    row['details'] = json.loads(row['details'])
                checks.append(MonitoringCheck(**row))
            return checks
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def cleanup_old_checks(hours=48):
        """Delete checks older than specified hours"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                DELETE FROM monitoring_checks
                WHERE checked_at < DATE_SUB(NOW(), INTERVAL %s HOUR)
            """, (hours,))
            deleted = cursor.rowcount
            conn.commit()
            return deleted
        finally:
            cursor.close()
            conn.close()

    def __repr__(self):
        return f"<MonitoringCheck {self.id}: {self.customer_id} {self.check_type}={self.status}>"


class CustomerMonitoringStatus:
    """Current monitoring status for a customer (one row per customer)"""

    def __init__(self, customer_id=None, http_status='unknown', container_status='unknown',
                 last_http_check=None, last_container_check=None, last_http_response_ms=None,
                 cpu_percent=None, memory_percent=None, memory_usage_mb=None, disk_usage_mb=None,
                 uptime_24h=0.00, consecutive_failures=0, last_state_change=None,
                 last_alert_sent=None, updated_at=None):
        self.customer_id = customer_id
        self.http_status = http_status
        self.container_status = container_status
        self.last_http_check = last_http_check
        self.last_container_check = last_container_check
        self.last_http_response_ms = last_http_response_ms
        self.cpu_percent = cpu_percent
        self.memory_percent = memory_percent
        self.memory_usage_mb = memory_usage_mb
        self.disk_usage_mb = disk_usage_mb
        self.uptime_24h = uptime_24h
        self.consecutive_failures = consecutive_failures
        self.last_state_change = last_state_change
        self.last_alert_sent = last_alert_sent
        self.updated_at = updated_at

    @staticmethod
    def get_or_create(customer_id):
        """Get existing status or create new one"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("""
                SELECT * FROM customer_monitoring_status WHERE customer_id = %s
            """, (customer_id,))
            row = cursor.fetchone()

            if row:
                return CustomerMonitoringStatus(**row)

            # Create new status record
            cursor.execute("""
                INSERT INTO customer_monitoring_status (customer_id) VALUES (%s)
            """, (customer_id,))
            conn.commit()

            return CustomerMonitoringStatus(customer_id=customer_id)
        finally:
            cursor.close()
            conn.close()

    def update_http_status(self, status, response_time_ms=None):
        """Update HTTP check status"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            # Track state changes
            state_change_sql = ""
            if status != self.http_status:
                state_change_sql = ", last_state_change = NOW()"

            cursor.execute(f"""
                UPDATE customer_monitoring_status
                SET http_status = %s, last_http_check = NOW(), last_http_response_ms = %s{state_change_sql}
                WHERE customer_id = %s
            """, (status, response_time_ms, self.customer_id))
            conn.commit()

            self.http_status = status
            self.last_http_response_ms = response_time_ms
            self.last_http_check = datetime.now()
        finally:
            cursor.close()
            conn.close()

    def update_container_status(self, status, cpu_percent=None, memory_mb=None, disk_mb=None):
        """Update container and resource metrics"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                UPDATE customer_monitoring_status
                SET container_status = %s, last_container_check = NOW(),
                    cpu_percent = %s, memory_usage_mb = %s, disk_usage_mb = %s
                WHERE customer_id = %s
            """, (status, cpu_percent, memory_mb, disk_mb, self.customer_id))
            conn.commit()

            self.container_status = status
            self.cpu_percent = cpu_percent
            self.memory_usage_mb = memory_mb
            self.disk_usage_mb = disk_mb
            self.last_container_check = datetime.now()
        finally:
            cursor.close()
            conn.close()

    def calculate_uptime_24h(self):
        """Calculate uptime percentage from last 24h of checks"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'up' THEN 1 ELSE 0 END) as up_count
                FROM monitoring_checks
                WHERE customer_id = %s
                    AND check_type = 'http'
                    AND checked_at > DATE_SUB(NOW(), INTERVAL 24 HOUR)
            """, (self.customer_id,))
            row = cursor.fetchone()

            if row and row['total'] > 0:
                uptime = (row['up_count'] / row['total']) * 100
            else:
                uptime = 0.0

            # Update the stored uptime
            cursor.execute("""
                UPDATE customer_monitoring_status SET uptime_24h = %s WHERE customer_id = %s
            """, (uptime, self.customer_id))
            conn.commit()

            self.uptime_24h = uptime
            return uptime
        finally:
            cursor.close()
            conn.close()

    def increment_failures(self):
        """Increment consecutive failure count"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                UPDATE customer_monitoring_status
                SET consecutive_failures = consecutive_failures + 1
                WHERE customer_id = %s
            """, (self.customer_id,))
            conn.commit()
            self.consecutive_failures += 1
        finally:
            cursor.close()
            conn.close()

    def reset_failures(self):
        """Reset consecutive failure count"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                UPDATE customer_monitoring_status
                SET consecutive_failures = 0
                WHERE customer_id = %s
            """, (self.customer_id,))
            conn.commit()
            self.consecutive_failures = 0
        finally:
            cursor.close()
            conn.close()

    def should_alert(self, threshold=3, cooldown_seconds=300):
        """Check if alert should be sent (threshold failures, cooldown period)"""
        if self.consecutive_failures < threshold:
            return False

        if self.last_alert_sent:
            elapsed = (datetime.now() - self.last_alert_sent).total_seconds()
            if elapsed < cooldown_seconds:
                return False

        return True

    def mark_alert_sent(self):
        """Update last_alert_sent timestamp"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                UPDATE customer_monitoring_status
                SET last_alert_sent = NOW()
                WHERE customer_id = %s
            """, (self.customer_id,))
            conn.commit()
            self.last_alert_sent = datetime.now()
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_all_statuses():
        """Get all customer monitoring statuses with customer info"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("""
                SELECT cms.*, c.email, c.domain, c.company_name, c.status as customer_status
                FROM customer_monitoring_status cms
                JOIN customers c ON cms.customer_id = c.id
                WHERE c.status = 'active'
                ORDER BY
                    CASE cms.http_status
                        WHEN 'down' THEN 1
                        WHEN 'degraded' THEN 2
                        WHEN 'unknown' THEN 3
                        ELSE 4
                    END,
                    c.domain
            """)
            return cursor.fetchall()
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_summary_stats():
        """Get aggregate monitoring statistics"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN http_status = 'up' AND container_status = 'up' THEN 1 ELSE 0 END) as up,
                    SUM(CASE WHEN http_status = 'down' OR container_status = 'down' THEN 1 ELSE 0 END) as down,
                    SUM(CASE WHEN http_status = 'degraded' OR container_status = 'degraded' THEN 1 ELSE 0 END) as degraded,
                    SUM(CASE WHEN http_status = 'unknown' OR container_status = 'unknown' THEN 1 ELSE 0 END) as unknown,
                    AVG(uptime_24h) as avg_uptime,
                    AVG(last_http_response_ms) as avg_response_time
                FROM customer_monitoring_status cms
                JOIN customers c ON cms.customer_id = c.id
                WHERE c.status = 'active'
            """)
            row = cursor.fetchone()
            return {
                'total': row['total'] or 0,
                'up': row['up'] or 0,
                'down': row['down'] or 0,
                'degraded': row['degraded'] or 0,
                'unknown': row['unknown'] or 0,
                'avg_uptime': round(row['avg_uptime'] or 0, 2),
                'avg_response_time': int(row['avg_response_time'] or 0)
            }
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_down_count():
        """Get count of customers with down status"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT COUNT(*) FROM customer_monitoring_status cms
                JOIN customers c ON cms.customer_id = c.id
                WHERE c.status = 'active'
                AND (cms.http_status = 'down' OR cms.container_status = 'down')
            """)
            return cursor.fetchone()[0]
        finally:
            cursor.close()
            conn.close()

    def __repr__(self):
        return f"<CustomerMonitoringStatus {self.customer_id}: http={self.http_status}>"


class MonitoringAlert:
    """Alert record for monitoring events"""

    def __init__(self, id=None, customer_id=None, alert_type=None, message=None,
                 details=None, email_sent=False, acknowledged=False,
                 acknowledged_by=None, acknowledged_at=None, created_at=None):
        self.id = id
        self.customer_id = customer_id
        self.alert_type = alert_type
        self.message = message
        self.details = details if details else {}
        self.email_sent = email_sent
        self.acknowledged = acknowledged
        self.acknowledged_by = acknowledged_by
        self.acknowledged_at = acknowledged_at
        self.created_at = created_at or datetime.now()

    def save(self):
        """Create alert record"""
        import json
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            details_json = json.dumps(self.details) if self.details else None
            cursor.execute("""
                INSERT INTO monitoring_alerts
                (customer_id, alert_type, message, details, created_at)
                VALUES (%s, %s, %s, %s, %s)
            """, (
                self.customer_id, self.alert_type, self.message,
                details_json, self.created_at
            ))
            self.id = cursor.lastrowid
            conn.commit()
            return self
        finally:
            cursor.close()
            conn.close()

    def mark_email_sent(self):
        """Mark alert as email sent"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                UPDATE monitoring_alerts SET email_sent = TRUE WHERE id = %s
            """, (self.id,))
            conn.commit()
            self.email_sent = True
        finally:
            cursor.close()
            conn.close()

    def acknowledge(self, admin_id):
        """Mark alert as acknowledged"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                UPDATE monitoring_alerts
                SET acknowledged = TRUE, acknowledged_by = %s, acknowledged_at = NOW()
                WHERE id = %s
            """, (admin_id, self.id))
            conn.commit()
            self.acknowledged = True
            self.acknowledged_by = admin_id
            self.acknowledged_at = datetime.now()
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_by_id(alert_id):
        """Get alert by ID"""
        import json
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("SELECT * FROM monitoring_alerts WHERE id = %s", (alert_id,))
            row = cursor.fetchone()
            if row:
                if row.get('details') and isinstance(row['details'], str):
                    row['details'] = json.loads(row['details'])
                return MonitoringAlert(**row)
            return None
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_unacknowledged(limit=50):
        """Get unacknowledged alerts"""
        import json
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("""
                SELECT ma.*, c.email, c.domain, c.company_name
                FROM monitoring_alerts ma
                JOIN customers c ON ma.customer_id = c.id
                WHERE ma.acknowledged = FALSE
                ORDER BY ma.created_at DESC
                LIMIT %s
            """, (limit,))
            rows = cursor.fetchall()
            for row in rows:
                if row.get('details') and isinstance(row['details'], str):
                    row['details'] = json.loads(row['details'])
            return rows
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_recent(limit=50, offset=0):
        """Get recent alerts with pagination"""
        import json
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("""
                SELECT ma.*, c.email, c.domain, c.company_name
                FROM monitoring_alerts ma
                JOIN customers c ON ma.customer_id = c.id
                ORDER BY ma.created_at DESC
                LIMIT %s OFFSET %s
            """, (limit, offset))
            rows = cursor.fetchall()
            for row in rows:
                if row.get('details') and isinstance(row['details'], str):
                    row['details'] = json.loads(row['details'])
            return rows
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_by_customer(customer_id, limit=20):
        """Get alerts for a specific customer"""
        import json
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("""
                SELECT * FROM monitoring_alerts
                WHERE customer_id = %s
                ORDER BY created_at DESC
                LIMIT %s
            """, (customer_id, limit))
            rows = cursor.fetchall()
            alerts = []
            for row in rows:
                if row.get('details') and isinstance(row['details'], str):
                    row['details'] = json.loads(row['details'])
                alerts.append(MonitoringAlert(**row))
            return alerts
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_unacknowledged_count():
        """Get count of unacknowledged alerts"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT COUNT(*) FROM monitoring_alerts WHERE acknowledged = FALSE
            """)
            return cursor.fetchone()[0]
        finally:
            cursor.close()
            conn.close()

    def __repr__(self):
        return f"<MonitoringAlert {self.id}: {self.alert_type} for customer {self.customer_id}>"
