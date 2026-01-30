# webapp/cloudflare/__init__.py
"""Cloudflare DNS Management Blueprint"""

from flask import Blueprint

cloudflare_bp = Blueprint('cloudflare', __name__, url_prefix='/dashboard/cloudflare')

# Export API components for use by other modules
from .api import (
    CloudflareAPIError,
    CloudflareAPI,
    get_oauth_authorize_url,
    exchange_code_for_tokens,
    refresh_access_token
)

# Import routes after blueprint is defined to avoid circular imports
# Note: routes module will be created in Task 4
try:
    from . import routes
except ImportError:
    pass  # Routes not yet created

__all__ = [
    'cloudflare_bp',
    'CloudflareAPIError',
    'CloudflareAPI',
    'get_oauth_authorize_url',
    'exchange_code_for_tokens',
    'refresh_access_token'
]
