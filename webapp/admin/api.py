"""
Admin Panel API Endpoints
JSON endpoints for AJAX dashboard updates
"""

import os
from flask import jsonify, request, session

from . import admin_bp
from .routes import (
    admin_required, get_customer_stats, get_queue_stats,
    get_service_status, get_disk_usage, get_backup_status,
    get_billing_stats, get_customers_filtered, get_all_provisioning_jobs
)

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models import PortManager, Ticket, TicketCategory


@admin_bp.route('/api/stats')
@admin_required
def api_stats():
    """Dashboard statistics"""
    stats = get_customer_stats()
    port_usage = PortManager.get_port_usage()
    queue_stats = get_queue_stats()

    return jsonify({
        'customers': stats,
        'ports': port_usage,
        'queue': queue_stats
    })


@admin_bp.route('/api/customers')
@admin_required
def api_customers():
    """Paginated customer list"""
    search = request.args.get('search', '').strip()
    status = request.args.get('status', '')
    platform = request.args.get('platform', '')
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 20))

    customers, total = get_customers_filtered(
        search=search,
        status=status,
        platform=platform,
        page=page,
        per_page=per_page
    )

    # Convert datetime objects to strings
    for c in customers:
        if c.get('created_at'):
            c['created_at'] = c['created_at'].isoformat()

    return jsonify({
        'customers': customers,
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': (total + per_page - 1) // per_page
    })


@admin_bp.route('/api/queue')
@admin_required
def api_queue():
    """Queue status and recent jobs"""
    queue_stats = get_queue_stats()
    jobs = get_all_provisioning_jobs(limit=20)

    # Convert datetime objects to strings
    for job in jobs:
        for key in ['started_at', 'finished_at', 'created_at']:
            if job.get(key):
                job[key] = job[key].isoformat()

    return jsonify({
        'stats': queue_stats,
        'jobs': jobs
    })


@admin_bp.route('/api/system')
@admin_required
def api_system():
    """System health metrics"""
    services = get_service_status()
    port_usage = PortManager.get_port_usage()
    disk_usage = get_disk_usage()
    backup_status = get_backup_status()

    return jsonify({
        'services': services,
        'ports': port_usage,
        'disk': disk_usage,
        'backup': backup_status
    })


@admin_bp.route('/api/billing')
@admin_required
def api_billing():
    """Billing metrics"""
    billing_stats = get_billing_stats()

    return jsonify({
        'stats': billing_stats
    })


@admin_bp.route('/api/tickets')
@admin_required
def api_tickets():
    """Paginated ticket list with filters"""
    status = request.args.get('status', '')
    priority = request.args.get('priority', '')
    category_id = request.args.get('category', '')
    assigned = request.args.get('assigned', '')
    search = request.args.get('search', '').strip()
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 20))

    tickets, total = Ticket.get_all_filtered(
        status=status or None,
        priority=priority or None,
        category_id=int(category_id) if category_id else None,
        assigned_admin_id=assigned or None,
        search=search or None,
        page=page,
        per_page=per_page
    )

    # Convert datetime objects to strings
    for t in tickets:
        for key in ['created_at', 'updated_at', 'resolved_at', 'closed_at']:
            if t.get(key):
                t[key] = t[key].isoformat()

    return jsonify({
        'tickets': tickets,
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': (total + per_page - 1) // per_page
    })


@admin_bp.route('/api/tickets/stats')
@admin_required
def api_ticket_stats():
    """Ticket statistics for dashboard"""
    stats = Ticket.get_stats()

    return jsonify({
        'total': stats['total'] or 0,
        'open': stats['open_count'] or 0,
        'in_progress': stats['in_progress_count'] or 0,
        'waiting_customer': stats['waiting_count'] or 0,
        'urgent': stats['urgent_count'] or 0,
        'unassigned': stats['unassigned_count'] or 0
    })
