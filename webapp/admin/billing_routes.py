"""
Admin Billing Routes
Dedicated routes for billing management
"""

import logging
from datetime import datetime, timedelta
from functools import wraps

from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify

from models import get_db_connection, Customer, Subscription, Invoice, PricingPlan
from .models import AdminUser, log_admin_action
from .billing_service import BillingService, BillingAuditLog, CustomerCredit, BillingServiceError
from .permissions import (
    require_billing_read, require_billing_write, require_billing_refund,
    require_revenue_access, require_billing_admin, can_process_refund,
    has_billing_permission, get_refund_limit
)

logger = logging.getLogger(__name__)

billing_bp = Blueprint('admin_billing', __name__, url_prefix='/admin/billing')


def admin_required(f):
    """Require admin authentication"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_user_id'):
            flash('Please log in to access the admin panel.', 'error')
            return redirect(url_for('admin.login'))
        return f(*args, **kwargs)
    return decorated_function


def get_current_admin():
    """Get current logged in admin user"""
    admin_id = session.get('admin_user_id')
    if admin_id:
        return AdminUser.get_by_id(admin_id)
    return None


# =============================================================================
# Billing Dashboard
# =============================================================================

@billing_bp.route('/')
@admin_required
@require_billing_read
def dashboard():
    """Billing dashboard with quick stats"""
    admin = get_current_admin()
    role = session.get('admin_user_role')

    # Get quick stats
    stats = get_billing_stats()

    # Get recent billing actions
    recent_actions = BillingAuditLog.search(limit=10)

    return render_template('admin/billing/dashboard.html',
                           admin=admin,
                           admin_role=role,
                           stats=stats,
                           recent_actions=recent_actions)


def get_billing_stats():
    """Get billing statistics for dashboard"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        # Active subscriptions
        cursor.execute("SELECT COUNT(*) as count FROM subscriptions WHERE status = 'active'")
        active_subs = cursor.fetchone()['count']

        # This month's revenue
        cursor.execute("""
            SELECT COALESCE(SUM(amount_paid), 0) as revenue
            FROM invoices
            WHERE status = 'paid'
              AND paid_at >= DATE_FORMAT(NOW(), '%Y-%m-01')
        """)
        monthly_revenue = cursor.fetchone()['revenue']

        # Pending invoices
        cursor.execute("SELECT COUNT(*) as count FROM invoices WHERE status IN ('open', 'draft')")
        pending_invoices = cursor.fetchone()['count']

        # Failed payments this month
        cursor.execute("""
            SELECT COUNT(*) as count FROM invoices
            WHERE status = 'uncollectible'
              AND created_at >= DATE_FORMAT(NOW(), '%Y-%m-01')
        """)
        failed_payments = cursor.fetchone()['count']

        # Credits issued this month
        cursor.execute("""
            SELECT COALESCE(SUM(amount_cents), 0) as total
            FROM customer_credits
            WHERE created_at >= DATE_FORMAT(NOW(), '%Y-%m-01')
        """)
        credits_row = cursor.fetchone()
        credits_issued = credits_row['total'] if credits_row else 0

        # Refunds this month
        cursor.execute("""
            SELECT COALESCE(SUM(amount_cents), 0) as total, COUNT(*) as count
            FROM billing_audit_log
            WHERE action_type = 'refund'
              AND created_at >= DATE_FORMAT(NOW(), '%Y-%m-01')
        """)
        refunds_row = cursor.fetchone()
        refunds_total = refunds_row['total'] if refunds_row else 0
        refunds_count = refunds_row['count'] if refunds_row else 0

        return {
            'active_subscriptions': active_subs,
            'monthly_revenue_cents': monthly_revenue,
            'pending_invoices': pending_invoices,
            'failed_payments': failed_payments,
            'credits_issued_cents': credits_issued,
            'refunds_total_cents': refunds_total,
            'refunds_count': refunds_count,
        }

    except Exception as e:
        logger.error(f"Error getting billing stats: {e}")
        return {
            'active_subscriptions': 0,
            'monthly_revenue_cents': 0,
            'pending_invoices': 0,
            'failed_payments': 0,
            'credits_issued_cents': 0,
            'refunds_total_cents': 0,
            'refunds_count': 0,
        }
    finally:
        cursor.close()
        conn.close()


# =============================================================================
# Invoice Routes
# =============================================================================

@billing_bp.route('/invoices')
@admin_required
@require_billing_read
def invoices():
    """List all invoices with filters"""
    admin = get_current_admin()
    role = session.get('admin_user_role')

    # Filter params
    status_filter = request.args.get('status', '')
    customer_search = request.args.get('customer', '')
    page = int(request.args.get('page', 1))
    per_page = 25

    # Build query
    invoices_list, total = get_invoices_paginated(
        status=status_filter,
        customer_search=customer_search,
        page=page,
        per_page=per_page
    )

    total_pages = (total + per_page - 1) // per_page

    return render_template('admin/billing/invoices/list.html',
                           admin=admin,
                           admin_role=role,
                           invoices=invoices_list,
                           status_filter=status_filter,
                           customer_search=customer_search,
                           page=page,
                           total_pages=total_pages,
                           total=total)


def get_invoices_paginated(status=None, customer_search=None, page=1, per_page=25):
    """Get paginated invoices with optional filters"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        where_clauses = []
        params = []

        if status:
            where_clauses.append("i.status = %s")
            params.append(status)

        if customer_search:
            where_clauses.append("(c.email LIKE %s OR c.company_name LIKE %s)")
            params.extend([f"%{customer_search}%", f"%{customer_search}%"])

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
        offset = (page - 1) * per_page

        # Get count
        cursor.execute(f"""
            SELECT COUNT(*) as count
            FROM invoices i
            LEFT JOIN customers c ON i.customer_id = c.id
            WHERE {where_sql}
        """, params)
        total = cursor.fetchone()['count']

        # Get invoices
        cursor.execute(f"""
            SELECT i.*, c.email as customer_email, c.company_name as customer_name
            FROM invoices i
            LEFT JOIN customers c ON i.customer_id = c.id
            WHERE {where_sql}
            ORDER BY i.created_at DESC
            LIMIT %s OFFSET %s
        """, params + [per_page, offset])

        invoices = cursor.fetchall()
        return invoices, total

    finally:
        cursor.close()
        conn.close()


@billing_bp.route('/invoices/<int:invoice_id>')
@admin_required
@require_billing_read
def invoice_detail(invoice_id):
    """View invoice detail"""
    admin = get_current_admin()
    role = session.get('admin_user_role')

    invoice = get_invoice_with_details(invoice_id)
    if not invoice:
        flash('Invoice not found.', 'error')
        return redirect(url_for('admin_billing.invoices'))

    # Get refund limit for current role
    refund_limit = get_refund_limit()

    return render_template('admin/billing/invoices/detail.html',
                           admin=admin,
                           admin_role=role,
                           invoice=invoice,
                           refund_limit=refund_limit,
                           can_refund=has_billing_permission('billing_refund'))


def get_invoice_with_details(invoice_id):
    """Get invoice with customer and subscription details"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT i.*,
                   c.email as customer_email, c.company_name as customer_name,
                   c.stripe_customer_id,
                   s.stripe_subscription_id, s.status as subscription_status,
                   p.name as plan_name
            FROM invoices i
            LEFT JOIN customers c ON i.customer_id = c.id
            LEFT JOIN subscriptions s ON i.subscription_id = s.id
            LEFT JOIN pricing_plans p ON s.plan_id = p.id
            WHERE i.id = %s
        """, (invoice_id,))
        return cursor.fetchone()
    finally:
        cursor.close()
        conn.close()


@billing_bp.route('/invoices/<int:invoice_id>/refund', methods=['POST'])
@admin_required
@require_billing_refund()
def refund_invoice(invoice_id):
    """Process refund for invoice"""
    admin = get_current_admin()

    amount_str = request.form.get('amount', '0')
    reason = request.form.get('reason', '').strip()

    if not reason:
        flash('Refund reason is required.', 'error')
        return redirect(url_for('admin_billing.invoice_detail', invoice_id=invoice_id))

    try:
        amount_cents = int(float(amount_str) * 100)
    except ValueError:
        flash('Invalid refund amount.', 'error')
        return redirect(url_for('admin_billing.invoice_detail', invoice_id=invoice_id))

    if not can_process_refund(amount_cents):
        flash('Refund amount exceeds your limit. Please contact a supervisor.', 'error')
        return redirect(url_for('admin_billing.invoice_detail', invoice_id=invoice_id))

    try:
        result = BillingService.process_refund(
            admin_id=admin.id,
            invoice_id=invoice_id,
            amount_cents=amount_cents,
            reason=reason,
            ip_address=request.remote_addr
        )
        flash(f'Refund of ${amount_cents/100:.2f} processed successfully.', 'success')
    except BillingServiceError as e:
        flash(f'Refund failed: {str(e)}', 'error')

    return redirect(url_for('admin_billing.invoice_detail', invoice_id=invoice_id))


@billing_bp.route('/invoices/<int:invoice_id>/retry', methods=['POST'])
@admin_required
def retry_invoice_payment(invoice_id):
    """Retry payment for invoice"""
    admin = get_current_admin()
    role = session.get('admin_user_role')

    if role not in ['support', 'admin', 'super_admin']:
        flash('You do not have permission to retry payments.', 'error')
        return redirect(url_for('admin_billing.invoice_detail', invoice_id=invoice_id))

    try:
        result = BillingService.retry_payment(
            admin_id=admin.id,
            invoice_id=invoice_id,
            ip_address=request.remote_addr
        )
        if result.get('paid'):
            flash('Payment successful! Invoice is now paid.', 'success')
        else:
            flash('Payment retry initiated.', 'info')
    except BillingServiceError as e:
        flash(f'Payment failed: {str(e)}', 'error')

    return redirect(url_for('admin_billing.invoice_detail', invoice_id=invoice_id))


# =============================================================================
# Subscription Routes
# =============================================================================

@billing_bp.route('/subscriptions')
@admin_required
@require_billing_read
def subscriptions():
    """List all subscriptions with filters"""
    admin = get_current_admin()
    role = session.get('admin_user_role')

    status_filter = request.args.get('status', '')
    customer_search = request.args.get('customer', '')
    page = int(request.args.get('page', 1))
    per_page = 25

    subs_list, total = get_subscriptions_paginated(
        status=status_filter,
        customer_search=customer_search,
        page=page,
        per_page=per_page
    )

    total_pages = (total + per_page - 1) // per_page

    return render_template('admin/billing/subscriptions/list.html',
                           admin=admin,
                           admin_role=role,
                           subscriptions=subs_list,
                           status_filter=status_filter,
                           customer_search=customer_search,
                           page=page,
                           total_pages=total_pages,
                           total=total)


def get_subscriptions_paginated(status=None, customer_search=None, page=1, per_page=25):
    """Get paginated subscriptions with optional filters"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        where_clauses = []
        params = []

        if status:
            where_clauses.append("s.status = %s")
            params.append(status)

        if customer_search:
            where_clauses.append("(c.email LIKE %s OR c.company_name LIKE %s)")
            params.extend([f"%{customer_search}%", f"%{customer_search}%"])

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
        offset = (page - 1) * per_page

        cursor.execute(f"""
            SELECT COUNT(*) as count
            FROM subscriptions s
            LEFT JOIN customers c ON s.customer_id = c.id
            WHERE {where_sql}
        """, params)
        total = cursor.fetchone()['count']

        cursor.execute(f"""
            SELECT s.*, c.email as customer_email, c.company_name as customer_name,
                   p.name as plan_name, p.price_monthly
            FROM subscriptions s
            LEFT JOIN customers c ON s.customer_id = c.id
            LEFT JOIN pricing_plans p ON s.plan_id = p.id
            WHERE {where_sql}
            ORDER BY s.created_at DESC
            LIMIT %s OFFSET %s
        """, params + [per_page, offset])

        subs = cursor.fetchall()
        return subs, total

    finally:
        cursor.close()
        conn.close()


@billing_bp.route('/subscriptions/<int:subscription_id>')
@admin_required
@require_billing_read
def subscription_detail(subscription_id):
    """View subscription detail"""
    admin = get_current_admin()
    role = session.get('admin_user_role')

    subscription = get_subscription_with_details(subscription_id)
    if not subscription:
        flash('Subscription not found.', 'error')
        return redirect(url_for('admin_billing.subscriptions'))

    # Get available plans for plan change
    available_plans = PricingPlan.get_all()

    # Get recent invoices for this subscription
    invoices = get_subscription_invoices(subscription_id, limit=10)

    return render_template('admin/billing/subscriptions/detail.html',
                           admin=admin,
                           admin_role=role,
                           subscription=subscription,
                           available_plans=available_plans,
                           invoices=invoices,
                           can_modify=has_billing_permission('billing_write'))


def get_subscription_with_details(subscription_id):
    """Get subscription with customer and plan details"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT s.*,
                   c.id as customer_id, c.email as customer_email,
                   c.company_name as customer_name, c.stripe_customer_id,
                   p.id as plan_id, p.name as plan_name, p.price_monthly,
                   p.stripe_price_id
            FROM subscriptions s
            LEFT JOIN customers c ON s.customer_id = c.id
            LEFT JOIN pricing_plans p ON s.plan_id = p.id
            WHERE s.id = %s
        """, (subscription_id,))
        return cursor.fetchone()
    finally:
        cursor.close()
        conn.close()


def get_subscription_invoices(subscription_id, limit=10):
    """Get invoices for a subscription"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT * FROM invoices
            WHERE subscription_id = %s
            ORDER BY created_at DESC
            LIMIT %s
        """, (subscription_id, limit))
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()


@billing_bp.route('/subscriptions/<int:subscription_id>/change-plan', methods=['POST'])
@admin_required
@require_billing_write
def change_subscription_plan(subscription_id):
    """Change subscription plan"""
    admin = get_current_admin()

    new_plan_id = request.form.get('plan_id')
    reason = request.form.get('reason', '').strip()

    if not new_plan_id:
        flash('Please select a plan.', 'error')
        return redirect(url_for('admin_billing.subscription_detail', subscription_id=subscription_id))

    # Get the new plan's Stripe price ID
    new_plan = PricingPlan.get_by_id(int(new_plan_id))
    if not new_plan or not new_plan.stripe_price_id:
        flash('Invalid plan selected.', 'error')
        return redirect(url_for('admin_billing.subscription_detail', subscription_id=subscription_id))

    try:
        result = BillingService.change_subscription_plan(
            admin_id=admin.id,
            subscription_id=subscription_id,
            new_price_id=new_plan.stripe_price_id,
            reason=reason,
            ip_address=request.remote_addr
        )

        # Update local plan_id
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "UPDATE subscriptions SET plan_id = %s WHERE id = %s",
                (new_plan_id, subscription_id)
            )
            conn.commit()
        finally:
            cursor.close()
            conn.close()

        flash(f'Plan changed to {new_plan.name} successfully.', 'success')
    except BillingServiceError as e:
        flash(f'Plan change failed: {str(e)}', 'error')

    return redirect(url_for('admin_billing.subscription_detail', subscription_id=subscription_id))


@billing_bp.route('/subscriptions/<int:subscription_id>/cancel', methods=['POST'])
@admin_required
@require_billing_write
def cancel_subscription(subscription_id):
    """Cancel subscription"""
    admin = get_current_admin()

    reason = request.form.get('reason', '').strip()
    cancel_immediately = request.form.get('immediately') == 'true'

    if not reason:
        flash('Cancellation reason is required.', 'error')
        return redirect(url_for('admin_billing.subscription_detail', subscription_id=subscription_id))

    try:
        result = BillingService.cancel_subscription(
            admin_id=admin.id,
            subscription_id=subscription_id,
            reason=reason,
            cancel_immediately=cancel_immediately,
            ip_address=request.remote_addr
        )

        if cancel_immediately:
            flash('Subscription cancelled immediately.', 'success')
        else:
            flash('Subscription will be cancelled at end of billing period.', 'success')
    except BillingServiceError as e:
        flash(f'Cancellation failed: {str(e)}', 'error')

    return redirect(url_for('admin_billing.subscription_detail', subscription_id=subscription_id))


@billing_bp.route('/subscriptions/<int:subscription_id>/pause', methods=['POST'])
@admin_required
@require_billing_write
def pause_subscription(subscription_id):
    """Pause subscription (using Stripe pause collection)"""
    admin = get_current_admin()

    reason = request.form.get('reason', '').strip()

    try:
        # Get subscription
        subscription = Subscription.get_by_id(subscription_id)
        if not subscription:
            flash('Subscription not found.', 'error')
            return redirect(url_for('admin_billing.subscriptions'))

        import stripe
        from stripe_integration.config import is_stripe_configured

        if not is_stripe_configured():
            flash('Stripe is not configured.', 'error')
            return redirect(url_for('admin_billing.subscription_detail', subscription_id=subscription_id))

        # Pause collection on the subscription
        stripe.Subscription.modify(
            subscription.stripe_subscription_id,
            pause_collection={'behavior': 'mark_uncollectible'}
        )

        # Log the action
        from .billing_service import BillingAuditLog
        log = BillingAuditLog(
            admin_user_id=admin.id,
            action_type='subscription_pause',
            target_customer_id=subscription.customer_id,
            target_subscription_id=subscription.id,
            before_state={'status': subscription.status},
            after_state={'status': 'paused'},
            reason=reason,
            ip_address=request.remote_addr
        )
        log.save()

        flash('Subscription paused. Payment collection is suspended.', 'success')
    except Exception as e:
        logger.error(f"Pause subscription error: {e}")
        flash(f'Failed to pause subscription: {str(e)}', 'error')

    return redirect(url_for('admin_billing.subscription_detail', subscription_id=subscription_id))


@billing_bp.route('/subscriptions/<int:subscription_id>/resume', methods=['POST'])
@admin_required
@require_billing_write
def resume_subscription(subscription_id):
    """Resume paused subscription"""
    admin = get_current_admin()

    try:
        subscription = Subscription.get_by_id(subscription_id)
        if not subscription:
            flash('Subscription not found.', 'error')
            return redirect(url_for('admin_billing.subscriptions'))

        import stripe
        from stripe_integration.config import is_stripe_configured

        if not is_stripe_configured():
            flash('Stripe is not configured.', 'error')
            return redirect(url_for('admin_billing.subscription_detail', subscription_id=subscription_id))

        # Resume collection
        stripe.Subscription.modify(
            subscription.stripe_subscription_id,
            pause_collection=''  # Empty string to resume
        )

        # Log the action
        from .billing_service import BillingAuditLog
        log = BillingAuditLog(
            admin_user_id=admin.id,
            action_type='subscription_resume',
            target_customer_id=subscription.customer_id,
            target_subscription_id=subscription.id,
            before_state={'status': 'paused'},
            after_state={'status': 'active'},
            ip_address=request.remote_addr
        )
        log.save()

        flash('Subscription resumed. Payment collection is active.', 'success')
    except Exception as e:
        logger.error(f"Resume subscription error: {e}")
        flash(f'Failed to resume subscription: {str(e)}', 'error')

    return redirect(url_for('admin_billing.subscription_detail', subscription_id=subscription_id))


# =============================================================================
# Credits Routes
# =============================================================================

@billing_bp.route('/credits')
@admin_required
@require_billing_read
def credits():
    """List all customer credits"""
    admin = get_current_admin()
    role = session.get('admin_user_role')

    page = int(request.args.get('page', 1))
    per_page = 25

    credits_list, total = get_credits_paginated(page=page, per_page=per_page)
    total_pages = (total + per_page - 1) // per_page

    return render_template('admin/billing/credits/list.html',
                           admin=admin,
                           admin_role=role,
                           credits=credits_list,
                           page=page,
                           total_pages=total_pages,
                           total=total)


def get_credits_paginated(page=1, per_page=25):
    """Get paginated credits"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("SELECT COUNT(*) as count FROM customer_credits")
        total = cursor.fetchone()['count']

        offset = (page - 1) * per_page
        cursor.execute("""
            SELECT cc.*, c.email as customer_email, c.company_name as customer_name,
                   au.full_name as admin_name
            FROM customer_credits cc
            LEFT JOIN customers c ON cc.customer_id = c.id
            LEFT JOIN admin_users au ON cc.created_by_admin_id = au.id
            ORDER BY cc.created_at DESC
            LIMIT %s OFFSET %s
        """, (per_page, offset))

        return cursor.fetchall(), total
    finally:
        cursor.close()
        conn.close()


# =============================================================================
# Audit Log Routes
# =============================================================================

@billing_bp.route('/audit-log')
@admin_required
@require_billing_read
def audit_log():
    """View billing audit log"""
    admin = get_current_admin()
    role = session.get('admin_user_role')

    # Filters
    action_type = request.args.get('action', '')
    admin_filter = request.args.get('admin_id', '')
    page = int(request.args.get('page', 1))
    per_page = 50

    filters = {}
    if action_type:
        filters['action_type'] = action_type
    if admin_filter:
        filters['admin_user_id'] = int(admin_filter)

    offset = (page - 1) * per_page
    logs = BillingAuditLog.search(filters=filters, limit=per_page, offset=offset)

    # Get list of admins for filter dropdown
    admins = AdminUser.get_all()

    return render_template('admin/billing/audit_log.html',
                           admin=admin,
                           admin_role=role,
                           logs=logs,
                           admins=admins,
                           action_type=action_type,
                           admin_filter=admin_filter,
                           page=page)
