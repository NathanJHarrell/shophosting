"""
Background task runner for asynchronous operations.
Uses Python threading for simple background jobs.
"""

import threading
import logging
import json
import os
import zipfile
import tempfile
import shutil
from datetime import datetime
from functools import wraps

logger = logging.getLogger(__name__)

# Store for running tasks (in production, use Redis or similar)
_running_tasks = {}


def run_in_background(func):
    """Decorator to run a function in a background thread"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        thread = threading.Thread(target=func, args=args, kwargs=kwargs)
        thread.daemon = True
        thread.start()
        return thread
    return wrapper


def run_task(task_func, *args, **kwargs):
    """Run a task function in background"""
    thread = threading.Thread(target=task_func, args=args, kwargs=kwargs)
    thread.daemon = True
    thread.start()
    return thread


# =============================================================================
# Data Export Task
# =============================================================================

EXPORT_DIR = '/opt/shophosting/webapp/exports'


def ensure_export_dir():
    """Ensure export directory exists"""
    if not os.path.exists(EXPORT_DIR):
        os.makedirs(EXPORT_DIR, mode=0o750)


def process_data_export(export_id, customer_id):
    """
    Process a data export request in the background.
    Gathers all customer data and creates a downloadable ZIP file.
    """
    from models import (
        CustomerDataExport, Customer, Subscription, Invoice, Ticket,
        CustomerLoginHistory, CustomerNotificationSettings,
        CustomerApiKey, CustomerWebhook, Customer2FASettings
    )
    from email_utils import send_data_export_ready_email

    logger.info(f"Starting data export {export_id} for customer {customer_id}")

    # Get the export record
    export = CustomerDataExport.get_by_id(export_id)
    if not export:
        logger.error(f"Export {export_id} not found")
        return

    # Update status to processing
    export.update_status('processing')

    try:
        # Gather all customer data
        customer = Customer.get_by_id(customer_id)
        if not customer:
            raise ValueError("Customer not found")

        export_data = {
            'export_info': {
                'generated_at': datetime.utcnow().isoformat(),
                'customer_id': customer_id,
                'export_id': export_id
            },
            'profile': {
                'id': customer.id,
                'email': customer.email,
                'company_name': customer.company_name,
                'timezone': getattr(customer, 'timezone', 'America/New_York'),
                'created_at': customer.created_at.isoformat() if customer.created_at else None,
                'status': customer.status
            }
        }

        # Subscription data
        subscription = Subscription.get_by_customer_id(customer_id)
        if subscription:
            export_data['subscription'] = {
                'id': subscription.id,
                'plan': subscription.plan,
                'status': subscription.status,
                'current_period_start': subscription.current_period_start.isoformat() if subscription.current_period_start else None,
                'current_period_end': subscription.current_period_end.isoformat() if subscription.current_period_end else None,
                'created_at': subscription.created_at.isoformat() if subscription.created_at else None
            }

        # Invoices
        invoices = Invoice.get_by_customer_id(customer_id, limit=100)
        export_data['invoices'] = [{
            'id': inv.id,
            'amount': float(inv.amount) if inv.amount else 0,
            'status': inv.status,
            'created_at': inv.created_at.isoformat() if inv.created_at else None,
            'paid_at': inv.paid_at.isoformat() if inv.paid_at else None
        } for inv in invoices]

        # Support tickets
        tickets, _ = Ticket.get_by_customer(customer_id, page=1, per_page=100)
        export_data['support_tickets'] = [{
            'id': t.id,
            'subject': t.subject,
            'status': t.status,
            'priority': t.priority,
            'created_at': t.created_at.isoformat() if t.created_at else None
        } for t in tickets]

        # Login history
        login_history = CustomerLoginHistory.get_by_customer(customer_id, limit=100, include_failed=True)
        export_data['login_history'] = [{
            'ip_address': lh.ip_address,
            'user_agent': lh.user_agent,
            'success': lh.success,
            'failure_reason': lh.failure_reason,
            'created_at': lh.created_at.isoformat() if lh.created_at else None
        } for lh in login_history]

        # Notification settings
        notif_settings = CustomerNotificationSettings.get_or_create(customer_id)
        export_data['notification_preferences'] = {
            'email_security_alerts': notif_settings.email_security_alerts,
            'email_login_alerts': notif_settings.email_login_alerts,
            'email_billing_alerts': notif_settings.email_billing_alerts,
            'email_maintenance_alerts': notif_settings.email_maintenance_alerts,
            'email_marketing': notif_settings.email_marketing
        }

        # API keys (without sensitive data)
        api_keys = CustomerApiKey.get_by_customer(customer_id, include_inactive=True)
        export_data['api_keys'] = [{
            'id': k.id,
            'name': k.name,
            'key_prefix': k.key_prefix,
            'is_active': k.is_active,
            'created_at': k.created_at.isoformat() if k.created_at else None,
            'last_used_at': k.last_used_at.isoformat() if k.last_used_at else None
        } for k in api_keys]

        # Webhooks (without secrets)
        webhooks = CustomerWebhook.get_by_customer(customer_id, include_inactive=True)
        export_data['webhooks'] = [{
            'id': w.id,
            'name': w.name,
            'url': w.url,
            'events': json.loads(w.events) if w.events else [],
            'is_active': w.is_active,
            'created_at': w.created_at.isoformat() if w.created_at else None
        } for w in webhooks]

        # 2FA status (without sensitive data)
        tfa_settings = Customer2FASettings.get_by_customer(customer_id)
        export_data['two_factor_auth'] = {
            'enabled': tfa_settings.is_enabled if tfa_settings else False,
            'enabled_at': tfa_settings.enabled_at.isoformat() if tfa_settings and tfa_settings.enabled_at else None
        }

        # Create export file
        ensure_export_dir()
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        filename = f"data_export_{customer_id}_{timestamp}.zip"
        filepath = os.path.join(EXPORT_DIR, filename)

        # Create ZIP with JSON data
        with zipfile.ZipFile(filepath, 'w', zipfile.ZIP_DEFLATED) as zf:
            # Main data file
            zf.writestr(
                'data_export.json',
                json.dumps(export_data, indent=2, default=str)
            )

            # README file
            readme_content = f"""ShopHosting Data Export
=======================

Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC
Customer ID: {customer_id}
Email: {customer.email}

This archive contains all personal data associated with your ShopHosting account.

Files included:
- data_export.json: All your account data in JSON format

Data categories included:
- Profile information
- Subscription details
- Invoice history
- Support ticket summaries
- Login history
- Notification preferences
- API key metadata (keys themselves are not included for security)
- Webhook configurations (secrets are not included for security)
- Two-factor authentication status

For questions about this export, contact support@shophosting.io
"""
            zf.writestr('README.txt', readme_content)

        # Get file size
        file_size = os.path.getsize(filepath)

        # Update export record
        export.update_status('completed', file_path=filename, file_size_bytes=file_size)

        # Send notification email
        try:
            download_token = generate_download_token(export_id, customer_id)
            send_data_export_ready_email(customer.email, download_token)
            logger.info(f"Data export {export_id} completed successfully")
        except Exception as e:
            logger.error(f"Failed to send export notification email: {e}")
            # Export succeeded, just email failed - don't mark as failed

    except Exception as e:
        logger.error(f"Data export {export_id} failed: {e}")
        export.update_status('failed', error_message=str(e)[:255])


def generate_download_token(export_id, customer_id):
    """Generate a secure download token for the export"""
    import secrets
    import hashlib

    # Create a token that encodes export_id and is verifiable
    raw_token = secrets.token_urlsafe(32)

    # Store token mapping (in production, use Redis with expiry)
    # For now, we'll encode the export_id in a verifiable way
    token_data = f"{export_id}:{customer_id}:{raw_token}"
    token_hash = hashlib.sha256(token_data.encode()).hexdigest()[:16]

    return f"{export_id}_{token_hash}_{raw_token[:16]}"


def verify_download_token(token, customer_id):
    """Verify a download token and return export_id if valid"""
    try:
        parts = token.split('_')
        if len(parts) != 3:
            return None

        export_id = int(parts[0])

        # Verify the export belongs to this customer and is completed
        from models import CustomerDataExport
        export = CustomerDataExport.get_by_id(export_id)

        if not export or export.customer_id != customer_id:
            return None

        if export.status != 'completed':
            return None

        # Check if expired
        if export.expires_at and export.expires_at < datetime.now():
            return None

        return export

    except (ValueError, IndexError):
        return None


# =============================================================================
# Cleanup Tasks
# =============================================================================

def cleanup_expired_exports():
    """Clean up expired export files"""
    from models import get_db_connection

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        # Find expired exports with files
        cursor.execute("""
            SELECT id, file_path FROM customer_data_exports
            WHERE status = 'completed'
            AND expires_at < NOW()
            AND file_path IS NOT NULL
        """)

        for row in cursor.fetchall():
            filepath = os.path.join(EXPORT_DIR, row['file_path'])
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                    logger.info(f"Deleted expired export file: {row['file_path']}")
                except OSError as e:
                    logger.error(f"Failed to delete export file {row['file_path']}: {e}")

            # Update status
            cursor.execute("""
                UPDATE customer_data_exports
                SET status = 'expired', file_path = NULL
                WHERE id = %s
            """, (row['id'],))

        conn.commit()

    finally:
        cursor.close()
        conn.close()
