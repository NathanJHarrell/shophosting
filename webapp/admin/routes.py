"""
Admin Panel Routes
Handles all admin page routes and actions
"""

import os
import subprocess
import logging
import re
from functools import wraps
from datetime import datetime, timedelta

from flask import render_template, request, redirect, url_for, flash, session, jsonify, current_app
from flask_wtf import FlaskForm
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from wtforms import StringField, PasswordField, SelectField, BooleanField, SubmitField
from wtforms.validators import DataRequired, Email, Length, EqualTo

from . import admin_bp
from .models import AdminUser, log_admin_action

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models import Customer, PortManager, get_db_connection, PricingPlan, Subscription, Invoice
from models import Ticket, TicketMessage, TicketAttachment, TicketCategory, ConsultationAppointment

logger = logging.getLogger(__name__)

# Security logger for admin actions
security_logger = logging.getLogger('security')


# =============================================================================
# Decorators
# =============================================================================

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
# Forms
# =============================================================================

class AdminLoginForm(FlaskForm):
    """Admin login form"""
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired()])


# =============================================================================
# Authentication Routes
# =============================================================================

def get_admin_limiter():
    """Get the limiter from the current app context"""
    from flask import current_app
    return current_app.extensions.get('limiter')


@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    """Admin login page with rate limiting"""
    # Apply rate limiting for admin login (stricter than customer login)
    limiter = get_admin_limiter()
    if limiter:
        # Check rate limit manually - 3 attempts per minute, 10 per hour
        key = f"admin_login:{get_remote_address()}"
        try:
            limiter.check()
        except Exception:
            pass  # Let Flask-Limiter handle the 429 response

    if session.get('admin_user_id'):
        return redirect(url_for('admin.dashboard'))

    form = AdminLoginForm()

    if form.validate_on_submit():
        admin = AdminUser.get_by_email(form.email.data)

        if admin and admin.is_active and admin.check_password(form.password.data):
            session['admin_user_id'] = admin.id
            session['admin_user_name'] = admin.full_name
            session['admin_user_role'] = admin.role
            admin.update_last_login()
            log_admin_action(admin.id, 'admin_login', ip_address=request.remote_addr)
            security_logger.info(
                f"ADMIN_LOGIN_SUCCESS: admin={admin.id} email={admin.email} "
                f"IP={request.remote_addr}"
            )
            flash('Welcome back, {}!'.format(admin.full_name), 'success')
            return redirect(url_for('admin.dashboard'))
        else:
            # Log failed attempt
            security_logger.warning(
                f"ADMIN_LOGIN_FAILED: email={form.email.data} "
                f"IP={request.remote_addr} user_agent={request.user_agent.string[:50]}"
            )
            flash('Invalid email or password.', 'error')

    return render_template('admin/login.html', form=form)


@admin_bp.route('/logout')
def logout():
    """Admin logout"""
    admin_id = session.get('admin_user_id')
    if admin_id:
        log_admin_action(admin_id, 'admin_logout', ip_address=request.remote_addr)

    session.pop('admin_user_id', None)
    session.pop('admin_user_name', None)
    session.pop('admin_user_role', None)
    flash('You have been logged out.', 'info')
    return redirect(url_for('admin.login'))


# =============================================================================
# Dashboard
# =============================================================================

@admin_bp.route('/')
@admin_required
def dashboard():
    """Main admin dashboard"""
    admin = get_current_admin()

    if admin.must_change_password and request.endpoint != 'admin.force_password_change':
        flash('You must change your password before accessing the admin panel.', 'warning')
        return redirect(url_for('admin.force_password_change'))

    stats = get_customer_stats()
    port_usage = PortManager.get_port_usage()
    queue_stats = get_queue_stats()
    recent_customers = get_recent_customers(5)
    failed_customers = get_failed_customers(5)

    return render_template('admin/dashboard.html',
                           admin=admin,
                           stats=stats,
                           port_usage=port_usage,
                           queue_stats=queue_stats,
                           recent_customers=recent_customers,
                           failed_customers=failed_customers)


# =============================================================================
# Customer Management
# =============================================================================

@admin_bp.route('/customers')
@admin_required
def customers():
    """Customer list page"""
    admin = get_current_admin()

    # Get filter parameters
    search = request.args.get('search', '').strip()
    status_filter = request.args.get('status', '')
    platform_filter = request.args.get('platform', '')
    page = int(request.args.get('page', 1))
    per_page = 20

    # Build query
    customers_list, total = get_customers_filtered(
        search=search,
        status=status_filter,
        platform=platform_filter,
        page=page,
        per_page=per_page
    )

    total_pages = (total + per_page - 1) // per_page

    return render_template('admin/customers.html',
                           admin=admin,
                           customers=customers_list,
                           search=search,
                           status_filter=status_filter,
                           platform_filter=platform_filter,
                           page=page,
                           total_pages=total_pages,
                           total=total)


@admin_bp.route('/customers/<int:customer_id>')
@admin_required
def customer_detail(customer_id):
    """Customer detail page"""
    admin = get_current_admin()
    customer = Customer.get_by_id(customer_id)

    if not customer:
        flash('Customer not found.', 'error')
        return redirect(url_for('admin.customers'))

    subscription = Subscription.get_by_customer_id(customer_id)
    invoices = Invoice.get_by_customer_id(customer_id)
    jobs = get_provisioning_jobs(customer_id)
    audit_logs = get_customer_audit_logs(customer_id)
    provisioning_logs = []

    in_progress_job = None
    for job in jobs:
        if job['status'] == 'started':
            in_progress_job = job
            break

    if in_progress_job:
        provisioning_logs = get_provisioning_logs_by_job(in_progress_job['job_id'])

    return render_template('admin/customer_detail.html',
                           admin=admin,
                           customer=customer,
                           subscription=subscription,
                           invoices=invoices,
                           jobs=jobs,
                           audit_logs=audit_logs,
                           provisioning_logs=provisioning_logs,
                           in_progress_job=in_progress_job)


@admin_bp.route('/customers/<int:customer_id>/suspend', methods=['POST'])
@admin_required
def customer_suspend(customer_id):
    """Suspend a customer"""
    admin = get_current_admin()
    customer = Customer.get_by_id(customer_id)

    if not customer:
        flash('Customer not found.', 'error')
        return redirect(url_for('admin.customers'))

    if customer.status == 'suspended':
        flash('Customer is already suspended.', 'warning')
        return redirect(url_for('admin.customer_detail', customer_id=customer_id))

    # Update customer status
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE customers SET status = 'suspended' WHERE id = %s",
            (customer_id,)
        )
        conn.commit()
    finally:
        cursor.close()
        conn.close()

    log_admin_action(admin.id, 'customer_suspend', 'customer', customer_id,
                     f'Suspended customer {customer.email}', request.remote_addr)
    flash(f'Customer {customer.email} has been suspended.', 'success')
    return redirect(url_for('admin.customer_detail', customer_id=customer_id))


@admin_bp.route('/customers/<int:customer_id>/retry-provisioning', methods=['POST'])
@admin_required
def customer_retry_provisioning(customer_id):
    """Retry provisioning for a failed customer"""
    admin = get_current_admin()
    customer = Customer.get_by_id(customer_id)

    if not customer:
        flash('Customer not found.', 'error')
        return redirect(url_for('admin.customers'))

    if customer.status not in ('failed', 'pending'):
        flash('Customer must be in failed or pending status to retry provisioning.', 'warning')
        return redirect(url_for('admin.customer_detail', customer_id=customer_id))

    try:
        # Clean up any existing resources from previous failed attempt
        customer_path = f"/var/customers/customer-{customer_id}"

        # Stop and remove any existing containers
        if os.path.exists(customer_path):
            subprocess.run(
                ['docker', 'compose', 'down', '-v', '--remove-orphans'],
                cwd=customer_path,
                capture_output=True,
                timeout=60
            )
            # Remove customer directory (use sudo because Docker creates files as root/lxd)
            subprocess.run(
                ['sudo', 'rm', '-rf', customer_path],
                capture_output=True,
                timeout=30
            )

        # Remove any existing Nginx config (using sudo)
        nginx_available = f"/etc/nginx/sites-available/customer-{customer_id}.conf"
        nginx_enabled = f"/etc/nginx/sites-enabled/customer-{customer_id}.conf"

        if os.path.exists(nginx_enabled):
            subprocess.run(['sudo', 'rm', '-f', nginx_enabled], capture_output=True)
        if os.path.exists(nginx_available):
            subprocess.run(['sudo', 'rm', '-f', nginx_available], capture_output=True)

        # Reload nginx to apply changes
        subprocess.run(['sudo', 'systemctl', 'reload', 'nginx'], capture_output=True)

        # Update customer status to provisioning
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE customers SET status = 'provisioning', error_message = NULL WHERE id = %s",
            (customer_id,)
        )
        conn.commit()
        cursor.close()
        conn.close()

        # Queue new provisioning job
        import sys
        sys.path.insert(0, '/opt/shophosting/provisioning')
        from enqueue_provisioning import ProvisioningQueue

        queue = ProvisioningQueue(
            redis_host=os.getenv('REDIS_HOST', 'localhost'),
            redis_port=int(os.getenv('REDIS_PORT', 6379))
        )

        customer_data = {
            'customer_id': customer.id,
            'domain': customer.domain,
            'platform': customer.platform,
            'email': customer.email,
            'web_port': customer.web_port,
            'site_title': customer.company_name,
            'memory_limit': os.getenv('DEFAULT_MEMORY_LIMIT', '1g'),
            'cpu_limit': os.getenv('DEFAULT_CPU_LIMIT', '1.0')
        }

        job = queue.enqueue_customer(customer_data)

        # Record the job
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO provisioning_jobs (customer_id, job_id, status)
            VALUES (%s, %s, 'queued')
        """, (customer_id, job.id))
        conn.commit()
        cursor.close()
        conn.close()

        log_admin_action(admin.id, 'retry_provisioning', 'customer', customer_id,
                        f'Retried provisioning for customer {customer.email}', request.remote_addr)
        flash(f'Provisioning has been restarted for {customer.email}.', 'success')
    except Exception as e:
        flash(f'Failed to retry provisioning: {str(e)}', 'error')

    return redirect(url_for('admin.customer_detail', customer_id=customer_id))


@admin_bp.route('/customers/<int:customer_id>/reactivate', methods=['POST'])
@admin_required
def customer_reactivate(customer_id):
    """Reactivate a suspended customer"""
    admin = get_current_admin()
    customer = Customer.get_by_id(customer_id)

    if not customer:
        flash('Customer not found.', 'error')
        return redirect(url_for('admin.customers'))

    if customer.status != 'suspended':
        flash('Customer is not suspended.', 'warning')
        return redirect(url_for('admin.customer_detail', customer_id=customer_id))

    # Update customer status back to active
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE customers SET status = 'active' WHERE id = %s",
            (customer_id,)
        )
        conn.commit()
    finally:
        cursor.close()
        conn.close()

    log_admin_action(admin.id, 'customer_reactivate', 'customer', customer_id,
                     f'Reactivated customer {customer.email}', request.remote_addr)
    flash(f'Customer {customer.email} has been reactivated.', 'success')
    return redirect(url_for('admin.customer_detail', customer_id=customer_id))


# =============================================================================
# Provisioning Monitoring
# =============================================================================

@admin_bp.route('/provisioning')
@admin_required
def provisioning():
    """Provisioning queue monitoring"""
    admin = get_current_admin()

    queue_stats = get_queue_stats()
    recent_jobs = get_all_provisioning_jobs(limit=50)
    failed_jobs = [j for j in recent_jobs if j['status'] == 'failed']

    return render_template('admin/provisioning.html',
                           admin=admin,
                           queue_stats=queue_stats,
                           recent_jobs=recent_jobs,
                           failed_jobs=failed_jobs)


@admin_bp.route('/provisioning/<job_id>/retry', methods=['POST'])
@admin_required
def job_retry(job_id):
    """Retry a failed provisioning job"""
    admin = get_current_admin()

    try:
        from redis import Redis
        from rq import Queue
        from rq.job import Job

        redis_host = os.getenv('REDIS_HOST', 'localhost')
        redis_conn = Redis(host=redis_host, port=6379)
        job = Job.fetch(job_id, connection=redis_conn)
        job.requeue()

        log_admin_action(admin.id, 'job_retry', 'job', None,
                         f'Retried job {job_id}', request.remote_addr)
        flash(f'Job {job_id} has been requeued.', 'success')
    except Exception as e:
        flash(f'Failed to retry job: {str(e)}', 'error')

    return redirect(url_for('admin.provisioning'))


# =============================================================================
# System Health
# =============================================================================

@admin_bp.route('/system')
@admin_required
def system():
    """System health dashboard"""
    admin = get_current_admin()

    services = get_service_status()
    port_usage = PortManager.get_port_usage()
    disk_usage = get_disk_usage()
    backup_status = get_backup_status()

    return render_template('admin/system.html',
                           admin=admin,
                           services=services,
                           port_usage=port_usage,
                           disk_usage=disk_usage,
                           backup_status=backup_status)


# =============================================================================
# System Backups
# =============================================================================

@admin_bp.route('/system/backups')
@admin_required
def system_backups():
    """System backups management page"""
    admin = get_current_admin()

    # Get restic snapshots
    snapshots = []
    try:
        result = subprocess.run(
            ['sudo', 'restic', '-r', 'sftp:sh-backup@15.204.249.219:/home/sh-backup/system',
             '--password-file', '/opt/shophosting/.system-restic-password',
             'snapshots', '--json'],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, 'HOME': '/root', 'XDG_CACHE_HOME': '/root/.cache'}
        )
        if result.returncode == 0 and result.stdout:
            import json
            snapshots = json.loads(result.stdout)
            # Sort by time descending
            snapshots.sort(key=lambda x: x.get('time', ''), reverse=True)
    except Exception as e:
        flash(f'Error fetching snapshots: {str(e)}', 'error')

    return render_template('admin/system_backups.html',
                           admin=admin,
                           snapshots=snapshots)


@admin_bp.route('/system/backup/create', methods=['POST'])
@admin_required
def system_backup_create():
    """Create a new system backup"""
    admin = get_current_admin()

    try:
        # Run backup in background
        subprocess.Popen(
            ['sudo', '/opt/shophosting/scripts/system-backup.sh'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        log_admin_action(admin.id, 'create_system_backup', ip_address=request.remote_addr)
        return {'success': True, 'message': 'Backup started in background'}
    except Exception as e:
        return {'success': False, 'message': str(e)}, 500


@admin_bp.route('/system/backup/restore/<snapshot_id>', methods=['POST'])
@admin_required
def system_backup_restore(snapshot_id):
    """Restore from a system backup snapshot"""
    admin = get_current_admin()

    # Get restore target from form
    target = request.form.get('target', 'all')

    try:
        log_admin_action(admin.id, 'restore_system_backup', 'snapshot', snapshot_id,
                        f'Restoring backup target={target}', request.remote_addr)

        # Run restore script in background
        subprocess.Popen(
            ['sudo', '/opt/shophosting/scripts/system-restore.sh', snapshot_id, '--target', target],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return {'success': True, 'message': f'Restore started for snapshot {snapshot_id}'}
    except Exception as e:
        return {'success': False, 'message': str(e)}, 500


# =============================================================================
# Staging Environments Management
# =============================================================================

@admin_bp.route('/staging')
@admin_required
def staging_list():
    """List all staging environments"""
    admin = get_current_admin()

    page = request.args.get('page', 1, type=int)
    status_filter = request.args.get('status', None)
    customer_filter = request.args.get('customer_id', None, type=int)

    staging_envs, total = StagingEnvironment.get_all(
        include_deleted=request.args.get('show_deleted') == '1',
        page=page,
        per_page=20
    )

    # Get staging statistics
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) as active_count,
                SUM(CASE WHEN status = 'creating' THEN 1 ELSE 0 END) as creating_count,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed_count
            FROM staging_environments
            WHERE status != 'deleted'
        """)
        stats = cursor.fetchone()
    finally:
        cursor.close()
        conn.close()

    # Port usage for staging
    staging_port_usage = {
        'total': StagingPortManager.PORT_RANGE_END - StagingPortManager.PORT_RANGE_START + 1,
        'used': stats['total'] if stats else 0
    }
    staging_port_usage['available'] = staging_port_usage['total'] - staging_port_usage['used']

    return render_template('admin/staging.html',
                           admin=admin,
                           staging_envs=staging_envs,
                           total=total,
                           page=page,
                           stats=stats,
                           staging_port_usage=staging_port_usage)


@admin_bp.route('/staging/<int:staging_id>')
@admin_required
def staging_detail(staging_id):
    """View staging environment details"""
    admin = get_current_admin()

    staging = StagingEnvironment.get_by_id(staging_id)
    if not staging:
        flash('Staging environment not found.', 'error')
        return redirect(url_for('admin.staging_list'))

    customer = Customer.get_by_id(staging.customer_id)
    sync_history = staging.get_sync_history(limit=20)

    return render_template('admin/staging_detail.html',
                           admin=admin,
                           staging=staging,
                           customer=customer,
                           sync_history=sync_history)


@admin_bp.route('/staging/<int:staging_id>/delete', methods=['POST'])
@admin_required
def staging_delete(staging_id):
    """Delete a staging environment (admin)"""
    admin = get_current_admin()

    staging = StagingEnvironment.get_by_id(staging_id)
    if not staging:
        flash('Staging environment not found.', 'error')
        return redirect(url_for('admin.staging_list'))

    try:
        from redis import Redis
        from rq import Queue

        redis_host = os.getenv('REDIS_HOST', 'localhost')
        redis_conn = Redis(host=redis_host, port=6379)
        queue = Queue('staging', connection=redis_conn)

        sys.path.insert(0, '/opt/shophosting/provisioning')
        from staging_worker import delete_staging_job
        job = queue.enqueue(delete_staging_job, staging_id,
                           job_timeout=300, result_ttl=3600)

        log_admin_action(admin.id, 'delete_staging', 'staging_environment', staging_id,
                        f'Deleted staging {staging.staging_domain}', request.remote_addr)

        flash(f'Staging environment {staging.staging_domain} is being deleted.', 'success')
        logger.info(f"Admin {admin.id} deleted staging {staging_id}")

    except Exception as e:
        logger.error(f"Failed to delete staging: {e}")
        flash('Failed to delete staging environment.', 'error')

    return redirect(url_for('admin.staging_list'))


@admin_bp.route('/customer/<int:customer_id>/staging/create', methods=['POST'])
@admin_required
def staging_create_for_customer(customer_id):
    """Create staging environment for a customer (admin)"""
    admin = get_current_admin()

    customer = Customer.get_by_id(customer_id)
    if not customer:
        flash('Customer not found.', 'error')
        return redirect(url_for('admin.customers'))

    if customer.status != 'active':
        flash('Customer site must be active to create staging.', 'error')
        return redirect(url_for('admin.customer_detail', customer_id=customer_id))

    if not StagingEnvironment.can_create_staging(customer_id):
        flash(f'Customer already has maximum staging environments.', 'error')
        return redirect(url_for('admin.customer_detail', customer_id=customer_id))

    staging_name = request.form.get('staging_name', '').strip() or None

    try:
        from redis import Redis
        from rq import Queue

        redis_host = os.getenv('REDIS_HOST', 'localhost')
        redis_conn = Redis(host=redis_host, port=6379)
        queue = Queue('staging', connection=redis_conn)

        sys.path.insert(0, '/opt/shophosting/provisioning')
        from staging_worker import create_staging_job
        job = queue.enqueue(create_staging_job, customer_id, staging_name,
                           job_timeout=600, result_ttl=3600)

        log_admin_action(admin.id, 'create_staging', 'customer', customer_id,
                        f'Created staging for customer {customer.email}', request.remote_addr)

        flash('Staging environment is being created.', 'success')
        logger.info(f"Admin {admin.id} created staging for customer {customer_id}")

    except Exception as e:
        logger.error(f"Failed to create staging: {e}")
        flash('Failed to create staging environment.', 'error')

    return redirect(url_for('admin.customer_detail', customer_id=customer_id))


# =============================================================================
# Billing Overview
# =============================================================================

@admin_bp.route('/billing')
@admin_required
def billing():
    """Billing overview page"""
    admin = get_current_admin()

    billing_stats = get_billing_stats()
    recent_invoices = get_recent_invoices(20)
    subscription_breakdown = get_subscription_breakdown()

    return render_template('admin/billing.html',
                           admin=admin,
                           billing_stats=billing_stats,
                           recent_invoices=recent_invoices,
                           subscription_breakdown=subscription_breakdown)


# =============================================================================
# Support Tickets
# =============================================================================

@admin_bp.route('/tickets')
@admin_required
def tickets():
    """List all support tickets"""
    admin = get_current_admin()

    # Get filter parameters
    status = request.args.get('status', '')
    priority = request.args.get('priority', '')
    category_id = request.args.get('category', '')
    assigned = request.args.get('assigned', '')
    search = request.args.get('search', '').strip()
    page = int(request.args.get('page', 1))
    per_page = 20

    # Get tickets
    tickets_list, total = Ticket.get_all_filtered(
        status=status or None,
        priority=priority or None,
        category_id=int(category_id) if category_id else None,
        assigned_admin_id=assigned or None,
        search=search or None,
        page=page,
        per_page=per_page
    )

    total_pages = (total + per_page - 1) // per_page

    # Get categories and admins for filter dropdowns
    categories = TicketCategory.get_all_active()
    admins = AdminUser.get_all()
    ticket_stats = Ticket.get_stats()

    return render_template('admin/tickets.html',
                           admin=admin,
                           tickets=tickets_list,
                           categories=categories,
                           admins=admins,
                           stats=ticket_stats,
                           status_filter=status,
                           priority_filter=priority,
                           category_filter=category_id,
                           assigned_filter=assigned,
                           search=search,
                           page=page,
                           total_pages=total_pages,
                           total=total)


@admin_bp.route('/tickets/<int:ticket_id>')
@admin_required
def ticket_detail(ticket_id):
    """View ticket detail with customer context"""
    admin = get_current_admin()
    ticket = Ticket.get_by_id(ticket_id)

    if not ticket:
        flash('Ticket not found.', 'error')
        return redirect(url_for('admin.tickets'))

    # Get related data
    customer = Customer.get_by_id(ticket.customer_id)
    category = TicketCategory.get_by_id(ticket.category_id) if ticket.category_id else None
    messages = ticket.get_messages(include_internal=True)  # Include internal notes for admin
    attachments = ticket.get_attachments()
    admins = AdminUser.get_all()

    # Get customer's other tickets count
    customer_tickets, customer_ticket_count = Ticket.get_by_customer(customer.id, page=1, per_page=1)

    return render_template('admin/ticket_detail.html',
                           admin=admin,
                           ticket=ticket,
                           customer=customer,
                           category=category,
                           messages=messages,
                           attachments=attachments,
                           admins=admins,
                           categories=TicketCategory.get_all_active(),
                           customer_ticket_count=customer_ticket_count)


@admin_bp.route('/tickets/<int:ticket_id>/respond', methods=['POST'])
@admin_required
def ticket_respond(ticket_id):
    """Admin respond to ticket"""
    admin = get_current_admin()
    ticket = Ticket.get_by_id(ticket_id)

    if not ticket:
        flash('Ticket not found.', 'error')
        return redirect(url_for('admin.tickets'))

    message_text = request.form.get('message', '').strip()
    if not message_text:
        flash('Message cannot be empty.', 'error')
        return redirect(url_for('admin.ticket_detail', ticket_id=ticket_id))

    try:
        # Create message
        message = TicketMessage(
            ticket_id=ticket.id,
            admin_user_id=admin.id,
            message=message_text,
            is_internal_note=False
        )
        message.save()

        # Update ticket status to in_progress if it was open
        if ticket.status == 'open':
            ticket.status = 'in_progress'
            ticket.save()

        log_admin_action(admin.id, 'ticket_respond', 'ticket', ticket_id,
                        f'Responded to ticket {ticket.ticket_number}', request.remote_addr)
        flash('Response sent successfully.', 'success')
    except Exception as e:
        flash(f'Error sending response: {str(e)}', 'error')

    return redirect(url_for('admin.ticket_detail', ticket_id=ticket_id))


@admin_bp.route('/tickets/<int:ticket_id>/note', methods=['POST'])
@admin_required
def ticket_note(ticket_id):
    """Add internal note to ticket"""
    admin = get_current_admin()
    ticket = Ticket.get_by_id(ticket_id)

    if not ticket:
        flash('Ticket not found.', 'error')
        return redirect(url_for('admin.tickets'))

    note_text = request.form.get('note', '').strip()
    if not note_text:
        flash('Note cannot be empty.', 'error')
        return redirect(url_for('admin.ticket_detail', ticket_id=ticket_id))

    try:
        # Create internal note
        message = TicketMessage(
            ticket_id=ticket.id,
            admin_user_id=admin.id,
            message=note_text,
            is_internal_note=True
        )
        message.save()

        log_admin_action(admin.id, 'ticket_note', 'ticket', ticket_id,
                        f'Added internal note to ticket {ticket.ticket_number}', request.remote_addr)
        flash('Internal note added.', 'success')
    except Exception as e:
        flash(f'Error adding note: {str(e)}', 'error')

    return redirect(url_for('admin.ticket_detail', ticket_id=ticket_id))


@admin_bp.route('/tickets/<int:ticket_id>/status', methods=['POST'])
@admin_required
def ticket_status(ticket_id):
    """Change ticket status"""
    admin = get_current_admin()
    ticket = Ticket.get_by_id(ticket_id)

    if not ticket:
        flash('Ticket not found.', 'error')
        return redirect(url_for('admin.tickets'))

    new_status = request.form.get('status', '')
    if new_status not in Ticket.STATUSES:
        flash('Invalid status.', 'error')
        return redirect(url_for('admin.ticket_detail', ticket_id=ticket_id))

    try:
        old_status = ticket.status
        ticket.status = new_status

        # Set resolved/closed timestamps
        if new_status == 'resolved' and not ticket.resolved_at:
            ticket.resolved_at = datetime.now()
        elif new_status == 'closed' and not ticket.closed_at:
            ticket.closed_at = datetime.now()

        ticket.save()

        log_admin_action(admin.id, 'ticket_status', 'ticket', ticket_id,
                        f'Changed status from {old_status} to {new_status}', request.remote_addr)
        flash(f'Ticket status updated to {new_status.replace("_", " ")}.', 'success')
    except Exception as e:
        flash(f'Error updating status: {str(e)}', 'error')

    return redirect(url_for('admin.ticket_detail', ticket_id=ticket_id))


@admin_bp.route('/tickets/<int:ticket_id>/assign', methods=['POST'])
@admin_required
def ticket_assign(ticket_id):
    """Assign ticket to admin"""
    admin = get_current_admin()
    ticket = Ticket.get_by_id(ticket_id)

    if not ticket:
        flash('Ticket not found.', 'error')
        return redirect(url_for('admin.tickets'))

    assigned_id = request.form.get('assigned_admin_id', '')

    try:
        if assigned_id:
            assigned_admin = AdminUser.get_by_id(int(assigned_id))
            ticket.assigned_admin_id = int(assigned_id)
            assigned_name = assigned_admin.full_name if assigned_admin else 'Unknown'
        else:
            ticket.assigned_admin_id = None
            assigned_name = 'Unassigned'

        ticket.save()

        log_admin_action(admin.id, 'ticket_assign', 'ticket', ticket_id,
                        f'Assigned ticket to {assigned_name}', request.remote_addr)
        flash(f'Ticket assigned to {assigned_name}.', 'success')
    except Exception as e:
        flash(f'Error assigning ticket: {str(e)}', 'error')

    return redirect(url_for('admin.ticket_detail', ticket_id=ticket_id))


@admin_bp.route('/tickets/<int:ticket_id>/priority', methods=['POST'])
@admin_required
def ticket_priority(ticket_id):
    """Change ticket priority"""
    admin = get_current_admin()
    ticket = Ticket.get_by_id(ticket_id)

    if not ticket:
        flash('Ticket not found.', 'error')
        return redirect(url_for('admin.tickets'))

    new_priority = request.form.get('priority', '')
    if new_priority not in Ticket.PRIORITIES:
        flash('Invalid priority.', 'error')
        return redirect(url_for('admin.ticket_detail', ticket_id=ticket_id))

    try:
        old_priority = ticket.priority
        ticket.priority = new_priority
        ticket.save()

        log_admin_action(admin.id, 'ticket_priority', 'ticket', ticket_id,
                        f'Changed priority from {old_priority} to {new_priority}', request.remote_addr)
        flash(f'Ticket priority updated to {new_priority}.', 'success')
    except Exception as e:
        flash(f'Error updating priority: {str(e)}', 'error')

    return redirect(url_for('admin.ticket_detail', ticket_id=ticket_id))


@admin_bp.route('/tickets/<int:ticket_id>/category', methods=['POST'])
@admin_required
def ticket_category(ticket_id):
    """Change ticket category"""
    admin = get_current_admin()
    ticket = Ticket.get_by_id(ticket_id)

    if not ticket:
        flash('Ticket not found.', 'error')
        return redirect(url_for('admin.tickets'))

    new_category_id = request.form.get('category_id', '')

    try:
        if new_category_id:
            category = TicketCategory.get_by_id(int(new_category_id))
            ticket.category_id = int(new_category_id)
            category_name = category.name if category else 'Unknown'
        else:
            ticket.category_id = None
            category_name = 'None'

        ticket.save()

        log_admin_action(admin.id, 'ticket_category', 'ticket', ticket_id,
                        f'Changed category to {category_name}', request.remote_addr)
        flash(f'Ticket category updated to {category_name}.', 'success')
    except Exception as e:
        flash(f'Error updating category: {str(e)}', 'error')

    return redirect(url_for('admin.ticket_detail', ticket_id=ticket_id))


@admin_bp.route('/tickets/attachment/<int:attachment_id>')
@admin_required
def serve_ticket_attachment(attachment_id):
    """Serve attachment file for admin"""
    from flask import send_file, abort
    import os

    attachment = TicketAttachment.get_by_id(attachment_id)
    if not attachment:
        abort(404)

    TICKET_UPLOAD_PATH = '/var/customers/tickets'
    full_path = os.path.join(TICKET_UPLOAD_PATH, attachment.file_path)

    if not os.path.exists(full_path):
        abort(404)

    return send_file(
        full_path,
        download_name=attachment.original_filename,
        as_attachment=True
    )


# =============================================================================
# Consultation Appointments
# =============================================================================

@admin_bp.route('/appointments')
@admin_required
def appointments():
    """List all consultation appointments"""
    admin = get_current_admin()

    # Get filter parameters
    status = request.args.get('status', '')
    search = request.args.get('search', '').strip()
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    page = int(request.args.get('page', 1))
    per_page = 20

    # Get filtered appointments
    appointments_list, total = ConsultationAppointment.get_all_filtered(
        status=status or None,
        search=search or None,
        date_from=date_from or None,
        date_to=date_to or None,
        page=page,
        per_page=per_page
    )

    total_pages = (total + per_page - 1) // per_page

    # Get stats
    stats = ConsultationAppointment.get_stats()

    # Get all admins for assignment dropdown
    admins = AdminUser.get_all()

    return render_template('admin/appointments.html',
                           admin=admin,
                           appointments=appointments_list,
                           stats=stats,
                           admins=admins,
                           status_filter=status,
                           search=search,
                           date_from=date_from,
                           date_to=date_to,
                           page=page,
                           total_pages=total_pages,
                           total=total)


@admin_bp.route('/appointments/<int:appointment_id>')
@admin_required
def appointment_detail(appointment_id):
    """View appointment detail"""
    admin = get_current_admin()
    appointment = ConsultationAppointment.get_by_id(appointment_id)

    if not appointment:
        flash('Appointment not found.', 'error')
        return redirect(url_for('admin.appointments'))

    # Get all admins for assignment dropdown
    admins = AdminUser.get_all()

    return render_template('admin/appointment_detail.html',
                           admin=admin,
                           appointment=appointment,
                           admins=admins)


@admin_bp.route('/appointments/<int:appointment_id>/status', methods=['POST'])
@admin_required
def appointment_status(appointment_id):
    """Update appointment status"""
    admin = get_current_admin()
    appointment = ConsultationAppointment.get_by_id(appointment_id)

    if not appointment:
        flash('Appointment not found.', 'error')
        return redirect(url_for('admin.appointments'))

    new_status = request.form.get('status', '')
    if new_status in ['pending', 'confirmed', 'completed', 'cancelled', 'no_show']:
        old_status = appointment.status
        appointment.status = new_status
        appointment.save()

        log_admin_action(admin.id, 'appointment_status_change', 'appointment', appointment_id,
                         f'Changed status from {old_status} to {new_status}', request.remote_addr)
        flash(f'Status updated to {new_status}.', 'success')
    else:
        flash('Invalid status.', 'error')

    return redirect(url_for('admin.appointment_detail', appointment_id=appointment_id))


@admin_bp.route('/appointments/<int:appointment_id>/assign', methods=['POST'])
@admin_required
def appointment_assign(appointment_id):
    """Assign appointment to an admin"""
    admin = get_current_admin()
    appointment = ConsultationAppointment.get_by_id(appointment_id)

    if not appointment:
        flash('Appointment not found.', 'error')
        return redirect(url_for('admin.appointments'))

    admin_id = request.form.get('admin_id', '')
    appointment.assigned_admin_id = int(admin_id) if admin_id else None
    appointment.save()

    if admin_id:
        assigned_admin = AdminUser.get_by_id(int(admin_id))
        log_admin_action(admin.id, 'appointment_assign', 'appointment', appointment_id,
                         f'Assigned to {assigned_admin.full_name if assigned_admin else "Unknown"}', request.remote_addr)
        flash('Appointment assigned.', 'success')
    else:
        log_admin_action(admin.id, 'appointment_unassign', 'appointment', appointment_id,
                         'Removed assignment', request.remote_addr)
        flash('Assignment removed.', 'success')

    return redirect(url_for('admin.appointment_detail', appointment_id=appointment_id))


@admin_bp.route('/appointments/<int:appointment_id>/notes', methods=['POST'])
@admin_required
def appointment_notes(appointment_id):
    """Update appointment notes"""
    admin = get_current_admin()
    appointment = ConsultationAppointment.get_by_id(appointment_id)

    if not appointment:
        flash('Appointment not found.', 'error')
        return redirect(url_for('admin.appointments'))

    notes = request.form.get('notes', '').strip()
    appointment.notes = notes
    appointment.save()

    log_admin_action(admin.id, 'appointment_notes_update', 'appointment', appointment_id,
                     'Updated notes', request.remote_addr)
    flash('Notes saved.', 'success')

    return redirect(url_for('admin.appointment_detail', appointment_id=appointment_id))


# =============================================================================
# Helper Functions
# =============================================================================

def get_customer_stats():
    """Get customer statistics by status"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT status, COUNT(*) as count
            FROM customers
            GROUP BY status
        """)
        rows = cursor.fetchall()

        stats = {
            'total': 0,
            'active': 0,
            'provisioning': 0,
            'pending': 0,
            'failed': 0,
            'suspended': 0
        }

        for row in rows:
            stats[row['status']] = row['count']
            stats['total'] += row['count']

        return stats
    finally:
        cursor.close()
        conn.close()


def get_recent_customers(limit=10):
    """Get recently created customers"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT id, email, domain, platform, status, created_at
            FROM customers
            ORDER BY created_at DESC
            LIMIT %s
        """, (limit,))
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()


def get_failed_customers(limit=10):
    """Get customers with failed provisioning"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT id, email, domain, platform, error_message, updated_at
            FROM customers
            WHERE status = 'failed'
            ORDER BY updated_at DESC
            LIMIT %s
        """, (limit,))
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()


def get_customers_filtered(search='', status='', platform='', page=1, per_page=20):
    """Get filtered and paginated customer list"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        where_clauses = []
        params = []

        if search:
            where_clauses.append("(email LIKE %s OR domain LIKE %s OR company_name LIKE %s)")
            search_param = f'%{search}%'
            params.extend([search_param, search_param, search_param])

        if status:
            where_clauses.append("status = %s")
            params.append(status)

        if platform:
            where_clauses.append("platform = %s")
            params.append(platform)

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

        # Get total count
        cursor.execute(f"SELECT COUNT(*) as count FROM customers WHERE {where_sql}", params)
        total = cursor.fetchone()['count']

        # Get paginated results
        offset = (page - 1) * per_page
        cursor.execute(f"""
            SELECT id, email, company_name, domain, platform, status, web_port, created_at
            FROM customers
            WHERE {where_sql}
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
        """, params + [per_page, offset])

        customers = cursor.fetchall()
        return customers, total
    finally:
        cursor.close()
        conn.close()


def get_provisioning_jobs(customer_id):
    """Get provisioning jobs for a customer"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT job_id, status, started_at, finished_at, error_message, created_at
            FROM provisioning_jobs
            WHERE customer_id = %s
            ORDER BY created_at DESC
        """, (customer_id,))
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()


def get_provisioning_logs_by_job(job_id):
    """Get provisioning logs for a specific job"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT job_id, customer_id, log_level, message, step_name, created_at
            FROM provisioning_logs
            WHERE job_id = %s
            ORDER BY created_at ASC
        """, (job_id,))
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()


def get_provisioning_logs_by_customer(customer_id, limit=100):
    """Get provisioning logs for a customer (most recent first)"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT pl.job_id, pl.customer_id, pl.log_level, pl.message, pl.step_name, pl.created_at
            FROM provisioning_logs pl
            INNER JOIN (
                SELECT job_id, MAX(created_at) as max_date
                FROM provisioning_logs
                WHERE customer_id = %s
                GROUP BY job_id
            ) latest ON pl.job_id = latest.job_id
            ORDER BY pl.created_at DESC
            LIMIT %s
        """, (customer_id, limit))
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()


def get_all_provisioning_jobs(limit=50):
    """Get all recent provisioning jobs"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT pj.*, c.email, c.domain
            FROM provisioning_jobs pj
            LEFT JOIN customers c ON pj.customer_id = c.id
            ORDER BY pj.created_at DESC
            LIMIT %s
        """, (limit,))
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()


def get_customer_audit_logs(customer_id, limit=20):
    """Get audit logs for a customer"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT action, details, ip_address, created_at
            FROM audit_log
            WHERE customer_id = %s
            ORDER BY created_at DESC
            LIMIT %s
        """, (customer_id, limit))
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()


def get_queue_stats():
    """Get queue statistics from Redis (live) and database (historical)"""
    stats = {
        'queued': 0,
        'started': 0,
        'finished': 0,
        'failed': 0,
        'connected': False
    }

    # Get live queue stats from Redis - this is the source of truth for active jobs
    try:
        from redis import Redis
        from rq import Queue

        redis_host = os.getenv('REDIS_HOST', 'localhost')
        redis_conn = Redis(host=redis_host, port=6379)
        queue = Queue('provisioning', connection=redis_conn)

        stats['queued'] = len(queue)
        stats['started'] = len(queue.started_job_registry)
        stats['connected'] = True
    except Exception as e:
        stats['error'] = str(e)

    # Get historical counts from database - only count most recent job per customer
    # and only if the customer status matches the job status for consistency
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Count finished jobs (customers who are now active)
        cursor.execute("""
            SELECT COUNT(DISTINCT pj.customer_id) as count
            FROM provisioning_jobs pj
            INNER JOIN customers c ON pj.customer_id = c.id
            WHERE pj.status = 'finished' AND c.status = 'active'
        """)
        row = cursor.fetchone()
        if row:
            stats['finished'] = row['count']

        # Count failed jobs (only for customers still in failed status)
        cursor.execute("""
            SELECT COUNT(DISTINCT pj.customer_id) as count
            FROM provisioning_jobs pj
            INNER JOIN customers c ON pj.customer_id = c.id
            WHERE pj.status = 'failed' AND c.status = 'failed'
        """)
        row = cursor.fetchone()
        if row:
            stats['failed'] = row['count']

        cursor.close()
        conn.close()
    except Exception:
        pass

    return stats


def get_service_status():
    """Check status of system services"""
    services = {
        'shophosting-webapp': False,
        'provisioning-worker': False,
        'nginx': False,
        'mysql': False,
        'redis': False
    }

    for service in services.keys():
        try:
            result = subprocess.run(
                ['systemctl', 'is-active', service],
                capture_output=True, text=True, timeout=5
            )
            services[service] = result.stdout.strip() == 'active'
        except Exception:
            services[service] = False

    return services


def get_disk_usage():
    """Get disk usage for customer data directory"""
    try:
        result = subprocess.run(
            ['df', '-h', '/var/customers'],
            capture_output=True, text=True, timeout=10
        )
        lines = result.stdout.strip().split('\n')
        if len(lines) > 1:
            parts = lines[1].split()
            return {
                'total': parts[1],
                'used': parts[2],
                'available': parts[3],
                'percent': parts[4]
            }
    except Exception:
        pass

    return {'total': 'N/A', 'used': 'N/A', 'available': 'N/A', 'percent': 'N/A'}


def get_backup_status():
    """Get backup status from log file"""
    log_path = '/var/log/shophosting-backup.log'
    status = {
        'last_run': None,
        'last_status': 'unknown',
        'snapshots': 0
    }

    try:
        if os.path.exists(log_path):
            result = subprocess.run(
                ['tail', '-n', '50', log_path],
                capture_output=True, text=True, timeout=10
            )
            lines = result.stdout.strip().split('\n')

            for line in reversed(lines):
                if 'Backup completed successfully' in line:
                    status['last_status'] = 'success'
                    # Try to extract date
                    if line[:10].count('-') == 2:
                        status['last_run'] = line[:19]
                    break
                elif 'ERROR' in line or 'Failed' in line:
                    status['last_status'] = 'failed'
                    break
    except Exception:
        pass

    return status


def get_billing_stats():
    """Get billing statistics"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        # Monthly Recurring Revenue
        cursor.execute("""
            SELECT COALESCE(SUM(pp.price_monthly), 0) as mrr
            FROM subscriptions s
            JOIN pricing_plans pp ON s.plan_id = pp.id
            WHERE s.status = 'active'
        """)
        mrr = cursor.fetchone()['mrr'] or 0

        # Active subscriptions count
        cursor.execute("SELECT COUNT(*) as count FROM subscriptions WHERE status = 'active'")
        active_subs = cursor.fetchone()['count']

        # This month's revenue
        cursor.execute("""
            SELECT COALESCE(SUM(amount_paid), 0) as revenue
            FROM invoices
            WHERE status = 'paid'
            AND MONTH(paid_at) = MONTH(CURRENT_DATE())
            AND YEAR(paid_at) = YEAR(CURRENT_DATE())
        """)
        month_revenue = cursor.fetchone()['revenue'] or 0

        return {
            'mrr': float(mrr),
            'active_subscriptions': active_subs,
            'month_revenue': float(month_revenue)
        }
    finally:
        cursor.close()
        conn.close()


def get_recent_invoices(limit=20):
    """Get recent invoices"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT i.*, c.email, c.company_name
            FROM invoices i
            LEFT JOIN customers c ON i.customer_id = c.id
            ORDER BY i.created_at DESC
            LIMIT %s
        """, (limit,))
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()


def get_subscription_breakdown():
    """Get subscription counts by plan"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT pp.name, pp.platform, COUNT(s.id) as count
            FROM pricing_plans pp
            LEFT JOIN subscriptions s ON s.plan_id = pp.id AND s.status = 'active'
            GROUP BY pp.id
            ORDER BY pp.platform, pp.price_monthly
        """)
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()


# =============================================================================
# Quick Action Routes
# =============================================================================

@admin_bp.route('/restart/<service>', methods=['POST'])
@admin_required
def restart_service(service):
    """Restart a system service"""
    admin = get_current_admin()

    allowed_services = ['shophosting-webapp', 'provisioning-worker', 'nginx', 'redis', 'mysql']
    if service not in allowed_services:
        flash(f'Service {service} is not allowed.', 'error')
        return redirect(url_for('admin.dashboard'))

    try:
        result = subprocess.run(
            ['sudo', 'systemctl', 'restart', service],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            log_admin_action(admin.id, 'restart_service', 'service', None,
                           f'Restarted {service}', request.remote_addr)
            flash(f'Service {service} restarted successfully.', 'success')
        else:
            flash(f'Failed to restart {service}: {result.stderr}', 'error')
    except Exception as e:
        flash(f'Error restarting service: {str(e)}', 'error')

    return redirect(url_for('admin.dashboard'))


@admin_bp.route('/backup/run', methods=['POST'])
@admin_required
def run_backup():
    """Run backup manually"""
    admin = get_current_admin()

    try:
        # Run backup in background
        subprocess.Popen(
            ['sudo', '/opt/shophosting/scripts/backup.sh'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        log_admin_action(admin.id, 'run_backup', ip_address=request.remote_addr)
        flash('Backup started in background. Check logs for progress.', 'success')
    except Exception as e:
        flash(f'Error starting backup: {str(e)}', 'error')

    return redirect(url_for('admin.dashboard'))


@admin_bp.route('/jobs/clear-failed', methods=['POST'])
@admin_required
def clear_failed_jobs():
    """Clear all failed jobs from the queue"""
    admin = get_current_admin()

    try:
        from redis import Redis
        from rq import Queue

        redis_host = os.getenv('REDIS_HOST', 'localhost')
        redis_conn = Redis(host=redis_host, port=6379)
        queue = Queue('provisioning', connection=redis_conn)

        # Clear failed jobs
        failed_count = len(queue.failed_job_registry)
        queue.failed_job_registry.cleanup()

        log_admin_action(admin.id, 'clear_failed_jobs', 'queue', None,
                        f'Cleared {failed_count} failed jobs', request.remote_addr)
        flash(f'Cleared {failed_count} failed jobs.', 'success')
    except Exception as e:
        flash(f'Error clearing failed jobs: {str(e)}', 'error')

    return redirect(url_for('admin.dashboard'))


@admin_bp.route('/logs/<log_type>')
@admin_required
def view_logs(log_type):
    """View log files"""
    admin = get_current_admin()

    log_files = {
        'webapp': '/opt/shophosting/logs/webapp.log',
        'worker': '/opt/shophosting/logs/provisioning_worker.log',
        'backup': '/var/log/shophosting-backup.log'
    }

    if log_type not in log_files:
        flash('Invalid log type.', 'error')
        return redirect(url_for('admin.dashboard'))

    log_path = log_files[log_type]
    lines = []

    try:
        result = subprocess.run(
            ['tail', '-n', '200', log_path],
            capture_output=True, text=True, timeout=10
        )
        lines = result.stdout.split('\n')
    except Exception as e:
        flash(f'Error reading logs: {str(e)}', 'error')

    return render_template('admin/logs.html',
                          admin=admin,
                          log_type=log_type,
                          lines=lines)


# =============================================================================
# Customer Management Routes
# =============================================================================

@admin_bp.route('/manage-customers')
@admin_required
def manage_customers():
    """Customer management page"""
    admin = get_current_admin()

    search = request.args.get('search', '').strip()
    page = int(request.args.get('page', 1))
    per_page = 20

    customers_list, total = get_customers_filtered(search=search, page=page, per_page=per_page)
    total_pages = (total + per_page - 1) // per_page

    return render_template('admin/manage_customers.html',
                          admin=admin,
                          customers=customers_list,
                          search=search,
                          page=page,
                          total_pages=total_pages,
                          total=total)


@admin_bp.route('/manage-customers/create', methods=['GET', 'POST'])
@admin_required
def create_customer():
    """Create a new customer and queue provisioning"""
    admin = get_current_admin()

    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        company_name = request.form.get('company_name', '').strip()
        domain = request.form.get('domain', '').strip().lower()
        platform = request.form.get('platform', 'woocommerce')
        start_provisioning = request.form.get('start_provisioning') == 'on'

        # Validation
        if not all([email, password, company_name, domain]):
            flash('All fields are required.', 'error')
            return render_template('admin/customer_form.html', admin=admin, customer=None)

        # Check for duplicates
        if Customer.email_exists(email):
            flash('Email already exists.', 'error')
            return render_template('admin/customer_form.html', admin=admin, customer=None)

        if Customer.domain_exists(domain):
            flash('Domain already exists.', 'error')
            return render_template('admin/customer_form.html', admin=admin, customer=None)

        try:
            # Assign a port
            web_port = PortManager.get_next_available_port()
            if not web_port:
                flash('No available ports. Maximum capacity reached.', 'error')
                return render_template('admin/customer_form.html', admin=admin, customer=None)

            # Create customer with port
            conn = get_db_connection()
            cursor = conn.cursor()

            from werkzeug.security import generate_password_hash
            password_hash = generate_password_hash(password)

            cursor.execute("""
                INSERT INTO customers (email, password_hash, company_name, domain, platform, status, web_port)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (email, password_hash, company_name, domain, platform,
                  'provisioning' if start_provisioning else 'pending', web_port))

            customer_id = cursor.lastrowid
            conn.commit()
            cursor.close()
            conn.close()

            # Queue provisioning if requested
            if start_provisioning:
                try:
                    import sys
                    sys.path.insert(0, '/opt/shophosting/provisioning')
                    from enqueue_provisioning import ProvisioningQueue

                    queue = ProvisioningQueue(
                        redis_host=os.getenv('REDIS_HOST', 'localhost'),
                        redis_port=int(os.getenv('REDIS_PORT', 6379))
                    )

                    customer_data = {
                        'customer_id': customer_id,
                        'domain': domain,
                        'platform': platform,
                        'email': email,
                        'web_port': web_port,
                        'site_title': company_name,
                        'memory_limit': os.getenv('DEFAULT_MEMORY_LIMIT', '1g'),
                        'cpu_limit': os.getenv('DEFAULT_CPU_LIMIT', '1.0')
                    }

                    job = queue.enqueue_customer(customer_data)

                    # Record the job
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    cursor.execute("""
                        INSERT INTO provisioning_jobs (customer_id, job_id, status)
                        VALUES (%s, %s, 'queued')
                    """, (customer_id, job.id))
                    conn.commit()
                    cursor.close()
                    conn.close()

                    flash(f'Customer {email} created and provisioning started.', 'success')
                except Exception as e:
                    flash(f'Customer created but provisioning failed to start: {str(e)}', 'warning')
            else:
                flash(f'Customer {email} created successfully (not provisioned).', 'success')

            log_admin_action(admin.id, 'create_customer', 'customer', customer_id,
                           f'Created customer {email}', request.remote_addr)
            return redirect(url_for('admin.manage_customers'))
        except Exception as e:
            flash(f'Error creating customer: {str(e)}', 'error')

    return render_template('admin/customer_form.html', admin=admin, customer=None)


@admin_bp.route('/manage-customers/<int:customer_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_customer(customer_id):
    """Edit a customer"""
    admin = get_current_admin()
    customer = Customer.get_by_id(customer_id)

    if not customer:
        flash('Customer not found.', 'error')
        return redirect(url_for('admin.manage_customers'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        company_name = request.form.get('company_name', '').strip()
        domain = request.form.get('domain', '').strip().lower()
        platform = request.form.get('platform', 'woocommerce')
        status = request.form.get('status', customer.status)
        new_password = request.form.get('password', '').strip()

        # Validation
        if not all([email, company_name, domain]):
            flash('Email, company name, and domain are required.', 'error')
            return render_template('admin/customer_form.html', admin=admin, customer=customer)

        # Check for duplicates (excluding current customer)
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT id FROM customers WHERE email = %s AND id != %s", (email, customer_id))
            if cursor.fetchone():
                flash('Email already exists.', 'error')
                return render_template('admin/customer_form.html', admin=admin, customer=customer)

            cursor.execute("SELECT id FROM customers WHERE domain = %s AND id != %s", (domain, customer_id))
            if cursor.fetchone():
                flash('Domain already exists.', 'error')
                return render_template('admin/customer_form.html', admin=admin, customer=customer)

            # Update customer
            if new_password:
                from werkzeug.security import generate_password_hash
                password_hash = generate_password_hash(new_password)
                cursor.execute("""
                    UPDATE customers SET email=%s, company_name=%s, domain=%s,
                    platform=%s, status=%s, password_hash=%s WHERE id=%s
                """, (email, company_name, domain, platform, status, password_hash, customer_id))
            else:
                cursor.execute("""
                    UPDATE customers SET email=%s, company_name=%s, domain=%s,
                    platform=%s, status=%s WHERE id=%s
                """, (email, company_name, domain, platform, status, customer_id))

            conn.commit()
            log_admin_action(admin.id, 'edit_customer', 'customer', customer_id,
                           f'Updated customer {email}', request.remote_addr)
            flash(f'Customer {email} updated successfully.', 'success')
            return redirect(url_for('admin.manage_customers'))
        finally:
            cursor.close()
            conn.close()

    return render_template('admin/customer_form.html', admin=admin, customer=customer)


@admin_bp.route('/manage-customers/<int:customer_id>/delete', methods=['POST'])
@admin_required
def delete_customer(customer_id):
    """Delete a customer and their containers/configs"""
    admin = get_current_admin()
    customer = Customer.get_by_id(customer_id)

    if not customer:
        flash('Customer not found.', 'error')
        return redirect(url_for('admin.manage_customers'))

    email = customer.email
    customer_path = f"/var/customers/customer-{customer_id}"
    db_deleted = False
    directory_deleted = False

    try:
        # Stop and remove containers with volume removal
        if os.path.exists(customer_path):
            result = subprocess.run(
                ['docker', 'compose', 'down', '-v', '--remove-orphans'],
                cwd=customer_path,
                capture_output=True,
                timeout=120
            )
            if result.returncode != 0:
                logger.warning(f"Docker compose down failed: {result.stderr.decode()}")
            
            # Short delay to ensure volumes are unmounted
            import time
            time.sleep(2)
            
            # Change ownership of all files to current user before deletion
            # This fixes permission issues with Docker-created root files
            result = subprocess.run(
                ['sudo', 'chmod', '-R', '777', customer_path],
                capture_output=True,
                timeout=30
            )
            if result.returncode != 0:
                logger.warning(f"chmod failed: {result.stderr.decode()}")
            
            # Also fix individual problem files like license.txt
            license_file = os.path.join(customer_path, 'license.txt')
            if os.path.exists(license_file):
                subprocess.run(['sudo', 'chmod', '777', license_file], capture_output=True)
            
            # Now remove the directory
            result = subprocess.run(
                ['sudo', 'rm', '-rf', customer_path],
                capture_output=True,
                timeout=60
            )
            if result.returncode != 0:
                raise PermissionError(f"Failed to delete customer directory: {result.stderr.decode()}")
            directory_deleted = True

        # Remove Nginx config
        nginx_available = f"/etc/nginx/sites-available/customer-{customer_id}.conf"
        nginx_enabled = f"/etc/nginx/sites-enabled/customer-{customer_id}.conf"

        if os.path.exists(nginx_enabled):
            result = subprocess.run(['sudo', 'rm', '-f', nginx_enabled], capture_output=True)
            if result.returncode != 0:
                logger.warning(f"Failed to remove nginx enabled config: {result.stderr.decode()}")
        if os.path.exists(nginx_available):
            result = subprocess.run(['sudo', 'rm', '-f', nginx_available], capture_output=True)
            if result.returncode != 0:
                logger.warning(f"Failed to remove nginx available config: {result.stderr.decode()}")

        # Reload nginx
        result = subprocess.run(['sudo', 'systemctl', 'reload', 'nginx'], capture_output=True)
        if result.returncode != 0:
            logger.warning(f"Nginx reload failed: {result.stderr.decode()}")

        # Always try to delete from database
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM provisioning_jobs WHERE customer_id = %s", (customer_id,))
            cursor.execute("DELETE FROM customers WHERE id = %s", (customer_id,))
            conn.commit()
            cursor.close()
            conn.close()
            db_deleted = True
        except Exception as db_error:
            logger.error(f"Database deletion failed: {db_error}")
            raise

        log_admin_action(admin.id, 'delete_customer', 'customer', customer_id,
                        f'Deleted customer {email} and removed containers', request.remote_addr)
        flash(f'Customer {email} and all associated resources deleted successfully.', 'success')
    except Exception as e:
        if directory_deleted and db_deleted:
            log_admin_action(admin.id, 'delete_customer_partial', 'customer', customer_id,
                            f'Deleted customer {email} but cleanup had issues: {str(e)}', request.remote_addr)
            flash(f'Customer {email} was deleted but some cleanup steps failed: {str(e)}. Check logs for details.', 'warning')
        elif directory_deleted:
            log_admin_action(admin.id, 'delete_customer_directory_only', 'customer', customer_id,
                            f'Deleted customer directory but database record remains: {str(e)}', request.remote_addr)
            flash(f'Customer directory deleted but database record could not be removed: {str(e)}. Contact administrator.', 'error')
        else:
            flash(f'Error deleting customer: {str(e)}', 'error')

    return redirect(url_for('admin.manage_customers'))


def super_admin_required(f):
    """Require super admin role for access"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('admin_user_role') != 'super_admin':
            flash('This action requires super admin privileges.', 'error')
            return redirect(url_for('admin.dashboard'))
        return f(*args, **kwargs)
    return decorated


class AdminUserForm(FlaskForm):
    """Form for creating/editing admin users"""
    full_name = StringField('Full Name', validators=[DataRequired(), Length(max=255)])
    email = StringField('Email', validators=[DataRequired(), Email(), Length(max=255)])
    password = PasswordField('Password', validators=[Length(min=8)])
    role = SelectField('Role', choices=[('admin', 'Admin'), ('support', 'Support'), ('super_admin', 'Super Admin')], default='admin')
    is_active = BooleanField('Active', default=True)
    submit = SubmitField('Save Admin User')

    def __init__(self, admin_id=None, is_self=False, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.admin_id = admin_id
        self.is_self = is_self
        if is_self:
            self.role.render_kw = {'disabled': True}

    def validate_email(self, email):
        admin = AdminUser.get_by_email(email.data)
        if admin and admin.id != self.admin_id:
            raise ValidationError('Email already registered to another admin user.')


class ChangePasswordForm(FlaskForm):
    """Form for changing password"""
    current_password = PasswordField('Current Password', validators=[DataRequired()])
    new_password = PasswordField('New Password', validators=[
        DataRequired(),
        Length(min=8, message='Password must be at least 8 characters'),
    ])
    confirm_password = PasswordField('Confirm Password', validators=[
        DataRequired(),
        EqualTo('new_password', message='Passwords must match')
    ])
    submit = SubmitField('Change Password')

    def __init__(self, admin_id, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.admin_id = admin_id


@admin_bp.route('/admins')
@admin_required
def admins():
    """List all admin users"""
    admin = get_current_admin()
    admins = AdminUser.get_all()

    return render_template('admin/admins.html',
                           admin=admin,
                           admins=admins)


@admin_bp.route('/admins/new', methods=['GET', 'POST'])
@super_admin_required
def new_admin():
    """Create new admin user"""
    admin = get_current_admin()
    form = AdminUserForm()

    if form.validate_on_submit():
        existing = AdminUser.get_by_email(form.email.data)
        if existing:
            flash('An admin with this email already exists.', 'error')
        else:
            new_admin_user = AdminUser(
                email=form.email.data,
                full_name=form.full_name.data,
                role=form.role.data,
                is_active=form.is_active.data
            )
            if form.password.data:
                new_admin_user.set_password(form.password.data)
            else:
                temp_password = ''.join(secrets.choice(string.ascii_letters + string.digits + '!@#$%^&*') for _ in range(16))
                new_admin_user.set_password(temp_password)
                flash(f'Password not provided. A temporary password has been generated and will need to be reset.', 'warning')

            new_admin_user.save()

            log_admin_action(admin.id, 'create_admin', 'admin_user', new_admin_user.id,
                           f'Created admin user {new_admin_user.email} with role {new_admin_user.role}', request.remote_addr)
            flash(f'Admin user {new_admin_user.email} created successfully.', 'success')
            return redirect(url_for('admin.admins'))

    return render_template('admin/admin_form.html',
                           admin=admin,
                           form=form,
                           is_new=True)


@admin_bp.route('/admins/<int:admin_id>/edit', methods=['GET', 'POST'])
@super_admin_required
def edit_admin(admin_id):
    """Edit existing admin user"""
    admin = get_current_admin()
    admin_user = AdminUser.get_by_id(admin_id)

    if not admin_user:
        flash('Admin user not found.', 'error')
        return redirect(url_for('admin.admins'))

    is_self = (admin_user.id == admin.id)
    form = AdminUserForm(admin_id=admin_id, is_self=is_self, obj=admin_user)

    if form.validate_on_submit():
        admin_user.email = form.email.data
        admin_user.full_name = form.full_name.data

        if is_self and admin_user.role == 'super_admin':
            admin_user.role = 'super_admin'
            admin_user.is_active = True
        else:
            admin_user.role = form.role.data
            admin_user.is_active = form.is_active.data

        if form.password.data:
            admin_user.set_password(form.password.data)

        admin_user.save()

        log_admin_action(admin.id, 'update_admin', 'admin_user', admin_id,
                       f'Updated admin user {admin_user.email}', request.remote_addr)
        flash(f'Admin user {admin_user.email} updated successfully.', 'success')
        return redirect(url_for('admin.admins'))

    return render_template('admin/admin_form.html',
                           admin=admin,
                           form=form,
                           admin_user=admin_user,
                           is_new=False,
                           is_self=is_self)


@admin_bp.route('/admins/<int:admin_id>/delete', methods=['POST'])
@super_admin_required
def delete_admin(admin_id):
    """Delete admin user"""
    admin = get_current_admin()
    admin_user = AdminUser.get_by_id(admin_id)

    if not admin_user:
        flash('Admin user not found.', 'error')
        return redirect(url_for('admin.admins'))

    if admin_user.id == admin.id:
        flash('You cannot delete your own account.', 'error')
        return redirect(url_for('admin.admins'))

    try:
        email = admin_user.email
        admin_user.delete()

        log_admin_action(admin.id, 'delete_admin', 'admin_user', admin_id,
                       f'Deleted admin user {email}', request.remote_addr)
        flash(f'Admin user {email} deleted successfully.', 'success')
    except Exception as e:
        flash(f'Error deleting admin user: {str(e)}', 'error')

    return redirect(url_for('admin.admins'))


@admin_bp.route('/admins/<int:admin_id>/toggle-active', methods=['POST'])
@super_admin_required
def toggle_admin_active(admin_id):
    """Toggle admin user active status"""
    admin = get_current_admin()
    admin_user = AdminUser.get_by_id(admin_id)

    if not admin_user:
        flash('Admin user not found.', 'error')
        return redirect(url_for('admin.admins'))

    if admin_user.id == admin.id:
        flash('You cannot toggle your own active status.', 'error')
        return redirect(url_for('admin.admins'))

    admin_user.is_active = not admin_user.is_active
    admin_user.save()

    status = 'activated' if admin_user.is_active else 'deactivated'
    log_admin_action(admin.id, f'{status}_admin', 'admin_user', admin_id,
                   f'{status.title()} admin user {admin_user.email}', request.remote_addr)
    flash(f'Admin user {admin_user.email} has been {status}.', 'success')

    return redirect(url_for('admin.admins'))


@admin_bp.route('/admins/<int:admin_id>/reset-password', methods=['POST'])
@super_admin_required
def reset_admin_password(admin_id):
    """Reset admin password and send email"""
    admin = get_current_admin()
    admin_user = AdminUser.get_by_id(admin_id)

    if not admin_user:
        flash('Admin user not found.', 'error')
        return redirect(url_for('admin.admins'))

    if not admin_user.is_active:
        flash('Cannot reset password for inactive admin user.', 'error')
        return redirect(url_for('admin.admins'))

    temp_password = ''.join(secrets.choice(string.ascii_letters + string.digits + '!@#$%^&*') for _ in range(16))
    admin_user.set_password(temp_password)
    admin_user.must_change_password = True
    admin_user.save()

    try:
        from email_service import email_service
        email_service.send_admin_password_reset_email(admin_user.email, admin_user.full_name, temp_password)
        email_sent = True
    except Exception as e:
        logger.error(f'Failed to send password reset email: {e}')
        email_sent = False

    log_admin_action(admin.id, 'reset_admin_password', 'admin_user', admin_id,
                   f'Reset password for {admin_user.email}' + (' (email sent)' if email_sent else ' (email failed)'), request.remote_addr)

    if email_sent:
        flash(f'Password reset email sent to {admin_user.email}.', 'success')
    else:
        flash(f'Password reset but email failed to send. Temporary password: {temp_password}', 'warning')

    return redirect(url_for('admin.admins'))


@admin_bp.route('/change-password', methods=['GET', 'POST'])
@admin_required
def change_password():
    """Change own password"""
    admin = get_current_admin()
    form = ChangePasswordForm(admin.id)

    if form.validate_on_submit():
        if not admin.check_password(form.current_password.data):
            flash('Current password is incorrect.', 'error')
        else:
            admin.set_password(form.new_password.data)
            admin.must_change_password = False
            admin.save()

            log_admin_action(admin.id, 'change_own_password', 'admin_user', admin.id,
                           'Changed own password', request.remote_addr)
            flash('Password changed successfully.', 'success')
            return redirect(url_for('admin.dashboard'))

    return render_template('admin/change_password.html',
                           admin=admin,
                           form=form,
                           force_change=False)


@admin_bp.route('/force-password-change', methods=['GET', 'POST'])
@admin_required
def force_password_change():
    """Force password change page (redirected here if must_change_password is True)"""
    admin = get_current_admin()

    if not admin.must_change_password:
        return redirect(url_for('admin.dashboard'))

    form = ChangePasswordForm(admin.id)

    if form.validate_on_submit():
        admin.set_password(form.new_password.data)
        admin.must_change_password = False
        admin.save()

        log_admin_action(admin.id, 'forced_password_change', 'admin_user', admin.id,
                       'Completed forced password change', request.remote_addr)
        flash('Password changed successfully. You can now access the admin panel.', 'success')
        return redirect(url_for('admin.dashboard'))

    return render_template('admin/change_password.html',
                           admin=admin,
                           form=form,
                           force_change=True)


# =============================================================================
# CMS Pages Management
# =============================================================================

@admin_bp.route('/pages')
@super_admin_required
def pages():
    """List all editable pages"""
    admin = get_current_admin()
    
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    try:
        cursor.execute("""
            SELECT id, page_slug, title, is_published, published_at, updated_at
            FROM page_content
            ORDER BY updated_at DESC
        """)
        pages_list = cursor.fetchall()
    finally:
        cursor.close()
        conn.close()
    
    return render_template('admin/pages.html',
                           admin=admin,
                           pages=pages_list)


@admin_bp.route('/pages/<slug>/edit', methods=['GET', 'POST'])
@super_admin_required
def page_edit(slug):
    """Edit page content"""
    admin = get_current_admin()
    
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    try:
        cursor.execute("SELECT * FROM page_content WHERE page_slug = %s", (slug,))
        page = cursor.fetchone()
        
        if not page:
            flash('Page not found.', 'error')
            return redirect(url_for('admin.pages'))
        
        if request.method == 'POST':
            import json

            raw_body = request.form.get('content_body', '')
            content_data, error_message = parse_page_editor_content(raw_body)
            if error_message:
                flash(error_message, 'error')
                return render_template('admin/page_edit.html',
                                       admin=admin,
                                       page=page,
                                       editor_content=raw_body,
                                       slug=slug)

            content_json = json.dumps(content_data)
            title = request.form.get('title', page['title'])

            cursor.execute("""
                UPDATE page_content 
                SET title = %s, content = %s, updated_at = NOW()
                WHERE id = %s
            """, (title, content_json, page['id']))
            conn.commit()

            cursor.execute("""
                INSERT INTO page_versions (page_id, content, changed_by_admin_id, change_summary)
                VALUES (%s, %s, %s, %s)
            """, (page['id'], content_json, admin.id, request.form.get('change_summary', 'Content updated')))
            conn.commit()

            log_admin_action(admin.id, 'page_edit', 'page_content', page['id'],
                           f'Edited page: {slug}', request.remote_addr)
            flash(f'Page "{page["title"]}" saved successfully.', 'success')

            return redirect(url_for('admin.page_edit', slug=slug))
        
        import json
        if page['content'] and isinstance(page['content'], str):
            page_content = json.loads(page['content'])
        else:
            page_content = page['content']

        editor_content = serialize_page_content(page_content or {})

        return render_template('admin/page_edit.html',
                               admin=admin,
                               page=page,
                               editor_content=editor_content,
                               slug=slug)
    finally:
        cursor.close()
        conn.close()


@admin_bp.route('/pages/<slug>/preview', methods=['GET', 'POST'])
@super_admin_required
def page_preview(slug):
    """Preview page (draft or published) in modal"""
    admin = get_current_admin()
    
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    try:
        cursor.execute("SELECT * FROM page_content WHERE page_slug = %s", (slug,))
        page = cursor.fetchone()

        if not page:
            return jsonify({'error': 'Page not found'}), 404

        import json
        if request.method == 'POST':
            raw_body = request.form.get('content_body', '')
            content_data, error_message = parse_page_editor_content(raw_body)
            if error_message:
                return jsonify({'success': False, 'error': error_message}), 400
            content_data['title'] = request.form.get('title', page['title'])
            page_content = content_data
        else:
            if page['content'] and isinstance(page['content'], str):
                page_content = json.loads(page['content'])
            else:
                page_content = page['content']

        preview_html = render_page_content(slug, page_content, preview=True)

        return jsonify({
            'success': True,
            'title': page_content.get('title', page['title']) if isinstance(page_content, dict) else page['title'],
            'html': preview_html
        })
    finally:
        cursor.close()
        conn.close()


@admin_bp.route('/pages/<slug>/publish', methods=['POST'])
@super_admin_required
def page_publish(slug):
    """Publish a page"""
    admin = get_current_admin()
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT id, title FROM page_content WHERE page_slug = %s", (slug,))
        page = cursor.fetchone()
        
        if not page:
            flash('Page not found.', 'error')
            return redirect(url_for('admin.pages'))
        
        cursor.execute("""
            UPDATE page_content 
            SET is_published = TRUE, published_at = NOW(), updated_at = NOW()
            WHERE id = %s
        """, (page[0],))
        conn.commit()
        
        log_admin_action(admin.id, 'page_publish', 'page_content', page[0],
                       f'Published page: {slug}', request.remote_addr)
        flash(f'Page "{page[1]}" has been published.', 'success')
    finally:
        cursor.close()
        conn.close()
    
    return redirect(url_for('admin.pages'))


@admin_bp.route('/pages/<slug>/unpublish', methods=['POST'])
@super_admin_required
def page_unpublish(slug):
    """Unpublish a page"""
    admin = get_current_admin()
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT id, title FROM page_content WHERE page_slug = %s", (slug,))
        page = cursor.fetchone()
        
        if not page:
            flash('Page not found.', 'error')
            return redirect(url_for('admin.pages'))
        
        cursor.execute("""
            UPDATE page_content 
            SET is_published = FALSE, updated_at = NOW()
            WHERE id = %s
        """, (page[0],))
        conn.commit()
        
        log_admin_action(admin.id, 'page_unpublish', 'page_content', page[0],
                       f'Unpublished page: {slug}', request.remote_addr)
        flash(f'Page "{page[1]}" has been unpublished.', 'success')
    finally:
        cursor.close()
        conn.close()
    
    return redirect(url_for('admin.pages'))


@admin_bp.route('/pages/<slug>/history')
@super_admin_required
def page_history(slug):
    """Version history for a page"""
    admin = get_current_admin()
    
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    try:
        cursor.execute("SELECT * FROM page_content WHERE page_slug = %s", (slug,))
        page = cursor.fetchone()
        
        if not page:
            flash('Page not found.', 'error')
            return redirect(url_for('admin.pages'))
        
        cursor.execute("""
            SELECT pv.*, au.full_name as changed_by_name
            FROM page_versions pv
            LEFT JOIN admin_users au ON pv.changed_by_admin_id = au.id
            WHERE pv.page_id = %s
            ORDER BY pv.created_at DESC
        """, (page['id'],))
        versions = cursor.fetchall()
        
        for v in versions:
            if v['created_at']:
                v['created_at'] = v['created_at'].strftime('%Y-%m-%d %H:%M:%S')
        
        return render_template('admin/page_history.html',
                               admin=admin,
                               page=page,
                               versions=versions,
                               slug=slug)
    finally:
        cursor.close()
        conn.close()


@admin_bp.route('/pages/<slug>/rollback/<int:version_id>', methods=['POST'])
@super_admin_required
def page_rollback(slug, version_id):
    """Rollback page to a specific version"""
    admin = get_current_admin()
    
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    try:
        cursor.execute("SELECT id, title FROM page_content WHERE page_slug = %s", (slug,))
        page = cursor.fetchone()
        
        if not page:
            flash('Page not found.', 'error')
            return redirect(url_for('admin.pages'))
        
        cursor.execute("SELECT * FROM page_versions WHERE id = %s AND page_id = %s", 
                      (version_id, page['id']))
        version = cursor.fetchone()
        
        if not version:
            flash('Version not found.', 'error')
            return redirect(url_for('admin.page_history', slug=slug))
        
        cursor.execute("""
            UPDATE page_content 
            SET content = %s, updated_at = NOW()
            WHERE id = %s
        """, (version['content'], page['id']))
        
        cursor.execute("""
            INSERT INTO page_versions (page_id, content, changed_by_admin_id, change_summary)
            VALUES (%s, %s, %s, %s)
        """, (page['id'], version['content'], admin.id, 
              f'Rolback to version {version_id} from {version["created_at"]}'))
        conn.commit()
        
        log_admin_action(admin.id, 'page_rollback', 'page_content', page['id'],
                       f'Rolled back page {slug} to version {version_id}', request.remote_addr)
        flash(f'Page has been rolled back to version from {version["created_at"]}.', 'success')
    except Exception as e:
        flash(f'Error during rollback: {str(e)}', 'error')
    finally:
        cursor.close()
        conn.close()
    
    return redirect(url_for('admin.page_history', slug=slug))


@admin_bp.route('/api/pages/<slug>')
@super_admin_required
def api_page_content(slug):
    """Get page content as JSON"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    try:
        cursor.execute("SELECT * FROM page_content WHERE page_slug = %s", (slug,))
        page = cursor.fetchone()
        
        if not page:
            return jsonify({'error': 'Page not found'}), 404
        
        import json
        content = page['content']
        if isinstance(content, str):
            content = json.loads(content)
        
        return jsonify({
            'id': page['id'],
            'slug': page['page_slug'],
            'title': page['title'],
            'content': content,
            'is_published': page['is_published'],
            'published_at': page['published_at'].isoformat() if page['published_at'] else None,
            'updated_at': page['updated_at'].isoformat() if page['updated_at'] else None
        })
    finally:
        cursor.close()
        conn.close()


# =============================================================================
# Pricing Plans Management (Super Admin Only)
# =============================================================================

@admin_bp.route('/pricing')
@super_admin_required
def pricing_plans():
    """List all pricing plans"""
    admin = get_current_admin()
    plans = PricingPlan.get_all()

    # Group by platform
    woocommerce_plans = [p for p in plans if p.platform == 'woocommerce']
    magento_plans = [p for p in plans if p.platform == 'magento']

    return render_template('admin/pricing_plans.html',
                           admin=admin,
                           woocommerce_plans=woocommerce_plans,
                           magento_plans=magento_plans)


@admin_bp.route('/pricing/<int:plan_id>/edit', methods=['GET', 'POST'])
@super_admin_required
def pricing_plan_edit(plan_id):
    """Edit a pricing plan"""
    admin = get_current_admin()
    plan = PricingPlan.get_by_id(plan_id)

    if not plan:
        flash('Pricing plan not found.', 'error')
        return redirect(url_for('admin.pricing_plans'))

    if request.method == 'POST':
        try:
            # Update basic fields
            plan.name = request.form.get('name', plan.name)
            plan.price_monthly = float(request.form.get('price_monthly', plan.price_monthly))
            plan.store_limit = int(request.form.get('store_limit', plan.store_limit))
            plan.memory_limit = request.form.get('memory_limit', plan.memory_limit)
            plan.cpu_limit = request.form.get('cpu_limit', plan.cpu_limit)
            plan.display_order = int(request.form.get('display_order', plan.display_order))
            plan.is_active = request.form.get('is_active') == 'on'

            # Update features from checkboxes
            feature_keys = [
                'daily_backups', 'email_support', 'premium_plugins', 'support_24_7',
                'redis_cache', 'staging', 'sla_uptime', 'advanced_security',
                'centralized_management', 'white_label', 'dedicated_support'
            ]
            plan.features = {key: request.form.get(f'feature_{key}') == 'on' for key in feature_keys}

            plan.update()

            log_admin_action(admin.id, 'pricing_plan_edit', f"Updated pricing plan: {plan.name} (ID: {plan.id})")
            flash(f'Pricing plan "{plan.name}" updated successfully.', 'success')
            return redirect(url_for('admin.pricing_plans'))

        except ValueError as e:
            flash(f'Invalid value: {str(e)}', 'error')
        except Exception as e:
            flash(f'Error updating plan: {str(e)}', 'error')

    return render_template('admin/pricing_plan_edit.html',
                           admin=admin,
                           plan=plan)


# =============================================================================
# Stripe Pricing Sync API
# =============================================================================

@admin_bp.route('/api/pricing/sync-options')
@super_admin_required
def api_pricing_sync_options():
    """Get Stripe sync options for all pricing plans"""
    try:
        from stripe_integration.pricing import get_all_pricing_sync_status
        result = get_all_pricing_sync_status()
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@admin_bp.route('/api/pricing/sync/<int:plan_id>', methods=['POST'])
@super_admin_required
def api_pricing_sync(plan_id):
    """Sync pricing plan to Stripe"""
    try:
        from stripe_integration.pricing import sync_price_to_stripe
        create_new = request.json.get('create_new', False) if request.json else False
        result = sync_price_to_stripe(plan_id, create_new)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


# =============================================================================
# Page Rendering Helpers
# =============================================================================

def serialize_page_content(content):
    """Convert structured page content into editor markdown."""
    import json

    if not content:
        return ''

    lines = []
    for section, section_data in content.items():
        if isinstance(section_data, dict):
            for key, value in section_data.items():
                # Handle nested complex types (lists, dicts) as JSON
                if isinstance(value, (list, dict)):
                    lines.append(f"## section: {section}.{key} (json)")
                    lines.append("```json")
                    lines.append(json.dumps(value, indent=2))
                    lines.append("```")
                else:
                    lines.append(f"## section: {section}.{key}")
                    lines.append(str(value) if value is not None else '')
                lines.append('')
        elif isinstance(section_data, str):
            lines.append(f"## section: {section}")
            lines.append(section_data)
            lines.append('')
        else:
            lines.append(f"## section: {section} (json)")
            lines.append("```json")
            lines.append(json.dumps(section_data, indent=2))
            lines.append("```")
            lines.append('')

    return "\n".join(lines).strip() + "\n"


def parse_page_editor_content(raw_text):
    """Parse editor markdown into structured page content."""
    import json

    if not raw_text or not raw_text.strip():
        return {}, 'Editor is empty. Add at least one section header.'

    pattern = re.compile(r'^##\s+section:\s*(.+)$', re.MULTILINE)
    matches = list(pattern.finditer(raw_text))
    if not matches:
        return {}, "No section headers found. Use '## section: <name>' to define sections."

    content = {}
    for index, match in enumerate(matches):
        header = match.group(1).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw_text)
        body = raw_text[start:end].strip('\n').strip()

        is_json = header.lower().endswith('(json)')
        if is_json:
            header = header[:-6].strip()
            json_text = body
            if json_text.startswith('```'):
                lines = json_text.splitlines()
                if len(lines) >= 2 and lines[-1].strip().startswith('```'):
                    json_text = "\n".join(lines[1:-1]).strip()
            try:
                value = json.loads(json_text) if json_text else {}
            except json.JSONDecodeError as exc:
                return {}, f"Invalid JSON in section '{header}': {exc.msg}."
        else:
            value = body

        if '.' in header:
            section, key = header.split('.', 1)
            content.setdefault(section, {})[key] = value
        else:
            content[header] = value

    return content, None

def render_page_content(slug, content, preview=False):
    """Render page content to HTML based on page type"""
    from flask import render_template_string
    
    base_template = '''
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{{ title }} - ShopHosting.io</title>
        <link rel="preconnect" href="https://fonts.googleapis.com">
        <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
        <link href="https://fonts.googleapis.com/css2?family=DM+Sans:opsz,wght@9..40,300;9..40,400;9..40,500;9..40,600;9..40,700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
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
                --accent-cyan: #00d4ff;
                --accent-blue: #0088ff;
                --accent-indigo: #5b5bd6;
                --gradient-primary: linear-gradient(135deg, #00d4ff 0%, #0088ff 50%, #5b5bd6 100%);
                --success: #22c55e;
            }
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body {
                font-family: 'DM Sans', -apple-system, BlinkMacSystemFont, sans-serif;
                line-height: 1.6;
                color: var(--text-primary);
                background: var(--bg-deepest);
            }
            .preview-banner {
                background: var(--accent-blue);
                color: white;
                text-align: center;
                padding: 8px;
                font-size: 14px;
                font-weight: 500;
            }
            .container { max-width: 1200px; margin: 0 auto; padding: 0 24px; }
            header { 
                background: rgba(8, 8, 10, 0.9); 
                backdrop-filter: blur(20px);
                border-bottom: 1px solid rgba(255,255,255,0.06);
                padding: 16px 0;
                position: sticky; top: 0; z-index: 100;
            }
            header .container { display: flex; justify-content: space-between; align-items: center; }
            .logo {
                font-size: 1.4rem; font-weight: 700; text-decoration: none;
                background: var(--gradient-primary);
                -webkit-background-clip: text; -webkit-text-fill-color: transparent;
            }
            nav { display: flex; gap: 8px; }
            nav a {
                padding: 10px 18px; color: var(--text-secondary); text-decoration: none;
                font-weight: 500; border-radius: 10px; transition: all 0.2s;
            }
            nav a:hover { color: var(--text-primary); background: var(--bg-hover); }
            main { min-height: calc(100vh - 180px); padding: 60px 0; }
            
            /* Hero */
            .hero { text-align: center; padding: 80px 0; }
            .hero h1 { 
                font-size: 3.5rem; font-weight: 700; margin-bottom: 20px; 
                background: var(--gradient-primary); -webkit-background-clip: text; -webkit-text-fill-color: transparent;
            }
            .hero p { font-size: 1.25rem; color: var(--text-secondary); max-width: 600px; margin: 0 auto 40px; }
            .btn {
                display: inline-flex; align-items: center; gap: 8px;
                padding: 14px 28px; border-radius: 10px; font-weight: 600;
                text-decoration: none; cursor: pointer; border: none;
                background: var(--gradient-primary); color: var(--bg-deepest);
                transition: all 0.25s;
            }
            .btn:hover { transform: translateY(-2px); box-shadow: 0 0 40px rgba(0, 136, 255, 0.3); }
            
            /* Stats */
            .stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 24px; margin: 60px 0; }
            .stat { 
                background: var(--bg-elevated); border: 1px solid rgba(255,255,255,0.06);
                padding: 32px; text-align: center; border-radius: 16px;
            }
            .stat-value { font-size: 2.5rem; font-weight: 700; color: var(--accent-cyan); }
            .stat-label { color: var(--text-secondary); margin-top: 8px; }
            
            /* Features grid */
            .features-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 24px; }
            .feature {
                background: var(--bg-elevated); border: 1px solid rgba(255,255,255,0.06);
                padding: 32px; border-radius: 16px;
            }
            .feature h3 { margin-bottom: 12px; }
            .feature p { color: var(--text-secondary); }
            
            /* CTA */
            .cta { 
                text-align: center; padding: 80px 0; background: var(--bg-elevated);
                border-radius: 24px; margin: 60px 0;
            }
            .cta h2 { font-size: 2.5rem; margin-bottom: 16px; }
            .cta p { color: var(--text-secondary); margin-bottom: 32px; }
            
            footer { 
                background: var(--bg-deep); border-top: 1px solid rgba(255,255,255,0.06);
                color: var(--text-tertiary); padding: 32px 0; text-align: center;
            }
        </style>
    </head>
    <body>
        {% if preview %}
        <div class="preview-banner">PREVIEW MODE - Changes not yet live</div>
        {% endif %}
        <header>
            <div class="container">
                <a href="/" class="logo">ShopHosting.io</a>
                <nav>
                    <a href="/features">Features</a>
                    <a href="/pricing">Pricing</a>
                    <a href="/about">About</a>
                    <a href="/contact">Contact</a>
                    <a href="/login">Login</a>
                    <a href="/signup" style="background: var(--gradient-primary); color: var(--bg-deepest);">Free Consultation</a>
                </nav>
            </div>
        </header>
        <main>
            {{ content_html }}
        </main>
        <footer>
            <p>&copy; 2025 ShopHosting.io. All rights reserved.</p>
        </footer>
    </body>
    </html>
    '''
    
    content_html = ''
    
    if slug == 'home' and content:
        content_html = _render_homepage(content)
    elif slug == 'pricing' and content:
        content_html = _render_pricing_page(content)
    elif slug == 'features' and content:
        content_html = _render_features_page(content)
    elif slug == 'about' and content:
        content_html = _render_about_page(content)
    elif slug == 'contact' and content:
        content_html = _render_contact_page(content)
    else:
        content_html = f'<div class="container"><h1>{content.get("title", slug)}</h1><p>Content placeholder for {slug}</p></div>'
    
    return render_template_string(base_template, title=content.get('title', slug), content_html=content_html, preview=preview)


def _render_homepage(content):
    hero = content.get('hero', {})
    stats = content.get('stats', {})
    cta = content.get('cta', {})
    
    return f'''
    <div class="container">
        <section class="hero">
            <h1>{hero.get("headline", "")}</h1>
            <p>{hero.get("subheadline", "")}</p>
            <a href="{hero.get("cta_link", "/signup")}" class="btn">{hero.get("cta_text", "Get Started")}</a>
        </section>
        
        <section class="stats">
            <div class="stat">
                <div class="stat-value">{stats.get("stores_count", "100+")}</div>
                <div class="stat-label">Active Stores</div>
            </div>
            <div class="stat">
                <div class="stat-value">{stats.get("uptime", "99.9%")}</div>
                <div class="stat-label">Uptime SLA</div>
            </div>
            <div class="stat">
                <div class="stat-value">{stats.get("hours_saved", "5000+")}</div>
                <div class="stat-label">Dev Hours Saved</div>
            </div>
        </section>
        
        <section class="cta">
            <h2>{cta.get("headline", "Ready to Scale?")}</h2>
            <p>{cta.get("subheadline", "")}</p>
            <a href="{cta.get("button_link", "/signup")}" class="btn">{cta.get("button_text", "Get Started")}</a>
        </section>
    </div>
    '''


def _render_pricing_page(content):
    header = content.get('header', {})
    
    return f'''
    <div class="container">
        <section class="hero">
            <h1>{header.get("headline", "")}</h1>
            <p>{header.get("subheadline", "")}</p>
        </section>
        
        <div style="text-align: center; padding: 40px;">
            <p style="color: var(--text-secondary);">Pricing plans loaded from database.</p>
            <p style="color: var(--text-tertiary); margin-top: 16px;">Edit pricing content in the CMS.</p>
        </div>
    </div>
    '''


def _render_features_page(content):
    hero = content.get('hero', {})
    
    return f'''
    <div class="container">
        <section class="hero">
            <h1>{hero.get("headline", "")}</h1>
            <p>{hero.get("subheadline", "")}</p>
        </section>
    </div>
    '''


def _render_about_page(content):
    hero = content.get('hero', {})
    
    return f'''
    <div class="container">
        <section class="hero">
            <h1>{hero.get("headline", "")}</h1>
            <p>{hero.get("subheadline", "")}</p>
        </section>
    </div>
    '''


def _render_contact_page(content):
    hero = content.get('hero', {})
    
    return f'''
    <div class="container">
        <section class="hero">
            <h1>{hero.get("headline", "")}</h1>
            <p>{hero.get("subheadline", "")}</p>
        </section>
    </div>
    '''
