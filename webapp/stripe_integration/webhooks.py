"""
Stripe Webhook Handlers
"""

import stripe
import logging
from datetime import datetime
import sys

# Add provisioning module to path
sys.path.insert(0, '/opt/shophosting/provisioning')

from models import Customer, PricingPlan, Subscription, Invoice, WebhookEvent
from .config import get_stripe_config

logger = logging.getLogger(__name__)


def process_webhook(payload, sig_header):
    """
    Process incoming Stripe webhook with signature verification.

    Args:
        payload: Raw request body
        sig_header: Stripe-Signature header value

    Returns:
        Tuple of (success: bool, message: str)
    """
    config = get_stripe_config()
    webhook_secret = config.get('webhook_secret')

    if not webhook_secret:
        logger.error("Webhook secret not configured")
        return False, "Webhook secret not configured"

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, webhook_secret
        )
    except ValueError as e:
        logger.error(f"Invalid webhook payload: {e}")
        return False, "Invalid payload"
    except stripe.error.SignatureVerificationError as e:
        logger.error(f"Invalid webhook signature: {e}")
        return False, "Invalid signature"

    # Idempotency check - skip if already processed
    if WebhookEvent.exists(event.id):
        logger.info(f"Duplicate webhook event {event.id}, skipping")
        return True, "Already processed"

    # Record the event
    webhook_event = WebhookEvent(
        stripe_event_id=event.id,
        event_type=event.type,
        payload=event.to_dict()
    )
    webhook_event.save()

    # Route to appropriate handler
    handler = WEBHOOK_HANDLERS.get(event.type)
    if handler:
        try:
            handler(event.data.object)
            webhook_event.mark_processed()
            logger.info(f"Successfully processed webhook {event.type}: {event.id}")
            return True, "Processed"
        except Exception as e:
            error_msg = str(e)
            webhook_event.mark_error(error_msg)
            logger.error(f"Webhook handler error for {event.type}: {e}")
            return False, error_msg
    else:
        # No handler for this event type - that's OK
        webhook_event.mark_processed()
        logger.debug(f"No handler for event type {event.type}")
        return True, "No handler needed"


def handle_checkout_completed(session):
    """Handle successful checkout - create subscription and trigger provisioning"""
    logger.info(f"Processing checkout.session.completed: {session.get('id')}")

    customer_id = session.get('client_reference_id')
    stripe_customer_id = session.get('customer')
    stripe_subscription_id = session.get('subscription')

    if not customer_id:
        logger.error("No client_reference_id in checkout session")
        raise ValueError("Missing client_reference_id")

    # Get our customer record
    customer = Customer.get_by_id(int(customer_id))
    if not customer:
        logger.error(f"Customer {customer_id} not found")
        raise ValueError(f"Customer {customer_id} not found")

    # Get plan from metadata
    plan_id = session.get('metadata', {}).get('plan_id')
    plan = PricingPlan.get_by_id(int(plan_id)) if plan_id else None

    # Update customer with Stripe ID and plan
    customer.stripe_customer_id = stripe_customer_id
    customer.plan_id = plan.id if plan else None
    customer.status = 'pending'  # Ready for provisioning
    customer.save()

    # Retrieve subscription details from Stripe
    if stripe_subscription_id:
        try:
            stripe_sub = stripe.Subscription.retrieve(stripe_subscription_id)

            # Create subscription record
            subscription = Subscription(
                customer_id=customer.id,
                plan_id=plan.id if plan else None,
                stripe_subscription_id=stripe_subscription_id,
                stripe_customer_id=stripe_customer_id,
                status=stripe_sub.status,
                current_period_start=datetime.fromtimestamp(stripe_sub.current_period_start) if stripe_sub.current_period_start else None,
                current_period_end=datetime.fromtimestamp(stripe_sub.current_period_end) if stripe_sub.current_period_end else None
            )
            subscription.save()
            logger.info(f"Created subscription record for customer {customer.id}")
        except Exception as e:
            logger.error(f"Error creating subscription record: {e}")

    # Enqueue provisioning job
    try:
        from enqueue_provisioning import ProvisioningQueue
        queue = ProvisioningQueue()
        job_data = {
            'customer_id': customer.id,
            'domain': customer.domain,
            'platform': customer.platform,
            'email': customer.email,
            'site_title': customer.company_name,
            'web_port': customer.web_port
        }
        job = queue.enqueue_customer(job_data)
        logger.info(f"Provisioning job {job.id} enqueued for customer {customer.id}")
    except Exception as e:
        logger.error(f"Error enqueueing provisioning job: {e}")
        raise

    logger.info(f"Checkout completed for customer {customer.id}, provisioning started")


def handle_invoice_paid(invoice):
    """Handle paid invoice - update records"""
    logger.info(f"Processing invoice.paid: {invoice.get('id')}")

    stripe_customer_id = invoice.get('customer')
    stripe_subscription_id = invoice.get('subscription')
    stripe_invoice_id = invoice.get('id')

    # Find our customer
    customer = Customer.get_by_stripe_customer_id(stripe_customer_id)
    if not customer:
        logger.warning(f"Customer not found for Stripe customer {stripe_customer_id}")
        return

    # Find subscription
    subscription = None
    if stripe_subscription_id:
        subscription = Subscription.get_by_stripe_subscription_id(stripe_subscription_id)

    # Check if invoice already exists
    existing_invoice = Invoice.get_by_stripe_invoice_id(stripe_invoice_id)
    if existing_invoice:
        # Update existing invoice
        existing_invoice.status = 'paid'
        existing_invoice.amount_paid = invoice.get('amount_paid', 0)
        existing_invoice.paid_at = datetime.fromtimestamp(invoice.get('status_transitions', {}).get('paid_at', 0)) if invoice.get('status_transitions', {}).get('paid_at') else datetime.now()
        existing_invoice.invoice_pdf_url = invoice.get('invoice_pdf')
        existing_invoice.hosted_invoice_url = invoice.get('hosted_invoice_url')
        existing_invoice.save()
    else:
        # Create new invoice record
        new_invoice = Invoice(
            customer_id=customer.id,
            subscription_id=subscription.id if subscription else None,
            stripe_invoice_id=stripe_invoice_id,
            stripe_payment_intent_id=invoice.get('payment_intent'),
            amount_due=invoice.get('amount_due', 0),
            amount_paid=invoice.get('amount_paid', 0),
            currency=invoice.get('currency', 'usd'),
            status='paid',
            invoice_pdf_url=invoice.get('invoice_pdf'),
            hosted_invoice_url=invoice.get('hosted_invoice_url'),
            period_start=datetime.fromtimestamp(invoice.get('period_start')) if invoice.get('period_start') else None,
            period_end=datetime.fromtimestamp(invoice.get('period_end')) if invoice.get('period_end') else None,
            paid_at=datetime.now()
        )
        new_invoice.save()

    # Update subscription status if needed
    if subscription and subscription.status != 'active':
        subscription.status = 'active'
        subscription.save()

    logger.info(f"Invoice {stripe_invoice_id} marked as paid for customer {customer.id}")


def handle_payment_failed(invoice):
    """Handle failed payment - update status and notify"""
    logger.info(f"Processing invoice.payment_failed: {invoice.get('id')}")

    stripe_customer_id = invoice.get('customer')
    stripe_subscription_id = invoice.get('subscription')

    customer = Customer.get_by_stripe_customer_id(stripe_customer_id)
    if not customer:
        logger.warning(f"Customer not found for Stripe customer {stripe_customer_id}")
        return

    # Update subscription status
    if stripe_subscription_id:
        subscription = Subscription.get_by_stripe_subscription_id(stripe_subscription_id)
        if subscription:
            subscription.status = 'past_due'
            subscription.save()

    # TODO: Send payment failed email notification
    logger.warning(f"Payment failed for customer {customer.id}")


def handle_subscription_updated(subscription_data):
    """Handle subscription updates (plan changes, status changes)"""
    logger.info(f"Processing customer.subscription.updated: {subscription_data.get('id')}")

    stripe_subscription_id = subscription_data.get('id')
    subscription = Subscription.get_by_stripe_subscription_id(stripe_subscription_id)

    if not subscription:
        logger.warning(f"Subscription {stripe_subscription_id} not found in database")
        return

    # Update subscription status
    subscription.status = subscription_data.get('status', subscription.status)

    # Update period dates
    if subscription_data.get('current_period_start'):
        subscription.current_period_start = datetime.fromtimestamp(subscription_data['current_period_start'])
    if subscription_data.get('current_period_end'):
        subscription.current_period_end = datetime.fromtimestamp(subscription_data['current_period_end'])

    # Update cancel dates
    if subscription_data.get('cancel_at'):
        subscription.cancel_at = datetime.fromtimestamp(subscription_data['cancel_at'])
    if subscription_data.get('canceled_at'):
        subscription.canceled_at = datetime.fromtimestamp(subscription_data['canceled_at'])

    subscription.save()
    logger.info(f"Subscription {stripe_subscription_id} updated to status: {subscription.status}")


def handle_subscription_deleted(subscription_data):
    """Handle subscription cancellation"""
    logger.info(f"Processing customer.subscription.deleted: {subscription_data.get('id')}")

    stripe_subscription_id = subscription_data.get('id')
    subscription = Subscription.get_by_stripe_subscription_id(stripe_subscription_id)

    if not subscription:
        logger.warning(f"Subscription {stripe_subscription_id} not found in database")
        return

    # Mark as canceled
    subscription.status = 'canceled'
    subscription.canceled_at = datetime.now()
    subscription.save()

    # Optionally suspend the customer's service
    customer = Customer.get_by_id(subscription.customer_id)
    if customer:
        # Don't immediately suspend - give grace period or send notification
        # customer.status = 'suspended'
        # customer.save()
        logger.info(f"Subscription canceled for customer {customer.id}")

    # TODO: Send cancellation email notification


# Map of Stripe event types to handlers
WEBHOOK_HANDLERS = {
    'checkout.session.completed': handle_checkout_completed,
    'invoice.paid': handle_invoice_paid,
    'invoice.payment_failed': handle_payment_failed,
    'customer.subscription.updated': handle_subscription_updated,
    'customer.subscription.deleted': handle_subscription_deleted,
}
