"""
Stripe Customer Portal Management
"""

import stripe
import logging
from flask import url_for

logger = logging.getLogger(__name__)


def create_portal_session(stripe_customer_id, return_url=None):
    """
    Create a Stripe Customer Portal session.

    The portal allows customers to:
    - Update payment methods
    - View invoice history
    - Cancel/modify subscriptions (if enabled)

    Args:
        stripe_customer_id: Stripe customer ID (cus_xxxx)
        return_url: URL to return to after portal session

    Returns:
        Stripe Portal Session object with URL
    """
    if return_url is None:
        return_url = url_for('billing', _external=True)

    try:
        session = stripe.billing_portal.Session.create(
            customer=stripe_customer_id,
            return_url=return_url,
        )

        logger.info(f"Created portal session for customer {stripe_customer_id}")
        return session

    except stripe.error.StripeError as e:
        logger.error(f"Error creating portal session: {e}")
        raise


def get_customer_subscription(stripe_customer_id):
    """
    Get the active subscription for a Stripe customer.

    Args:
        stripe_customer_id: Stripe customer ID

    Returns:
        Stripe Subscription object or None
    """
    try:
        subscriptions = stripe.Subscription.list(
            customer=stripe_customer_id,
            status='active',
            limit=1
        )

        if subscriptions.data:
            return subscriptions.data[0]
        return None

    except stripe.error.StripeError as e:
        logger.error(f"Error fetching subscription: {e}")
        return None


def get_customer_invoices(stripe_customer_id, limit=10):
    """
    Get recent invoices for a Stripe customer.

    Args:
        stripe_customer_id: Stripe customer ID
        limit: Maximum number of invoices to return

    Returns:
        List of Stripe Invoice objects
    """
    try:
        invoices = stripe.Invoice.list(
            customer=stripe_customer_id,
            limit=limit
        )
        return invoices.data

    except stripe.error.StripeError as e:
        logger.error(f"Error fetching invoices: {e}")
        return []
