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
