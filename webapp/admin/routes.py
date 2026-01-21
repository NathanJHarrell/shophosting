"""
Admin Panel Routes
Handles all admin page routes and actions
"""

import os
import subprocess
from functools import wraps
from datetime import datetime, timedelta

from flask import render_template, request, redirect, url_for, flash, session, jsonify
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField
from wtforms.validators import DataRequired, Email

from . import admin_bp
from .models import AdminUser, log_admin_action

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models import Customer, PortManager, get_db_connection, PricingPlan, Subscription, Invoice
from models import Ticket, TicketMessage, TicketAttachment, TicketCategory


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

@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    """Admin login page"""
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
            flash('Welcome back, {}!'.format(admin.full_name), 'success')
            return redirect(url_for('admin.dashboard'))
        else:
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

    # Get customer stats
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

    # Get related data
    subscription = Subscription.get_by_customer_id(customer_id)
    invoices = Invoice.get_by_customer_id(customer_id)
    jobs = get_provisioning_jobs(customer_id)
    audit_logs = get_customer_audit_logs(customer_id)

    return render_template('admin/customer_detail.html',
                           admin=admin,
                           customer=customer,
                           subscription=subscription,
                           invoices=invoices,
                           jobs=jobs,
                           audit_logs=audit_logs)


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
    """Get Redis queue statistics"""
    try:
        from redis import Redis
        from rq import Queue

        redis_host = os.getenv('REDIS_HOST', 'localhost')
        redis_conn = Redis(host=redis_host, port=6379)
        queue = Queue('provisioning', connection=redis_conn)

        return {
            'queued': len(queue),
            'started': len(queue.started_job_registry),
            'finished': len(queue.finished_job_registry),
            'failed': len(queue.failed_job_registry),
            'connected': True
        }
    except Exception as e:
        return {
            'queued': 0,
            'started': 0,
            'finished': 0,
            'failed': 0,
            'connected': False,
            'error': str(e)
        }


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

    try:
        # Stop and remove containers
        if os.path.exists(customer_path):
            subprocess.run(
                ['docker', 'compose', 'down', '-v'],
                cwd=customer_path,
                capture_output=True,
                timeout=60
            )
            # Remove customer directory
            import shutil
            shutil.rmtree(customer_path)

        # Remove Nginx config
        nginx_available = f"/etc/nginx/sites-available/customer-{customer_id}.conf"
        nginx_enabled = f"/etc/nginx/sites-enabled/customer-{customer_id}.conf"

        if os.path.exists(nginx_enabled):
            os.unlink(nginx_enabled)
        if os.path.exists(nginx_available):
            os.unlink(nginx_available)

        # Reload nginx
        subprocess.run(['sudo', 'systemctl', 'reload', 'nginx'], capture_output=True)

        # Delete from database
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM provisioning_jobs WHERE customer_id = %s", (customer_id,))
        cursor.execute("DELETE FROM customers WHERE id = %s", (customer_id,))
        conn.commit()
        cursor.close()
        conn.close()

        log_admin_action(admin.id, 'delete_customer', 'customer', customer_id,
                        f'Deleted customer {email} and removed containers', request.remote_addr)
        flash(f'Customer {email} and all associated resources deleted successfully.', 'success')
    except Exception as e:
        flash(f'Error deleting customer: {str(e)}', 'error')

    return redirect(url_for('admin.manage_customers'))
