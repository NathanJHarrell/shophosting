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

    db_config = {
        'host': os.getenv('DB_HOST', 'localhost'),
        'user': os.getenv('DB_USER', 'shophosting_app'),
        'password': os.getenv('DB_PASSWORD', 'YourSecurePasswordHere123!'),
        'database': os.getenv('DB_NAME', 'shophosting_db'),
        'pool_name': 'shophosting_pool',
        'pool_size': 5
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
                 db_name=None, db_user=None, db_password=None,
                 admin_user=None, admin_password=None, error_message=None,
                 stripe_customer_id=None, plan_id=None,
                 created_at=None, updated_at=None):
        self.id = id
        self.email = email
        self.password_hash = password_hash
        self.company_name = company_name
        self.domain = domain
        self.platform = platform
        self.status = status
        self.web_port = web_port
        self.db_name = db_name
        self.db_user = db_user
        self.db_password = db_password
        self.admin_user = admin_user
        self.admin_password = admin_password
        self.error_message = error_message
        self.stripe_customer_id = stripe_customer_id
        self.plan_id = plan_id
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
                     stripe_customer_id, plan_id, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    self.email, self.password_hash, self.company_name,
                    self.domain, self.platform, self.status, self.web_port,
                    self.stripe_customer_id, self.plan_id,
                    self.created_at, self.updated_at
                ))
                self.id = cursor.lastrowid
            else:
                # Update existing customer
                cursor.execute("""
                    UPDATE customers SET
                        email = %s, password_hash = %s, company_name = %s,
                        domain = %s, platform = %s, status = %s, web_port = %s,
                        stripe_customer_id = %s, plan_id = %s, updated_at = %s
                    WHERE id = %s
                """, (
                    self.email, self.password_hash, self.company_name,
                    self.domain, self.platform, self.status, self.web_port,
                    self.stripe_customer_id, self.plan_id,
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
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

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
                 cpu_limit='1.0', is_active=True, display_order=0,
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

    def __repr__(self):
        return f"<PricingPlan {self.id}: {self.slug}>"


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
