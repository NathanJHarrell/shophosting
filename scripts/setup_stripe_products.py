#!/usr/bin/env python3
"""
Stripe Products and Prices Setup Script

This script creates Stripe products and prices for all pricing plans,
then updates the database with the Stripe IDs.

Usage:
    1. Set STRIPE_SECRET_KEY environment variable
    2. Run: python /opt/shophosting/scripts/setup_stripe_products.py

Prerequisites:
    - Run the database migration first
    - Have Stripe secret key available
"""

import os
import sys
import stripe

# Add webapp to path for models
sys.path.insert(0, '/opt/shophosting/webapp')

from dotenv import load_dotenv
import mysql.connector

# Load environment variables
load_dotenv('/opt/shophosting/.env')

# Initialize Stripe
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')

if not stripe.api_key:
    print("ERROR: STRIPE_SECRET_KEY not set!")
    print("Please set your Stripe secret key in /opt/shophosting/.env")
    sys.exit(1)

# Database connection
def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv('DB_HOST', 'localhost'),
        user=os.getenv('DB_USER', 'shophosting_app'),
        password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME', 'shophosting_db')
    )


def create_stripe_product_and_price(plan_data):
    """Create a Stripe product and price for a plan"""

    # Create the product
    product = stripe.Product.create(
        name=f"{plan_data['name']} ({plan_data['platform'].title()})",
        description=f"ShopHosting.io {plan_data['name']} plan for {plan_data['platform'].title()}",
        metadata={
            'plan_slug': plan_data['slug'],
            'platform': plan_data['platform'],
            'tier_type': plan_data['tier_type'],
            'store_limit': str(plan_data['store_limit'])
        }
    )

    # Create the price (in cents)
    price_cents = int(plan_data['price_monthly'] * 100)
    price = stripe.Price.create(
        product=product.id,
        unit_amount=price_cents,
        currency='usd',
        recurring={'interval': 'month'},
        metadata={
            'plan_slug': plan_data['slug']
        }
    )

    return product.id, price.id


def main():
    print("=" * 60)
    print("ShopHosting.io - Stripe Products Setup")
    print("=" * 60)

    # Connect to database
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Get all plans that don't have Stripe IDs yet
    cursor.execute("""
        SELECT id, name, slug, platform, tier_type, price_monthly, store_limit
        FROM pricing_plans
        WHERE stripe_product_id IS NULL OR stripe_price_id IS NULL
    """)
    plans = cursor.fetchall()

    if not plans:
        print("\nAll plans already have Stripe products configured!")
        print("If you want to recreate them, clear the stripe_product_id and stripe_price_id columns first.")
        return

    print(f"\nFound {len(plans)} plans to create in Stripe:")
    for plan in plans:
        print(f"  - {plan['name']} ({plan['platform']}) - ${plan['price_monthly']}/mo")

    print("\nCreating Stripe products and prices...")

    for plan in plans:
        try:
            product_id, price_id = create_stripe_product_and_price(plan)

            # Update database with Stripe IDs
            cursor.execute("""
                UPDATE pricing_plans
                SET stripe_product_id = %s, stripe_price_id = %s
                WHERE id = %s
            """, (product_id, price_id, plan['id']))

            print(f"  ✓ {plan['name']} ({plan['platform']})")
            print(f"      Product: {product_id}")
            print(f"      Price: {price_id}")

        except stripe.error.StripeError as e:
            print(f"  ✗ {plan['name']} ({plan['platform']}) - Error: {e}")

    conn.commit()
    cursor.close()
    conn.close()

    print("\n" + "=" * 60)
    print("Setup complete!")
    print("=" * 60)
    print("\nNext steps:")
    print("1. Configure your webhook endpoint in Stripe Dashboard:")
    print("   URL: https://yourdomain.com/webhook/stripe")
    print("   Events: checkout.session.completed, invoice.paid,")
    print("           invoice.payment_failed, customer.subscription.updated,")
    print("           customer.subscription.deleted")
    print("")
    print("2. Copy the webhook signing secret to your .env file as STRIPE_WEBHOOK_SECRET")
    print("")
    print("3. (Optional) Configure Stripe Customer Portal settings in Dashboard")
    print("")


if __name__ == '__main__':
    main()
