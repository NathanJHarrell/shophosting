"""
Leads Module - Lead Generation Funnel
Provides speed test tools and migration preview requests for customer acquisition
"""

from flask import Blueprint

leads_bp = Blueprint('leads', __name__, template_folder='../templates/leads')

from . import models
