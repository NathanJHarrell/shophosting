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


# =============================================================================
# Revenue Reports (Phase 3)
# =============================================================================

@billing_bp.route('/revenue')
@admin_required
@require_revenue_access
def revenue():
    """Revenue reports dashboard"""
    admin = get_current_admin()
    role = session.get('admin_user_role')

    # Get date range from params (default: last 30 days)
    days = int(request.args.get('days', 30))
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)

    summary = BillingService.get_revenue_summary(start_date, end_date)

    # Get MRR trend data
    mrr_data = get_mrr_trend(days=days)

    # Get plan distribution
    plan_distribution = get_plan_distribution()

    return render_template('admin/billing/revenue/reports.html',
                           admin=admin,
                           admin_role=role,
                           summary=summary,
                           mrr_data=mrr_data,
                           plan_distribution=plan_distribution,
                           days=days)


def get_mrr_trend(days=30):
    """Get MRR trend data for charts"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT DATE(paid_at) as date, SUM(amount_paid) as revenue
            FROM invoices
            WHERE status = 'paid'
              AND paid_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
            GROUP BY DATE(paid_at)
            ORDER BY date
        """, (days,))
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()


def get_plan_distribution():
    """Get subscription distribution by plan"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT p.name as plan_name, COUNT(s.id) as count,
                   SUM(p.price_monthly) as monthly_revenue
            FROM subscriptions s
            JOIN pricing_plans p ON s.plan_id = p.id
            WHERE s.status = 'active'
            GROUP BY p.id, p.name
            ORDER BY count DESC
        """)
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()


@billing_bp.route('/revenue/api/mrr')
@admin_required
@require_revenue_access
def revenue_api_mrr():
    """API endpoint for MRR chart data"""
    days = int(request.args.get('days', 30))
    data = get_mrr_trend(days=days)

    return jsonify({
        'labels': [row['date'].strftime('%Y-%m-%d') if row['date'] else '' for row in data],
        'values': [float(row['revenue'] or 0) / 100 for row in data]
    })


@billing_bp.route('/revenue/export')
@admin_required
@require_revenue_access
def revenue_export():
    """Export revenue data as CSV"""
    from flask import Response
    import csv
    import io

    days = int(request.args.get('days', 30))
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT i.stripe_invoice_id, c.email as customer_email,
                   i.amount_due, i.amount_paid, i.status,
                   i.created_at, i.paid_at
            FROM invoices i
            LEFT JOIN customers c ON i.customer_id = c.id
            WHERE i.created_at BETWEEN %s AND %s
            ORDER BY i.created_at DESC
        """, (start_date, end_date))
        rows = cursor.fetchall()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Invoice ID', 'Customer Email', 'Amount Due', 'Amount Paid', 'Status', 'Created', 'Paid'])

        for row in rows:
            writer.writerow([
                row['stripe_invoice_id'],
                row['customer_email'],
                f"${row['amount_due']/100:.2f}",
                f"${row['amount_paid']/100:.2f}",
                row['status'],
                row['created_at'].strftime('%Y-%m-%d %H:%M') if row['created_at'] else '',
                row['paid_at'].strftime('%Y-%m-%d %H:%M') if row['paid_at'] else ''
            ])

        output.seek(0)
        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename=revenue_export_{start_date.strftime("%Y%m%d")}_{end_date.strftime("%Y%m%d")}.csv'}
        )
    finally:
        cursor.close()
        conn.close()


# =============================================================================
# Manual Invoice Creation (Phase 3)
# =============================================================================

@billing_bp.route('/invoices/create', methods=['GET', 'POST'])
@admin_required
@require_billing_write
def create_invoice():
    """Create manual invoice"""
    admin = get_current_admin()
    role = session.get('admin_user_role')

    if request.method == 'POST':
        customer_id = request.form.get('customer_id')
        amount_str = request.form.get('amount', '0')
        description = request.form.get('description', '').strip()
        notes = request.form.get('notes', '').strip()

        if not customer_id:
            flash('Please select a customer.', 'error')
            return redirect(url_for('admin_billing.create_invoice'))

        try:
            amount_cents = int(float(amount_str) * 100)
        except ValueError:
            flash('Invalid amount.', 'error')
            return redirect(url_for('admin_billing.create_invoice'))

        if amount_cents <= 0:
            flash('Amount must be positive.', 'error')
            return redirect(url_for('admin_billing.create_invoice'))

        try:
            result = create_manual_invoice(
                admin_id=admin.id,
                customer_id=int(customer_id),
                amount_cents=amount_cents,
                description=description,
                notes=notes,
                ip_address=request.remote_addr
            )
            flash(f'Manual invoice created successfully.', 'success')
            return redirect(url_for('admin_billing.invoice_detail', invoice_id=result['invoice_id']))
        except Exception as e:
            logger.error(f"Manual invoice creation error: {e}")
            flash(f'Failed to create invoice: {str(e)}', 'error')

    # GET: show form
    customers = get_customers_for_select()

    return render_template('admin/billing/invoices/create.html',
                           admin=admin,
                           admin_role=role,
                           customers=customers)


def get_customers_for_select():
    """Get customers for dropdown"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT id, email, company_name, stripe_customer_id
            FROM customers
            WHERE status = 'active'
            ORDER BY email
        """)
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()


def create_manual_invoice(admin_id, customer_id, amount_cents, description, notes, ip_address):
    """Create a manual invoice via Stripe"""
    import stripe
    from stripe_integration.config import is_stripe_configured

    if not is_stripe_configured():
        raise BillingServiceError("Stripe is not configured")

    customer = Customer.get_by_id(customer_id)
    if not customer:
        raise BillingServiceError(f"Customer {customer_id} not found")

    if not customer.stripe_customer_id:
        raise BillingServiceError("Customer has no Stripe account")

    try:
        # Create invoice item
        stripe.InvoiceItem.create(
            customer=customer.stripe_customer_id,
            amount=amount_cents,
            currency='usd',
            description=description or 'Manual charge'
        )

        # Create and finalize invoice
        stripe_invoice = stripe.Invoice.create(
            customer=customer.stripe_customer_id,
            auto_advance=True,  # Auto-finalize
            metadata={
                'manual': 'true',
                'created_by_admin': str(admin_id)
            }
        )

        # Finalize the invoice
        stripe_invoice = stripe.Invoice.finalize_invoice(stripe_invoice.id)

        # Save to local database
        invoice = Invoice(
            customer_id=customer_id,
            stripe_invoice_id=stripe_invoice.id,
            amount_due=amount_cents,
            amount_paid=0,
            currency='usd',
            status=stripe_invoice.status,
            hosted_invoice_url=stripe_invoice.hosted_invoice_url,
            invoice_pdf_url=stripe_invoice.invoice_pdf
        )
        invoice.save()

        # Update with manual flag and notes
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                UPDATE invoices SET manual = TRUE, notes = %s, created_by_admin_id = %s
                WHERE id = %s
            """, (notes, admin_id, invoice.id))
            conn.commit()
        finally:
            cursor.close()
            conn.close()

        # Log the action
        log = BillingAuditLog(
            admin_user_id=admin_id,
            action_type='invoice_create',
            target_customer_id=customer_id,
            target_invoice_id=invoice.id,
            amount_cents=amount_cents,
            after_state={'invoice_id': invoice.id, 'stripe_invoice_id': stripe_invoice.id},
            ip_address=ip_address
        )
        log.save()

        return {'success': True, 'invoice_id': invoice.id}

    except stripe.error.StripeError as e:
        raise BillingServiceError(f"Stripe error: {str(e)}")


# =============================================================================
# Coupon Management (Phase 3)
# =============================================================================

@billing_bp.route('/coupons')
@admin_required
@require_billing_write
def coupons():
    """List and manage coupons"""
    admin = get_current_admin()
    role = session.get('admin_user_role')

    stripe_coupons = get_stripe_coupons()

    return render_template('admin/billing/coupons/list.html',
                           admin=admin,
                           admin_role=role,
                           coupons=stripe_coupons)


def get_stripe_coupons():
    """Get coupons from Stripe"""
    import stripe
    from stripe_integration.config import is_stripe_configured

    if not is_stripe_configured():
        return []

    try:
        coupons = stripe.Coupon.list(limit=100)
        return coupons.data
    except stripe.error.StripeError as e:
        logger.error(f"Error fetching coupons: {e}")
        return []


@billing_bp.route('/coupons/apply', methods=['POST'])
@admin_required
@require_billing_write
def apply_coupon():
    """Apply coupon to customer subscription"""
    admin = get_current_admin()

    subscription_id = request.form.get('subscription_id')
    coupon_id = request.form.get('coupon_id')

    if not subscription_id or not coupon_id:
        flash('Subscription and coupon are required.', 'error')
        return redirect(url_for('admin_billing.coupons'))

    subscription = Subscription.get_by_id(int(subscription_id))
    if not subscription:
        flash('Subscription not found.', 'error')
        return redirect(url_for('admin_billing.coupons'))

    try:
        import stripe
        stripe.Subscription.modify(
            subscription.stripe_subscription_id,
            coupon=coupon_id
        )

        # Log the action
        log = BillingAuditLog(
            admin_user_id=admin.id,
            action_type='coupon_apply',
            target_customer_id=subscription.customer_id,
            target_subscription_id=subscription.id,
            after_state={'coupon_id': coupon_id},
            ip_address=request.remote_addr
        )
        log.save()

        flash(f'Coupon {coupon_id} applied successfully.', 'success')
    except Exception as e:
        logger.error(f"Error applying coupon: {e}")
        flash(f'Failed to apply coupon: {str(e)}', 'error')

    return redirect(url_for('admin_billing.subscription_detail', subscription_id=subscription_id))


# =============================================================================
# Payment Method Management (Phase 3)
# =============================================================================

@billing_bp.route('/payment-methods/<int:customer_id>')
@admin_required
@require_billing_read
def payment_methods(customer_id):
    """View customer payment methods"""
    admin = get_current_admin()
    role = session.get('admin_user_role')

    customer = Customer.get_by_id(customer_id)
    if not customer:
        flash('Customer not found.', 'error')
        return redirect(url_for('admin_billing.dashboard'))

    methods = get_customer_payment_methods(customer)

    return render_template('admin/billing/payment_methods.html',
                           admin=admin,
                           admin_role=role,
                           customer=customer,
                           payment_methods=methods)


def get_customer_payment_methods(customer):
    """Get payment methods for a customer from Stripe"""
    import stripe
    from stripe_integration.config import is_stripe_configured

    if not is_stripe_configured() or not customer.stripe_customer_id:
        return []

    try:
        methods = stripe.PaymentMethod.list(
            customer=customer.stripe_customer_id,
            type='card'
        )
        return methods.data
    except stripe.error.StripeError as e:
        logger.error(f"Error fetching payment methods: {e}")
        return []


@billing_bp.route('/payment-methods/<int:customer_id>/<pm_id>/remove', methods=['POST'])
@admin_required
@require_billing_write
def remove_payment_method(customer_id, pm_id):
    """Remove a payment method"""
    admin = get_current_admin()

    customer = Customer.get_by_id(customer_id)
    if not customer:
        flash('Customer not found.', 'error')
        return redirect(url_for('admin_billing.dashboard'))

    try:
        import stripe
        stripe.PaymentMethod.detach(pm_id)

        # Log the action
        log = BillingAuditLog(
            admin_user_id=admin.id,
            action_type='payment_method_update',
            target_customer_id=customer_id,
            before_state={'payment_method_id': pm_id},
            after_state={'action': 'removed'},
            ip_address=request.remote_addr
        )
        log.save()

        flash('Payment method removed.', 'success')
    except Exception as e:
        logger.error(f"Error removing payment method: {e}")
        flash(f'Failed to remove payment method: {str(e)}', 'error')

    return redirect(url_for('admin_billing.payment_methods', customer_id=customer_id))


# =============================================================================
# Billing Settings (Phase 3)
# =============================================================================

@billing_bp.route('/settings', methods=['GET', 'POST'])
@admin_required
@require_billing_admin
def settings():
    """Billing settings (super_admin only)"""
    admin = get_current_admin()
    role = session.get('admin_user_role')

    if request.method == 'POST':
        # Update settings
        settings_to_update = {
            'support_refund_limit_cents': request.form.get('support_refund_limit', '5000'),
            'default_credit_expiry_days': request.form.get('credit_expiry_days', '365'),
            'require_refund_reason': 'true' if request.form.get('require_refund_reason') else 'false',
            'require_credit_reason': 'true' if request.form.get('require_credit_reason') else 'false',
            'enable_manual_invoices': 'true' if request.form.get('enable_manual_invoices') else 'false',
        }

        update_billing_settings(admin.id, settings_to_update, request.remote_addr)
        flash('Billing settings updated.', 'success')
        return redirect(url_for('admin_billing.settings'))

    # GET: show current settings
    current_settings = get_all_billing_settings()

    return render_template('admin/billing/settings.html',
                           admin=admin,
                           admin_role=role,
                           settings=current_settings)


def get_all_billing_settings():
    """Get all billing settings"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("SELECT setting_key, setting_value FROM billing_settings")
        rows = cursor.fetchall()

        settings = {}
        for row in rows:
            try:
                import json
                settings[row['setting_key']] = json.loads(row['setting_value'])
            except:
                settings[row['setting_key']] = row['setting_value']

        return settings
    finally:
        cursor.close()
        conn.close()


def update_billing_settings(admin_id, settings_dict, ip_address):
    """Update billing settings"""
    import json
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        for key, value in settings_dict.items():
            cursor.execute("""
                INSERT INTO billing_settings (setting_key, setting_value, updated_by_admin_id)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    setting_value = VALUES(setting_value),
                    updated_by_admin_id = VALUES(updated_by_admin_id)
            """, (key, json.dumps(value), admin_id))

        conn.commit()

        # Log the settings change
        log = BillingAuditLog(
            admin_user_id=admin_id,
            action_type='settings_change',
            after_state=settings_dict,
            ip_address=ip_address
        )
        log.save()

    finally:
        cursor.close()
        conn.close()


# =============================================================================
# Audit Log Export (Phase 3)
# =============================================================================

@billing_bp.route('/audit-log/export')
@admin_required
@require_revenue_access
def audit_log_export():
    """Export audit log as CSV"""
    from flask import Response
    import csv
    import io

    logs = BillingAuditLog.search(limit=1000)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Date', 'Action', 'Admin', 'Customer', 'Amount', 'Reason', 'IP Address'])

    for log in logs:
        writer.writerow([
            log['created_at'].strftime('%Y-%m-%d %H:%M:%S') if log.get('created_at') else '',
            log.get('action_type', ''),
            log.get('admin_name', ''),
            log.get('customer_email', ''),
            f"${log['amount_cents']/100:.2f}" if log.get('amount_cents') else '',
            log.get('reason', ''),
            log.get('ip_address', '')
        ])

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=billing_audit_log_{datetime.now().strftime("%Y%m%d")}.csv'}
    )
