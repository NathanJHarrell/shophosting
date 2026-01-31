"""
Mail Management Admin Routes
Provides admin interface for mailbox, alias, and autoresponder management
"""

import logging
from functools import wraps

from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify

from .routes import admin_required, get_current_admin
from .models import log_admin_action
from .mail import Mailbox, Alias, Autoresponder, get_maildir_size, MAIL_DOMAIN

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models import get_db_connection

logger = logging.getLogger(__name__)

# Create mail blueprint as nested under admin
mail_bp = Blueprint('mail', __name__, url_prefix='/mail')


# =============================================================================
# Dashboard
# =============================================================================

@mail_bp.route('/')
@admin_required
def dashboard():
    """Mail management dashboard with statistics"""
    conn = get_db_connection()
    try:
        stats = Mailbox.get_stats(conn)

        # Get alias count
        cursor = conn.cursor(dictionary=True)
        cursor.execute('SELECT COUNT(*) as total FROM mail_aliases')
        alias_count = cursor.fetchone()['total']

        # Get active autoresponders count
        cursor.execute('SELECT COUNT(*) as total FROM mail_autoresponders WHERE is_active = 1')
        autoresponder_count = cursor.fetchone()['total']
        cursor.close()

        stats['aliases'] = alias_count
        stats['autoresponders'] = autoresponder_count

    finally:
        conn.close()

    return render_template('admin/mail/dashboard.html', stats=stats, mail_domain=MAIL_DOMAIN)


# =============================================================================
# Mailbox Management
# =============================================================================

@mail_bp.route('/mailboxes')
@admin_required
def mailboxes():
    """List all mailboxes with search, filter, and pagination"""
    search = request.args.get('search', '')
    status = request.args.get('status', '')
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)

    conn = get_db_connection()
    try:
        mailboxes_list, total = Mailbox.get_all(
            conn, search=search, status=status, page=page, per_page=per_page
        )

        # Calculate pagination info
        total_pages = (total + per_page - 1) // per_page

        # Add disk usage for each mailbox
        for mailbox in mailboxes_list:
            mailbox['disk_usage'] = get_maildir_size(mailbox['username'])

    finally:
        conn.close()

    return render_template(
        'admin/mail/mailboxes.html',
        mailboxes=mailboxes_list,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        search=search,
        status=status,
        mail_domain=MAIL_DOMAIN
    )


@mail_bp.route('/mailboxes/create', methods=['GET', 'POST'])
@admin_required
def create_mailbox():
    """Create a new mailbox"""
    admin = get_current_admin()

    if request.method == 'POST':
        username = request.form.get('username', '').strip().lower()
        password = request.form.get('password', '')
        quota_mb = request.form.get('quota_mb', 1024, type=int)
        is_system_user = request.form.get('is_system_user') == '1'

        if not username:
            flash('Username is required.', 'error')
            return render_template('admin/mail/mailbox_form.html', action='create', mail_domain=MAIL_DOMAIN)

        if not password:
            flash('Password is required.', 'error')
            return render_template('admin/mail/mailbox_form.html', action='create', mail_domain=MAIL_DOMAIN)

        if len(password) < 8:
            flash('Password must be at least 8 characters.', 'error')
            return render_template('admin/mail/mailbox_form.html', action='create', mail_domain=MAIL_DOMAIN)

        conn = get_db_connection()
        try:
            mailbox_id = Mailbox.create(
                conn,
                username=username,
                password=password,
                quota_mb=quota_mb,
                is_system_user=is_system_user
            )

            log_admin_action(
                admin.id, 'mail_create_mailbox', 'mailbox', mailbox_id,
                f'Created mailbox {username}@{MAIL_DOMAIN}', request.remote_addr
            )

            flash(f'Mailbox {username}@{MAIL_DOMAIN} created successfully.', 'success')
            return redirect(url_for('admin.mail.mailboxes'))

        except ValueError as e:
            flash(str(e), 'error')
        finally:
            conn.close()

    return render_template('admin/mail/mailbox_form.html', action='create', mail_domain=MAIL_DOMAIN)


@mail_bp.route('/mailboxes/<int:mailbox_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_mailbox(mailbox_id):
    """Edit mailbox settings including password reset and autoresponder"""
    admin = get_current_admin()

    conn = get_db_connection()
    try:
        mailbox = Mailbox.get_by_id(conn, mailbox_id)
        if not mailbox:
            flash('Mailbox not found.', 'error')
            return redirect(url_for('admin.mail.mailboxes'))

        autoresponder = Autoresponder.get_by_mailbox(conn, mailbox_id)
        aliases = Alias.get_all(conn, mailbox_id=mailbox_id)
        mailbox['disk_usage'] = get_maildir_size(mailbox['username'])

        if request.method == 'POST':
            action = request.form.get('action', 'update')

            if action == 'update':
                # Update mailbox settings
                quota_mb = request.form.get('quota_mb', 1024, type=int)
                is_active = request.form.get('is_active') == '1'

                Mailbox.update(conn, mailbox_id, quota_mb=quota_mb, is_active=is_active)

                log_admin_action(
                    admin.id, 'mail_update_mailbox', 'mailbox', mailbox_id,
                    f'Updated mailbox {mailbox["email"]}', request.remote_addr
                )

                flash('Mailbox updated successfully.', 'success')

            elif action == 'reset_password':
                # Reset password
                new_password = request.form.get('new_password', '')

                if not new_password or len(new_password) < 8:
                    flash('Password must be at least 8 characters.', 'error')
                else:
                    Mailbox.set_password(conn, mailbox_id, new_password)

                    log_admin_action(
                        admin.id, 'mail_reset_password', 'mailbox', mailbox_id,
                        f'Reset password for {mailbox["email"]}', request.remote_addr
                    )

                    flash('Password reset successfully.', 'success')

            elif action == 'autoresponder':
                # Update autoresponder
                ar_subject = request.form.get('ar_subject', '')
                ar_body = request.form.get('ar_body', '')
                ar_active = request.form.get('ar_active') == '1'
                ar_start = request.form.get('ar_start_date') or None
                ar_end = request.form.get('ar_end_date') or None

                Autoresponder.save(
                    conn, mailbox_id,
                    subject=ar_subject,
                    body=ar_body,
                    is_active=ar_active,
                    start_date=ar_start,
                    end_date=ar_end
                )

                log_admin_action(
                    admin.id, 'mail_update_autoresponder', 'mailbox', mailbox_id,
                    f'Updated autoresponder for {mailbox["email"]}', request.remote_addr
                )

                flash('Autoresponder updated successfully.', 'success')

            # Refresh data
            mailbox = Mailbox.get_by_id(conn, mailbox_id)
            autoresponder = Autoresponder.get_by_mailbox(conn, mailbox_id)
            mailbox['disk_usage'] = get_maildir_size(mailbox['username'])

    finally:
        conn.close()

    return render_template(
        'admin/mail/mailbox_form.html',
        action='edit',
        mailbox=mailbox,
        autoresponder=autoresponder,
        aliases=aliases,
        mail_domain=MAIL_DOMAIN
    )


@mail_bp.route('/mailboxes/<int:mailbox_id>/delete', methods=['POST'])
@admin_required
def delete_mailbox(mailbox_id):
    """Delete a mailbox and its maildir"""
    admin = get_current_admin()

    conn = get_db_connection()
    try:
        mailbox = Mailbox.get_by_id(conn, mailbox_id)
        if not mailbox:
            flash('Mailbox not found.', 'error')
            return redirect(url_for('admin.mail.mailboxes'))

        if mailbox['is_system_user']:
            flash('Cannot delete system mailboxes.', 'error')
            return redirect(url_for('admin.mail.mailboxes'))

        email = mailbox['email']
        Mailbox.delete(conn, mailbox_id)

        log_admin_action(
            admin.id, 'mail_delete_mailbox', 'mailbox', mailbox_id,
            f'Deleted mailbox {email}', request.remote_addr
        )

        flash(f'Mailbox {email} deleted successfully.', 'success')

    except ValueError as e:
        flash(str(e), 'error')
    finally:
        conn.close()

    return redirect(url_for('admin.mail.mailboxes'))


# =============================================================================
# Alias Management
# =============================================================================

@mail_bp.route('/aliases')
@admin_required
def aliases():
    """List all aliases"""
    conn = get_db_connection()
    try:
        aliases_list = Alias.get_all(conn)
    finally:
        conn.close()

    return render_template('admin/mail/aliases.html', aliases=aliases_list, mail_domain=MAIL_DOMAIN)


@mail_bp.route('/aliases/create', methods=['GET', 'POST'])
@admin_required
def create_alias():
    """Create a new alias"""
    admin = get_current_admin()

    conn = get_db_connection()
    try:
        # Get all mailboxes for destination dropdown
        mailboxes_list, _ = Mailbox.get_all(conn, per_page=1000)

        if request.method == 'POST':
            alias_name = request.form.get('alias', '').strip().lower()
            destination_id = request.form.get('destination_id', type=int)

            if not alias_name:
                flash('Alias name is required.', 'error')
                return render_template(
                    'admin/mail/alias_form.html',
                    action='create',
                    mailboxes=mailboxes_list,
                    mail_domain=MAIL_DOMAIN
                )

            if not destination_id:
                flash('Destination mailbox is required.', 'error')
                return render_template(
                    'admin/mail/alias_form.html',
                    action='create',
                    mailboxes=mailboxes_list,
                    mail_domain=MAIL_DOMAIN
                )

            try:
                alias_id = Alias.create(conn, alias_name, destination_id)

                # Build full alias email for logging
                alias_email = alias_name if '@' in alias_name else f'{alias_name}@{MAIL_DOMAIN}'

                log_admin_action(
                    admin.id, 'mail_create_alias', 'alias', alias_id,
                    f'Created alias {alias_email}', request.remote_addr
                )

                flash(f'Alias {alias_email} created successfully.', 'success')
                return redirect(url_for('admin.mail.aliases'))

            except ValueError as e:
                flash(str(e), 'error')

    finally:
        conn.close()

    return render_template(
        'admin/mail/alias_form.html',
        action='create',
        mailboxes=mailboxes_list,
        mail_domain=MAIL_DOMAIN
    )


@mail_bp.route('/aliases/<int:alias_id>/delete', methods=['POST'])
@admin_required
def delete_alias(alias_id):
    """Delete an alias"""
    admin = get_current_admin()

    conn = get_db_connection()
    try:
        # Get alias info for logging
        cursor = conn.cursor(dictionary=True)
        cursor.execute('SELECT * FROM mail_aliases WHERE id = %s', (alias_id,))
        alias = cursor.fetchone()
        cursor.close()

        if not alias:
            flash('Alias not found.', 'error')
            return redirect(url_for('admin.mail.aliases'))

        Alias.delete(conn, alias_id)

        log_admin_action(
            admin.id, 'mail_delete_alias', 'alias', alias_id,
            f'Deleted alias {alias["alias"]}', request.remote_addr
        )

        flash(f'Alias {alias["alias"]} deleted successfully.', 'success')

    finally:
        conn.close()

    return redirect(url_for('admin.mail.aliases'))


# =============================================================================
# Catch-All Configuration
# =============================================================================

@mail_bp.route('/catch-all', methods=['GET', 'POST'])
@admin_required
def catch_all():
    """Configure catch-all alias for domain"""
    admin = get_current_admin()

    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)

        # Get all mailboxes for destination dropdown
        mailboxes_list, _ = Mailbox.get_all(conn, per_page=1000)

        # Check for existing catch-all
        catch_all_alias = f'@{MAIL_DOMAIN}'
        cursor.execute('SELECT * FROM mail_aliases WHERE alias = %s', (catch_all_alias,))
        current_catch_all = cursor.fetchone()
        cursor.close()

        if request.method == 'POST':
            action = request.form.get('action', 'set')

            if action == 'disable':
                if current_catch_all:
                    Alias.delete(conn, current_catch_all['id'])

                    log_admin_action(
                        admin.id, 'mail_disable_catchall', 'alias', current_catch_all['id'],
                        f'Disabled catch-all for {MAIL_DOMAIN}', request.remote_addr
                    )

                    flash('Catch-all disabled.', 'success')
                    current_catch_all = None

            elif action == 'set':
                destination_id = request.form.get('destination_id', type=int)

                if not destination_id:
                    flash('Destination mailbox is required.', 'error')
                else:
                    # Remove existing catch-all if any
                    if current_catch_all:
                        Alias.delete(conn, current_catch_all['id'])

                    # Create new catch-all
                    alias_id = Alias.create(conn, catch_all_alias, destination_id)

                    log_admin_action(
                        admin.id, 'mail_set_catchall', 'alias', alias_id,
                        f'Set catch-all for {MAIL_DOMAIN}', request.remote_addr
                    )

                    flash('Catch-all configured successfully.', 'success')

                    # Refresh catch-all info
                    cursor = conn.cursor(dictionary=True)
                    cursor.execute('SELECT * FROM mail_aliases WHERE alias = %s', (catch_all_alias,))
                    current_catch_all = cursor.fetchone()
                    cursor.close()

    finally:
        conn.close()

    return render_template(
        'admin/mail/catch_all.html',
        catch_all=current_catch_all,
        mailboxes=mailboxes_list,
        mail_domain=MAIL_DOMAIN
    )


# =============================================================================
# API Endpoints
# =============================================================================

@mail_bp.route('/api/stats')
@admin_required
def api_stats():
    """JSON API for mail statistics"""
    conn = get_db_connection()
    try:
        stats = Mailbox.get_stats(conn)

        cursor = conn.cursor(dictionary=True)
        cursor.execute('SELECT COUNT(*) as total FROM mail_aliases')
        stats['aliases'] = cursor.fetchone()['total']

        cursor.execute('SELECT COUNT(*) as total FROM mail_autoresponders WHERE is_active = 1')
        stats['autoresponders'] = cursor.fetchone()['total']
        cursor.close()

    finally:
        conn.close()

    return jsonify(stats)


@mail_bp.route('/api/usage/<int:mailbox_id>')
@admin_required
def api_usage(mailbox_id):
    """JSON API for mailbox usage statistics"""
    conn = get_db_connection()
    try:
        mailbox = Mailbox.get_by_id(conn, mailbox_id)
        if not mailbox:
            return jsonify({'error': 'Mailbox not found'}), 404

        disk_usage = get_maildir_size(mailbox['username'])
        quota_bytes = mailbox['quota_bytes'] or 0

        usage_data = {
            'mailbox_id': mailbox_id,
            'email': mailbox['email'],
            'disk_usage_bytes': disk_usage,
            'disk_usage_mb': round(disk_usage / (1024 * 1024), 2),
            'quota_bytes': quota_bytes,
            'quota_mb': round(quota_bytes / (1024 * 1024), 2) if quota_bytes else None,
            'usage_percent': round((disk_usage / quota_bytes) * 100, 1) if quota_bytes else None
        }

    finally:
        conn.close()

    return jsonify(usage_data)
