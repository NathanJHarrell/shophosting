"""
Status Page Blueprint
Public status page showing system health
"""

from flask import Blueprint

status_bp = Blueprint('status', __name__, template_folder='../templates/status')

from . import routes
from .models import StatusIncident, StatusIncidentUpdate, StatusMaintenance, StatusOverride
