"""
Stripe Pricing Sync Module
Handles two-way synchronization between ShopHosting pricing plans and Stripe prices.
"""

import os
import sys
import logging
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import get_db_connection, PricingPlan
from stripe_integration.config import stripe

logger = logging.getLogger(__name__)


def sync_price_to_stripe(plan_id, create_new=False):
    """
    Push pricing plan to Stripe.
    
    Args:
        plan_id: The pricing plan ID to sync
        create_new: If True, archives old price and creates new one. If False, updates existing.
    
    Returns:
        dict: Result with success status and message
    """
    try:
        plan = PricingPlan.get_by_id(plan_id)
        if not plan:
            return {'success': False, 'message': 'Plan not found'}
        
        amount_cents = int(plan.price_monthly * 100)
        
        if create_new:
            return _create_new_stripe_price(plan, amount_cents)
        else:
            return _update_existing_stripe_price(plan, amount_cents)
            
    except Exception as e:
        logger.error(f"Error syncing price to Stripe: {e}")
        return {'success': False, 'message': str(e)}


def _create_new_stripe_price(plan, amount_cents):
    """Create a new Stripe price for a plan, archiving the old one."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        if plan.stripe_price_id:
            archive_stripe_price(plan.stripe_price_id)
        
        if not plan.stripe_product_id:
            product = stripe.Product.create(
                name=plan.name,
                description=f"{plan.platform.capitalize()} hosting plan - {plan.tier_type} tier",
                metadata={'plan_id': plan.id, 'platform': plan.platform}
            )
            plan.stripe_product_id = product.id
        
        price = stripe.Price.create(
            product=plan.stripe_product_id,
            unit_amount=amount_cents,
            currency='usd',
            recurring={'interval': 'month'},
            metadata={'plan_id': plan.id}
        )
        
        cursor.execute("""
            UPDATE pricing_plans 
            SET stripe_product_id = %s, stripe_price_id = %s, updated_at = NOW()
            WHERE id = %s
        """, (plan.stripe_product_id, price.id, plan.id))
        conn.commit()
        cursor.close()
        conn.close()
        
        logger.info(f"Created new Stripe price {price.id} for plan {plan.id}")
        return {
            'success': True, 
            'message': f'Created new price: {price.id}',
            'price_id': price.id,
            'old_price_id': plan.stripe_price_id
        }
        
    except Exception as e:
        logger.error(f"Error creating new Stripe price: {e}")
        return {'success': False, 'message': str(e)}


def _update_existing_stripe_price(plan, amount_cents):
    """Update an existing Stripe price."""
    try:
        if not plan.stripe_price_id:
            return _create_new_stripe_price(plan, amount_cents)
        
        stripe.Price.modify(
            plan.stripe_price_id,
            unit_amount=amount_cents,
            metadata={'updated_at': datetime.utcnow().isoformat()}
        )
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE pricing_plans SET price_monthly = %s, updated_at = NOW() WHERE id = %s
        """, (plan.price_monthly / 100, plan.id))
        conn.commit()
        cursor.close()
        conn.close()
        
        logger.info(f"Updated Stripe price {plan.stripe_price_id} for plan {plan.id}")
        return {
            'success': True,
            'message': f'Updated price: {plan.stripe_price_id}'
        }
        
    except Exception as e:
        logger.error(f"Error updating Stripe price: {e}")
        return {'success': False, 'message': str(e)}


def archive_stripe_price(price_id):
    """Mark a Stripe price as inactive."""
    try:
        if price_id:
            stripe.Price.modify(price_id, active=False)
            logger.info(f"Archived Stripe price {price_id}")
        return True
    except Exception as e:
        logger.error(f"Error archiving Stripe price {price_id}: {e}")
        return False


def sync_price_from_stripe(price_id):
    """
    Pull pricing from Stripe webhook event.
    
    Args:
        price_id: The Stripe price ID
    
    Returns:
        dict: Result with success status
    """
    try:
        stripe_price = stripe.Price.retrieve(price_id)
        
        plan_id = stripe_price.metadata.get('plan_id')
        if not plan_id:
            logger.warning(f"No plan_id in Stripe price metadata for {price_id}")
            return {'success': False, 'message': 'No plan_id in metadata'}
        
        plan = PricingPlan.get_by_id(int(plan_id))
        if not plan:
            return {'success': False, 'message': 'Plan not found'}
        
        new_price = stripe_price.unit_amount / 100
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE pricing_plans 
            SET price_monthly = %s, stripe_price_id = %s, updated_at = NOW()
            WHERE id = %s
        """, (new_price, price_id, plan.id))
        conn.commit()
        cursor.close()
        conn.close()
        
        logger.info(f"Synced Stripe price {price_id} to plan {plan.id}")
        return {'success': True, 'message': 'Price synced from Stripe'}
        
    except Exception as e:
        logger.error(f"Error syncing price from Stripe: {e}")
        return {'success': False, 'message': str(e)}


def get_stripe_price_dialog_options(plan_id):
    """
    Get pricing options for dialog display.
    
    Args:
        plan_id: The pricing plan ID
    
    Returns:
        dict: Options for the sync dialog
    """
    try:
        plan = PricingPlan.get_by_id(plan_id)
        if not plan:
            return {'success': False, 'message': 'Plan not found'}
        
        current_stripe_price = None
        if plan.stripe_price_id:
            try:
                stripe_price = stripe.Price.retrieve(plan.stripe_price_id)
                current_stripe_price = {
                    'id': stripe_price.id,
                    'amount': stripe_price.unit_amount / 100,
                    'active': stripe_price.active
                }
            except Exception:
                current_stripe_price = {'id': plan.stripe_price_id, 'amount': None, 'active': False}
        
        local_price = float(plan.price_monthly)
        
        price_changed = current_stripe_price and current_stripe_price.get('amount') != local_price
        price_status = 'changed' if price_changed else ('missing' if not current_stripe_price else 'current')
        
        return {
            'success': True,
            'plan_id': plan.id,
            'plan_name': plan.name,
            'local_price': local_price,
            'stripe_price': current_stripe_price,
            'price_status': price_status,
            'has_stripe_product': bool(plan.stripe_product_id)
        }
        
    except Exception as e:
        logger.error(f"Error getting Stripe price options: {e}")
        return {'success': False, 'message': str(e)}


def get_all_pricing_sync_status():
    """Get sync status for all pricing plans."""
    try:
        plans = PricingPlan.get_all_active()
        status_list = []
        
        for plan in plans:
            options = get_stripe_price_dialog_options(plan.id)
            if options['success']:
                status_list.append({
                    'plan_id': plan.id,
                    'plan_name': plan.name,
                    'platform': plan.platform,
                    'local_price': options['local_price'],
                    'stripe_price': options.get('stripe_price'),
                    'price_status': options['price_status'],
                    'stripe_product_id': plan.stripe_product_id,
                    'stripe_price_id': plan.stripe_price_id
                })
        
        return {'success': True, 'plans': status_list}
        
    except Exception as e:
        logger.error(f"Error getting pricing sync status: {e}")
        return {'success': False, 'message': str(e)}
