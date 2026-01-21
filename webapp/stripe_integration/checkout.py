"""
Stripe Checkout Session Management
"""

import stripe
import logging
from flask import url_for

logger = logging.getLogger(__name__)


def create_checkout_session(customer, plan, success_url=None, cancel_url=None):
    """
    Create a Stripe Checkout Session for a new subscription.

    Args:
        customer: Customer model instance
        plan: PricingPlan model instance
        success_url: Optional custom success URL
        cancel_url: Optional custom cancel URL

    Returns:
        Stripe Checkout Session object
    """
    if not plan.stripe_price_id:
        raise ValueError(f"Plan {plan.slug} does not have a Stripe price ID configured")

    # Default URLs
    if success_url is None:
        success_url = url_for('checkout_success', _external=True) + '?session_id={CHECKOUT_SESSION_ID}'
    if cancel_url is None:
        cancel_url = url_for('checkout_cancel', _external=True)

    try:
        session = stripe.checkout.Session.create(
            mode='subscription',
            payment_method_types=['card'],
            line_items=[{
                'price': plan.stripe_price_id,
                'quantity': 1,
            }],
            # Link to our customer record for webhook processing
            client_reference_id=str(customer.id),

            # Pre-fill customer email
            customer_email=customer.email,

            # Allow promo codes
            allow_promotion_codes=True,

            # Redirect URLs
            success_url=success_url,
            cancel_url=cancel_url,

            # Metadata for tracking
            metadata={
                'customer_id': str(customer.id),
                'plan_slug': plan.slug,
                'plan_id': str(plan.id),
                'domain': customer.domain,
            },

            # Subscription data - metadata gets attached to the subscription
            subscription_data={
                'metadata': {
                    'customer_id': str(customer.id),
                    'plan_id': str(plan.id),
                    'domain': customer.domain,
                }
            }
        )

        logger.info(f"Created checkout session {session.id} for customer {customer.id}")
        return session

    except stripe.error.StripeError as e:
        logger.error(f"Stripe error creating checkout session: {e}")
        raise


def get_checkout_session(session_id):
    """
    Retrieve a Checkout Session by ID.

    Args:
        session_id: Stripe Checkout Session ID

    Returns:
        Stripe Checkout Session object or None
    """
    try:
        session = stripe.checkout.Session.retrieve(
            session_id,
            expand=['subscription', 'customer']
        )
        return session
    except stripe.error.StripeError as e:
        logger.error(f"Error retrieving checkout session {session_id}: {e}")
        return None
