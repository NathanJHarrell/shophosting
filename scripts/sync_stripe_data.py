#!/usr/bin/env python3
"""
Stripe Data Sync Script

Syncs existing Stripe subscriptions and invoices to the local database.
Run this once after setting up the billing system to populate historical data,
or periodically to catch any missed webhook events.

Usage:
    python3 sync_stripe_data.py [--dry-run] [--subscriptions] [--invoices] [--all]
"""

import os
import sys
import argparse
import logging
from datetime import datetime

# Add webapp to path
sys.path.insert(0, '/opt/shophosting/webapp')

# Load environment
from dotenv import load_dotenv
load_dotenv('/opt/shophosting/.env')

import stripe
from models import Customer, Subscription, Invoice, PricingPlan, get_db_connection

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def init_stripe():
    """Initialize Stripe API"""
    api_key = os.environ.get('STRIPE_SECRET_KEY')
    if not api_key:
        raise ValueError("STRIPE_SECRET_KEY not set in environment")
    stripe.api_key = api_key
    logger.info("Stripe API initialized")


def get_customers_with_stripe():
    """Get all customers that have a Stripe customer ID"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT id, email, stripe_customer_id, plan_id
        FROM customers
        WHERE stripe_customer_id IS NOT NULL
    """)
    customers = cursor.fetchall()
    cursor.close()
    conn.close()
    return customers


def sync_subscriptions(dry_run=False):
    """Sync all subscriptions from Stripe to local database"""
    logger.info("Starting subscription sync...")
    customers = get_customers_with_stripe()

    synced = 0
    created = 0
    updated = 0
    errors = 0

    for customer in customers:
        try:
            # Get subscriptions from Stripe
            stripe_subs = stripe.Subscription.list(
                customer=customer['stripe_customer_id'],
                limit=100
            )

            for stripe_sub in stripe_subs.data:
                synced += 1

                # Check if subscription exists locally
                existing = Subscription.get_by_stripe_subscription_id(stripe_sub.id)

                # Get period dates safely
                period_start = None
                period_end = None
                cancel_at = None
                canceled_at = None

                try:
                    if hasattr(stripe_sub, 'current_period_start') and stripe_sub.current_period_start:
                        period_start = datetime.fromtimestamp(stripe_sub.current_period_start)
                    if hasattr(stripe_sub, 'current_period_end') and stripe_sub.current_period_end:
                        period_end = datetime.fromtimestamp(stripe_sub.current_period_end)
                    if hasattr(stripe_sub, 'cancel_at') and stripe_sub.cancel_at:
                        cancel_at = datetime.fromtimestamp(stripe_sub.cancel_at)
                    if hasattr(stripe_sub, 'canceled_at') and stripe_sub.canceled_at:
                        canceled_at = datetime.fromtimestamp(stripe_sub.canceled_at)
                except Exception as e:
                    logger.warning(f"Error parsing dates for {stripe_sub.id}: {e}")

                if existing:
                    if dry_run:
                        logger.info(f"[DRY RUN] Would update subscription {stripe_sub.id}")
                    else:
                        # Update existing
                        existing.status = stripe_sub.status
                        existing.current_period_start = period_start
                        existing.current_period_end = period_end
                        existing.cancel_at = cancel_at
                        existing.canceled_at = canceled_at
                        existing.save()
                        logger.info(f"Updated subscription {stripe_sub.id} for {customer['email']}")
                    updated += 1
                else:
                    if dry_run:
                        logger.info(f"[DRY RUN] Would create subscription {stripe_sub.id}")
                    else:
                        # Create new subscription
                        conn = get_db_connection()
                        cursor = conn.cursor()
                        cursor.execute("""
                            INSERT INTO subscriptions
                            (customer_id, plan_id, stripe_subscription_id, stripe_customer_id,
                             status, current_period_start, current_period_end, cancel_at, canceled_at,
                             created_at, updated_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                        """, (
                            customer['id'],
                            customer['plan_id'],
                            stripe_sub.id,
                            customer['stripe_customer_id'],
                            stripe_sub.status,
                            period_start,
                            period_end,
                            cancel_at,
                            canceled_at
                        ))
                        conn.commit()
                        cursor.close()
                        conn.close()
                        logger.info(f"Created subscription {stripe_sub.id} for {customer['email']}")
                    created += 1

        except Exception as e:
            logger.error(f"Error syncing subscriptions for {customer['email']}: {e}")
            errors += 1

    logger.info(f"Subscription sync complete: {synced} processed, {created} created, {updated} updated, {errors} errors")
    return synced, created, updated, errors


def sync_invoices(dry_run=False):
    """Sync all invoices from Stripe to local database"""
    logger.info("Starting invoice sync...")
    customers = get_customers_with_stripe()

    synced = 0
    created = 0
    updated = 0
    errors = 0

    for customer in customers:
        try:
            # Get invoices from Stripe
            stripe_invoices = stripe.Invoice.list(
                customer=customer['stripe_customer_id'],
                limit=100
            )

            for stripe_inv in stripe_invoices.data:
                synced += 1

                # Check if invoice exists locally
                existing = Invoice.get_by_stripe_invoice_id(stripe_inv.id)

                # Get subscription ID if exists
                subscription_id = None
                stripe_sub_id = stripe_inv.get('subscription') if hasattr(stripe_inv, 'get') else getattr(stripe_inv, 'subscription', None)
                if stripe_sub_id:
                    sub = Subscription.get_by_stripe_subscription_id(stripe_sub_id)
                    if sub:
                        subscription_id = sub.id

                # Helper to safely get stripe object attributes
                def safe_get(obj, key, default=None):
                    try:
                        return obj.get(key, default) if hasattr(obj, 'get') else getattr(obj, key, default)
                    except (KeyError, AttributeError):
                        return default

                # Parse dates safely
                period_start = None
                period_end = None
                paid_at = None

                try:
                    ps = safe_get(stripe_inv, 'period_start')
                    if ps:
                        period_start = datetime.fromtimestamp(ps)
                    pe = safe_get(stripe_inv, 'period_end')
                    if pe:
                        period_end = datetime.fromtimestamp(pe)
                    status_transitions = safe_get(stripe_inv, 'status_transitions')
                    if status_transitions:
                        pa = safe_get(status_transitions, 'paid_at')
                        if pa:
                            paid_at = datetime.fromtimestamp(pa)
                except Exception as e:
                    logger.warning(f"Error parsing dates for invoice {stripe_inv.id}: {e}")

                # Extract values safely
                inv_status = safe_get(stripe_inv, 'status', 'draft')
                inv_amount_due = safe_get(stripe_inv, 'amount_due', 0) or 0
                inv_amount_paid = safe_get(stripe_inv, 'amount_paid', 0) or 0
                inv_currency = safe_get(stripe_inv, 'currency', 'usd') or 'usd'
                inv_pdf_url = safe_get(stripe_inv, 'invoice_pdf')
                inv_hosted_url = safe_get(stripe_inv, 'hosted_invoice_url')
                inv_payment_intent = safe_get(stripe_inv, 'payment_intent')

                if existing:
                    if dry_run:
                        logger.info(f"[DRY RUN] Would update invoice {stripe_inv.id}")
                    else:
                        # Update existing
                        existing.status = inv_status
                        existing.amount_due = inv_amount_due
                        existing.amount_paid = inv_amount_paid
                        existing.invoice_pdf_url = inv_pdf_url
                        existing.hosted_invoice_url = inv_hosted_url
                        if paid_at:
                            existing.paid_at = paid_at
                        existing.save()
                        logger.debug(f"Updated invoice {stripe_inv.id}")
                    updated += 1
                else:
                    if dry_run:
                        logger.info(f"[DRY RUN] Would create invoice {stripe_inv.id}")
                    else:
                        # Create new invoice
                        conn = get_db_connection()
                        cursor = conn.cursor()
                        cursor.execute("""
                            INSERT INTO invoices
                            (customer_id, subscription_id, stripe_invoice_id, stripe_payment_intent_id,
                             amount_due, amount_paid, currency, status, invoice_pdf_url, hosted_invoice_url,
                             period_start, period_end, paid_at, created_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                        """, (
                            customer['id'],
                            subscription_id,
                            stripe_inv.id,
                            inv_payment_intent,
                            inv_amount_due,
                            inv_amount_paid,
                            inv_currency,
                            inv_status,
                            inv_pdf_url,
                            inv_hosted_url,
                            period_start,
                            period_end,
                            paid_at
                        ))
                        conn.commit()
                        cursor.close()
                        conn.close()
                        logger.info(f"Created invoice {stripe_inv.id} for {customer['email']}")
                    created += 1

        except Exception as e:
            logger.error(f"Error syncing invoices for {customer['email']}: {e}")
            errors += 1

    logger.info(f"Invoice sync complete: {synced} processed, {created} created, {updated} updated, {errors} errors")
    return synced, created, updated, errors


def main():
    parser = argparse.ArgumentParser(description='Sync Stripe data to local database')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be synced without making changes')
    parser.add_argument('--subscriptions', action='store_true', help='Sync only subscriptions')
    parser.add_argument('--invoices', action='store_true', help='Sync only invoices')
    parser.add_argument('--all', action='store_true', help='Sync everything (default)')

    args = parser.parse_args()

    # Default to --all if no specific flag
    if not args.subscriptions and not args.invoices:
        args.all = True

    try:
        init_stripe()

        if args.dry_run:
            logger.info("=== DRY RUN MODE - No changes will be made ===")

        results = {}

        if args.subscriptions or args.all:
            results['subscriptions'] = sync_subscriptions(dry_run=args.dry_run)

        if args.invoices or args.all:
            results['invoices'] = sync_invoices(dry_run=args.dry_run)

        # Summary
        logger.info("=" * 50)
        logger.info("SYNC SUMMARY")
        logger.info("=" * 50)
        for key, (synced, created, updated, errors) in results.items():
            logger.info(f"{key.upper()}: {synced} processed, {created} created, {updated} updated, {errors} errors")

        # Return non-zero if any errors
        total_errors = sum(r[3] for r in results.values())
        sys.exit(1 if total_errors > 0 else 0)

    except Exception as e:
        logger.error(f"Sync failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
