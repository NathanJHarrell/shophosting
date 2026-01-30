"""
Status Page Routes
Public routes for viewing system status and incident history
"""

from datetime import datetime
from flask import render_template, jsonify

from . import status_bp
from .health_checks import get_all_statuses, get_status_display, get_overall_message
from .models import StatusIncident, StatusMaintenance


@status_bp.route('/')
def index():
    """Main status page"""
    # Get all statuses using get_all_statuses()
    statuses = get_all_statuses()

    # Get active incidents with their updates
    active_incidents = StatusIncident.get_active()
    for incident in active_incidents:
        incident.updates = incident.get_updates()

    # Get recent incidents (7 days)
    recent_incidents = StatusIncident.get_recent(days=7)

    # Get upcoming maintenance (7 days)
    upcoming_maintenance = StatusMaintenance.get_upcoming(days=7)

    return render_template(
        'status/index.html',
        statuses=statuses,
        active_incidents=active_incidents,
        recent_incidents=recent_incidents,
        upcoming_maintenance=upcoming_maintenance,
        get_status_display=get_status_display,
        get_overall_message=get_overall_message
    )


@status_bp.route('/api/status')
def api_status():
    """JSON API for current status"""
    return jsonify(get_all_statuses())


@status_bp.route('/api/incidents')
def api_incidents():
    """JSON API for incidents"""
    active_incidents = StatusIncident.get_active()
    recent_incidents = StatusIncident.get_recent(days=7)

    def incident_to_dict(incident):
        return {
            'id': incident.id,
            'server_id': incident.server_id,
            'title': incident.title,
            'status': incident.status,
            'severity': incident.severity,
            'is_auto_detected': incident.is_auto_detected,
            'started_at': incident.started_at.isoformat() if incident.started_at else None,
            'resolved_at': incident.resolved_at.isoformat() if incident.resolved_at else None,
            'created_at': incident.created_at.isoformat() if incident.created_at else None,
            'updated_at': incident.updated_at.isoformat() if incident.updated_at else None
        }

    return jsonify({
        'active': [incident_to_dict(i) for i in active_incidents],
        'recent': [incident_to_dict(i) for i in recent_incidents]
    })


@status_bp.route('/api/incidents/<int:incident_id>')
def api_incident_detail(incident_id):
    """JSON API for single incident with updates"""
    incident = StatusIncident.get_by_id(incident_id)

    if not incident:
        return jsonify({'error': 'Incident not found'}), 404

    updates = incident.get_updates()

    def update_to_dict(update):
        return {
            'id': update.id,
            'status': update.status,
            'message': update.message,
            'created_by': update.created_by,
            'admin_name': update.admin_name,
            'created_at': update.created_at.isoformat() if update.created_at else None
        }

    return jsonify({
        'id': incident.id,
        'server_id': incident.server_id,
        'title': incident.title,
        'status': incident.status,
        'severity': incident.severity,
        'is_auto_detected': incident.is_auto_detected,
        'started_at': incident.started_at.isoformat() if incident.started_at else None,
        'resolved_at': incident.resolved_at.isoformat() if incident.resolved_at else None,
        'created_at': incident.created_at.isoformat() if incident.created_at else None,
        'updated_at': incident.updated_at.isoformat() if incident.updated_at else None,
        'updates': [update_to_dict(u) for u in updates]
    })
