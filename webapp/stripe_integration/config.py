"""
Stripe Configuration
"""

import os
import stripe
import logging

logger = logging.getLogger(__name__)

# Global stripe configuration
_stripe_config = {
    'secret_key': None,
    'publishable_key': None,
    'webhook_secret': None,
    'initialized': False
}


def init_stripe():
    """Initialize Stripe with API keys from environment"""
    _stripe_config['secret_key'] = os.getenv('STRIPE_SECRET_KEY')
    _stripe_config['publishable_key'] = os.getenv('STRIPE_PUBLISHABLE_KEY')
    _stripe_config['webhook_secret'] = os.getenv('STRIPE_WEBHOOK_SECRET')

    if not _stripe_config['secret_key']:
        logger.warning("STRIPE_SECRET_KEY not set - Stripe integration disabled")
        return False

    stripe.api_key = _stripe_config['secret_key']
    _stripe_config['initialized'] = True

    logger.info("Stripe initialized successfully")
    return True


def get_stripe_config():
    """Get Stripe configuration"""
    return _stripe_config


def is_stripe_configured():
    """Check if Stripe is properly configured"""
    return _stripe_config['initialized'] and _stripe_config['secret_key'] is not None
