"""
Admin Panel - Lead Management Routes
Routes for managing leads from the speed test funnel
"""

import json
import logging
from functools import wraps

from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify

from .models import AdminUser, log_admin_action
from models import get_db_connection

# Import leads models
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from leads.models import SiteScan, MigrationPreviewRequest, SpeedBattle
from leads.battle_scorer import get_round_breakdown, get_score_tier

logger = logging.getLogger(__name__)

# Create blueprint
leads_admin_bp = Blueprint('leads_admin', __name__, url_prefix='/leads')


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


def acquisition_or_admin_required(f):
    """Require acquisition, admin, or super_admin role for leads management access"""
    @wraps(f)
    def decorated(*args, **kwargs):
        role = session.get('admin_user_role')
        if role not in ['acquisition', 'admin', 'super_admin']:
            flash('This action requires acquisition or admin privileges.', 'error')
            return redirect(url_for('admin.dashboard'))
        return f(*args, **kwargs)
    return decorated


def get_current_admin():
    """Get current logged in admin user"""
    admin_id = session.get('admin_user_id')
    if admin_id:
        return AdminUser.get_by_id(admin_id)
    return None


# =============================================================================
# Helper Functions
# =============================================================================

def get_leads_filtered(search=None, status_filter=None, has_email=None, page=1, per_page=20):
    """
    Get filtered list of leads (site scans with email).

    Args:
        search: Search term for email or URL
        status_filter: Filter by migration request status
        has_email: Filter by whether email is captured (True/False/None)
        page: Page number
        per_page: Items per page

    Returns:
        tuple: (list of SiteScan objects with migration info, total count)
    """
    conn = get_db_connection(read_only=True)
    cursor = conn.cursor(dictionary=True)

    try:
        # Build WHERE clause
        conditions = []
        params = []

        if search:
            conditions.append("(ss.email LIKE %s OR ss.url LIKE %s)")
            search_term = f"%{search}%"
            params.extend([search_term, search_term])

        if has_email is True:
            conditions.append("ss.email IS NOT NULL")
        elif has_email is False:
            conditions.append("ss.email IS NULL")

        if status_filter:
            conditions.append("mpr.status = %s")
            params.append(status_filter)

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        # Count query
        count_query = f"""
            SELECT COUNT(DISTINCT ss.id) as total
            FROM site_scans ss
            LEFT JOIN migration_preview_requests mpr ON ss.id = mpr.site_scan_id
            WHERE {where_clause}
        """
        cursor.execute(count_query, params)
        total = cursor.fetchone()['total']

        # Data query with pagination
        offset = (page - 1) * per_page
        data_query = f"""
            SELECT ss.*,
                   mpr.id as migration_request_id,
                   mpr.status as migration_status,
                   mpr.store_platform,
                   mpr.monthly_revenue as reported_revenue,
                   mpr.current_host,
                   mpr.assigned_admin_id
            FROM site_scans ss
            LEFT JOIN migration_preview_requests mpr ON ss.id = mpr.site_scan_id
            WHERE {where_clause}
            ORDER BY ss.created_at DESC
            LIMIT %s OFFSET %s
        """
        params.extend([per_page, offset])
        cursor.execute(data_query, params)
        rows = cursor.fetchall()

        return rows, total

    finally:
        cursor.close()
        conn.close()


def get_lead_detail(scan_id):
    """
    Get detailed lead information including scan results and migration request.

    Args:
        scan_id: The site scan ID

    Returns:
        dict: Combined lead data or None if not found
    """
    scan = SiteScan.get_by_id(scan_id)
    if not scan:
        return None

    migration_request = MigrationPreviewRequest.get_by_scan_id(scan_id)

    # Parse JSON data
    pagespeed_data = None
    custom_probe_data = None

    if scan.pagespeed_data:
        try:
            pagespeed_data = json.loads(scan.pagespeed_data)
        except (json.JSONDecodeError, TypeError):
            pass

    if scan.custom_probe_data:
        try:
            custom_probe_data = json.loads(scan.custom_probe_data)
        except (json.JSONDecodeError, TypeError):
            pass

    return {
        'scan': scan,
        'migration_request': migration_request,
        'pagespeed_data': pagespeed_data,
        'custom_probe_data': custom_probe_data,
    }


# =============================================================================
# Lead Dashboard Routes
# =============================================================================

@leads_admin_bp.route('/')
@admin_required
@acquisition_or_admin_required
def dashboard():
    """Lead management dashboard"""
    admin = get_current_admin()

    # Get filter parameters
    search = request.args.get('search', '').strip()
    status_filter = request.args.get('status', '')
    email_filter = request.args.get('email_filter', '')
    page = int(request.args.get('page', 1))
    per_page = 20

    # Convert email filter
    has_email = None
    if email_filter == 'with_email':
        has_email = True
    elif email_filter == 'no_email':
        has_email = False

    # Get filtered leads
    leads, total = get_leads_filtered(
        search=search,
        status_filter=status_filter if status_filter else None,
        has_email=has_email,
        page=page,
        per_page=per_page
    )

    total_pages = (total + per_page - 1) // per_page

    # Get stats
    scan_stats = SiteScan.get_stats()
    migration_stats = MigrationPreviewRequest.get_stats()
    battle_stats = SpeedBattle.get_stats()
    recent_battles = SpeedBattle.get_recent(limit=10)

    return render_template('admin/leads/dashboard.html',
                           admin=admin,
                           leads=leads,
                           search=search,
                           status_filter=status_filter,
                           email_filter=email_filter,
                           page=page,
                           total_pages=total_pages,
                           total=total,
                           scan_stats=scan_stats,
                           migration_stats=migration_stats,
                           battle_stats=battle_stats,
                           recent_battles=recent_battles)


@leads_admin_bp.route('/analytics')
@admin_required
@acquisition_or_admin_required
def analytics():
    """Lead analytics and conversion stats"""
    admin = get_current_admin()

    scan_stats = SiteScan.get_stats()
    migration_stats = MigrationPreviewRequest.get_stats()

    # Get daily scan data for chart
    daily_data = scan_stats.get('scans_per_day', [])

    return render_template('admin/leads/analytics.html',
                           admin=admin,
                           scan_stats=scan_stats,
                           migration_stats=migration_stats,
                           daily_data=daily_data)


# =============================================================================
# Lead Detail Routes
# =============================================================================

@leads_admin_bp.route('/<int:scan_id>')
@admin_required
@acquisition_or_admin_required
def detail(scan_id):
    """Lead detail view"""
    admin = get_current_admin()

    lead_data = get_lead_detail(scan_id)
    if not lead_data:
        flash('Lead not found.', 'error')
        return redirect(url_for('leads_admin.dashboard'))

    # Get assigned admin info if applicable
    assigned_admin = None
    if lead_data['migration_request'] and lead_data['migration_request'].assigned_admin_id:
        assigned_admin = AdminUser.get_by_id(lead_data['migration_request'].assigned_admin_id)

    return render_template('admin/leads/detail.html',
                           admin=admin,
                           scan=lead_data['scan'],
                           migration_request=lead_data['migration_request'],
                           pagespeed_data=lead_data['pagespeed_data'],
                           custom_probe_data=lead_data['custom_probe_data'],
                           assigned_admin=assigned_admin)


@leads_admin_bp.route('/<int:scan_id>/notes', methods=['POST'])
@admin_required
@acquisition_or_admin_required
def add_note(scan_id):
    """Add a note to a migration preview request"""
    admin = get_current_admin()

    migration_request = MigrationPreviewRequest.get_by_scan_id(scan_id)
    if not migration_request:
        if request.is_json:
            return jsonify({'error': 'Migration request not found'}), 404
        flash('Migration request not found for this scan.', 'error')
        return redirect(url_for('leads_admin.detail', scan_id=scan_id))

    note = request.form.get('note', '').strip() if not request.is_json else request.get_json().get('note', '').strip()
    if not note:
        if request.is_json:
            return jsonify({'error': 'Note is required'}), 400
        flash('Note cannot be empty.', 'error')
        return redirect(url_for('leads_admin.detail', scan_id=scan_id))

    # Add admin name to note
    note_with_author = f"{admin.full_name}: {note}"
    migration_request.add_note(note_with_author)

    log_admin_action(admin.id, 'lead_note_added',
                     details=f"Added note to migration request {migration_request.id}",
                     ip_address=request.remote_addr)

    if request.is_json:
        return jsonify({'success': True})

    flash('Note added successfully.', 'success')
    return redirect(url_for('leads_admin.detail', scan_id=scan_id))


@leads_admin_bp.route('/<int:scan_id>/status', methods=['POST'])
@admin_required
@acquisition_or_admin_required
def update_status(scan_id):
    """Update migration preview request status"""
    admin = get_current_admin()

    migration_request = MigrationPreviewRequest.get_by_scan_id(scan_id)
    if not migration_request:
        if request.is_json:
            return jsonify({'error': 'Migration request not found'}), 404
        flash('Migration request not found for this scan.', 'error')
        return redirect(url_for('leads_admin.detail', scan_id=scan_id))

    new_status = request.form.get('status', '').strip() if not request.is_json else request.get_json().get('status', '').strip()
    if new_status not in MigrationPreviewRequest.STATUSES:
        if request.is_json:
            return jsonify({'error': f'Invalid status. Must be one of: {MigrationPreviewRequest.STATUSES}'}), 400
        flash('Invalid status.', 'error')
        return redirect(url_for('leads_admin.detail', scan_id=scan_id))

    old_status = migration_request.status
    migration_request.update_status(new_status)

    log_admin_action(admin.id, 'lead_status_updated',
                     details=f"Updated migration request {migration_request.id} status from {old_status} to {new_status}",
                     ip_address=request.remote_addr)

    if request.is_json:
        return jsonify({'success': True, 'new_status': new_status})

    flash(f'Status updated to {new_status}.', 'success')
    return redirect(url_for('leads_admin.detail', scan_id=scan_id))


# =============================================================================
# Migration Preview Routes
# =============================================================================

@leads_admin_bp.route('/previews')
@admin_required
@acquisition_or_admin_required
def previews():
    """Migration preview request queue"""
    admin = get_current_admin()

    status_filter = request.args.get('status', 'pending')

    if status_filter == 'all':
        requests = MigrationPreviewRequest.get_all(limit=100)
    else:
        requests = MigrationPreviewRequest.get_all(status_filter=status_filter, limit=100)

    # Enrich with scan data
    enriched_requests = []
    for req in requests:
        scan = SiteScan.get_by_id(req.site_scan_id)
        assigned_admin = None
        if req.assigned_admin_id:
            assigned_admin = AdminUser.get_by_id(req.assigned_admin_id)

        enriched_requests.append({
            'request': req,
            'scan': scan,
            'assigned_admin': assigned_admin,
        })

    migration_stats = MigrationPreviewRequest.get_stats()

    return render_template('admin/leads/previews.html',
                           admin=admin,
                           requests=enriched_requests,
                           status_filter=status_filter,
                           migration_stats=migration_stats)


@leads_admin_bp.route('/previews/<int:request_id>/assign', methods=['POST'])
@admin_required
@acquisition_or_admin_required
def assign_preview(request_id):
    """Assign an admin to handle a migration preview request"""
    admin = get_current_admin()

    migration_request = MigrationPreviewRequest.get_by_id(request_id)
    if not migration_request:
        if request.is_json:
            return jsonify({'error': 'Migration request not found'}), 404
        flash('Migration request not found.', 'error')
        return redirect(url_for('leads_admin.previews'))

    # Assign to current admin or specified admin
    data = request.get_json() if request.is_json else request.form
    assign_to = data.get('admin_id')

    if assign_to:
        assign_to = int(assign_to)
    else:
        # Self-assign
        assign_to = admin.id

    migration_request.assign_admin(assign_to)

    # Update status to contacted if still pending
    if migration_request.status == 'pending':
        migration_request.update_status('contacted')

    assigned_admin = AdminUser.get_by_id(assign_to)
    log_admin_action(admin.id, 'lead_assigned',
                     details=f"Assigned migration request {request_id} to {assigned_admin.full_name}",
                     ip_address=request.remote_addr)

    if request.is_json:
        return jsonify({'success': True, 'assigned_to': assigned_admin.full_name})

    flash(f'Assigned to {assigned_admin.full_name}.', 'success')
    return redirect(url_for('leads_admin.previews'))


# =============================================================================
# Speed Battle Management Routes
# =============================================================================

def get_battles_filtered(status=None, winner=None, has_email=None, page=1, per_page=25):
    """
    Get filtered list of speed battles with pagination.

    Args:
        status: Filter by battle status (pending, scanning, completed, failed)
        winner: Filter by winner (challenger, opponent, tie)
        has_email: Filter by whether email is captured (True/False/None)
        page: Page number
        per_page: Items per page

    Returns:
        tuple: (list of SpeedBattle objects, total count)
    """
    conn = get_db_connection(read_only=True)
    cursor = conn.cursor(dictionary=True)

    try:
        # Build WHERE clause
        conditions = []
        params = []

        if status:
            conditions.append("status = %s")
            params.append(status)

        if winner:
            conditions.append("winner = %s")
            params.append(winner)

        if has_email is True:
            conditions.append("email IS NOT NULL")
        elif has_email is False:
            conditions.append("email IS NULL")

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        # Count query
        count_query = f"SELECT COUNT(*) as total FROM speed_battles WHERE {where_clause}"
        cursor.execute(count_query, params)
        total = cursor.fetchone()['total']

        # Data query with pagination
        offset = (page - 1) * per_page
        data_query = f"""
            SELECT * FROM speed_battles
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
        """
        params.extend([per_page, offset])
        cursor.execute(data_query, params)
        rows = cursor.fetchall()

        battles = [SpeedBattle(**row) for row in rows]
        return battles, total

    finally:
        cursor.close()
        conn.close()


def get_spawned_battles(battle_id):
    """
    Get battles that were spawned from a specific battle (via referral).

    Args:
        battle_id: The parent battle ID

    Returns:
        list: List of SpeedBattle objects with referrer_battle_id = battle_id
    """
    conn = get_db_connection(read_only=True)
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT * FROM speed_battles
            WHERE referrer_battle_id = %s
            ORDER BY created_at DESC
        """, (battle_id,))
        rows = cursor.fetchall()
        return [SpeedBattle(**row) for row in rows]

    finally:
        cursor.close()
        conn.close()


@leads_admin_bp.route('/battles')
@admin_required
@acquisition_or_admin_required
def battles():
    """Speed battle management dashboard"""
    admin = get_current_admin()

    # Get filter parameters
    status_filter = request.args.get('status', '')
    winner_filter = request.args.get('winner', '')
    email_filter = request.args.get('has_email', '')
    page = int(request.args.get('page', 1))
    per_page = 25

    # Convert filters
    status = status_filter if status_filter else None
    winner = winner_filter if winner_filter else None
    has_email = None
    if email_filter == 'yes':
        has_email = True
    elif email_filter == 'no':
        has_email = False

    # Get filtered battles
    battles_list, total = get_battles_filtered(
        status=status,
        winner=winner,
        has_email=has_email,
        page=page,
        per_page=per_page
    )

    total_pages = (total + per_page - 1) // per_page

    # Get battle stats
    battle_stats = SpeedBattle.get_stats()

    return render_template('admin/leads/battles.html',
                           admin=admin,
                           battles=battles_list,
                           status_filter=status_filter,
                           winner_filter=winner_filter,
                           email_filter=email_filter,
                           page=page,
                           total_pages=total_pages,
                           total=total,
                           battle_stats=battle_stats)


@leads_admin_bp.route('/battles/<int:battle_id>')
@admin_required
@acquisition_or_admin_required
def battle_detail(battle_id):
    """Speed battle detail view"""
    admin = get_current_admin()

    battle = SpeedBattle.get_by_id(battle_id)
    if not battle:
        flash('Battle not found.', 'error')
        return redirect(url_for('leads_admin.battles'))

    # Get related scans if available
    challenger_scan = None
    opponent_scan = None
    round_breakdown = None

    if battle.challenger_scan_id:
        challenger_scan = SiteScan.get_by_id(battle.challenger_scan_id)
    if battle.opponent_scan_id:
        opponent_scan = SiteScan.get_by_id(battle.opponent_scan_id)

    # Get round breakdown if both scans are available
    if challenger_scan and opponent_scan:
        try:
            # Build scan data dicts for the scorer
            challenger_data = {
                'performance_score': challenger_scan.performance_score,
                'pagespeed_data': json.loads(challenger_scan.pagespeed_data) if challenger_scan.pagespeed_data else None,
                'ttfb_ms': challenger_scan.ttfb_ms,
                'url': challenger_scan.url
            }
            opponent_data = {
                'performance_score': opponent_scan.performance_score,
                'pagespeed_data': json.loads(opponent_scan.pagespeed_data) if opponent_scan.pagespeed_data else None,
                'ttfb_ms': opponent_scan.ttfb_ms,
                'url': opponent_scan.url
            }
            round_breakdown = get_round_breakdown(challenger_data, opponent_data)
        except (json.JSONDecodeError, TypeError):
            round_breakdown = None

    # Get score tiers
    challenger_tier = get_score_tier(battle.challenger_score) if battle.challenger_score else None
    opponent_tier = get_score_tier(battle.opponent_score) if battle.opponent_score else None

    # Get spawned battles (battles that came from this one via sharing)
    spawned_battles = get_spawned_battles(battle_id)

    # Get referrer battle if exists
    referrer_battle = None
    if battle.referrer_battle_id:
        referrer_battle = SpeedBattle.get_by_id(battle.referrer_battle_id)

    return render_template('admin/leads/battle_detail.html',
                           admin=admin,
                           battle=battle,
                           challenger_scan=challenger_scan,
                           opponent_scan=opponent_scan,
                           round_breakdown=round_breakdown,
                           challenger_tier=challenger_tier,
                           opponent_tier=opponent_tier,
                           spawned_battles=spawned_battles,
                           referrer_battle=referrer_battle)
