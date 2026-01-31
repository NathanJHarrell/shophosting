#!/usr/bin/env python3
"""
Subscription Grace Period Worker

Monitors cancelled subscriptions and suspends customers after their grace period ends.
Does NOT delete data - only stops containers.

Run via cron or systemd timer:
    */15 * * * * /opt/shophosting/scripts/subscription_worker.py

Or as a service:
    python3 /opt/shophosting/scripts/subscription_worker.py --daemon
"""

import os
import sys
import argparse
import logging
import time
from datetime import datetime, timedelta

# Add webapp to path
sys.path.insert(0, '/opt/shophosting/webapp')

# Load environment
from dotenv import load_dotenv
load_dotenv('/opt/shophosting/.env')

from models import Customer, Subscription, get_db_connection
from services.container_service import ContainerService
from email_utils import send_suspension_notification

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/opt/shophosting/logs/subscription_worker.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Grace period in days after subscription ends
GRACE_PERIOD_DAYS = int(os.environ.get('SUBSCRIPTION_GRACE_PERIOD_DAYS', 7))

# Warning email days before suspension
WARNING_DAYS = [3, 1]  # Send warnings 3 days and 1 day before suspension


class SubscriptionWorker:
    """Handles subscription lifecycle and grace period enforcement"""

    def get_cancelled_subscriptions(self):
        """Get all cancelled subscriptions that may need action"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            # Get subscriptions that are:
            # - Status is 'canceled'
            # - Customer is still active (not yet suspended)
            cursor.execute("""
                SELECT s.*, c.id as customer_id, c.email, c.domain, c.status as customer_status
                FROM subscriptions s
                JOIN customers c ON s.customer_id = c.id
                WHERE s.status = 'canceled'
                  AND c.status = 'active'
                ORDER BY s.canceled_at ASC
            """)
            return cursor.fetchall()
        finally:
            cursor.close()
            conn.close()

    def get_past_due_subscriptions(self):
        """Get subscriptions that are past due (payment failed)"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("""
                SELECT s.*, c.id as customer_id, c.email, c.domain, c.status as customer_status
                FROM subscriptions s
                JOIN customers c ON s.customer_id = c.id
                WHERE s.status IN ('past_due', 'unpaid')
                  AND c.status = 'active'
                ORDER BY s.current_period_end ASC
            """)
            return cursor.fetchall()
        finally:
            cursor.close()
            conn.close()

    def calculate_suspension_date(self, subscription):
        """Calculate when a subscription should trigger suspension"""
        # Use the later of: canceled_at or current_period_end
        base_date = subscription.get('current_period_end') or subscription.get('canceled_at')

        if not base_date:
            # If no date available, use canceled_at or now
            base_date = subscription.get('canceled_at') or datetime.now()

        if isinstance(base_date, str):
            base_date = datetime.fromisoformat(base_date)

        return base_date + timedelta(days=GRACE_PERIOD_DAYS)

    def suspend_customer(self, customer_id, email, reason):
        """Suspend a customer and stop their containers (preserves data)"""
        logger.info(f"Suspending customer {customer_id} ({email}): {reason}")

        # Stop containers (does NOT delete data)
        success, msg = ContainerService.stop_containers(customer_id)
        if not success:
            logger.warning(f"Could not stop containers for {customer_id}: {msg}")

        # Update customer status
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                UPDATE customers
                SET status = 'suspended',
                    suspension_reason = %s,
                    suspended_at = NOW(),
                    auto_suspended = TRUE
                WHERE id = %s AND status = 'active'
            """, (reason, customer_id))

            if cursor.rowcount > 0:
                # Log the suspension
                cursor.execute("""
                    INSERT INTO customer_suspension_log
                    (customer_id, action, reason, auto_action)
                    VALUES (%s, 'suspended', %s, TRUE)
                """, (customer_id, reason))

            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error suspending customer {customer_id}: {e}")
            conn.rollback()
            return False
        finally:
            cursor.close()
            conn.close()

    def terminate_customer(self, customer_id, email, reason):
        """Terminate a customer - delete containers and files (for cancelled subscriptions)"""
        logger.info(f"Terminating customer {customer_id} ({email}): {reason}")

        # Delete containers and volumes
        success, msg = ContainerService.delete_containers(customer_id, remove_volumes=True)
        if not success:
            logger.warning(f"Could not delete containers for {customer_id}: {msg}")

        # Delete customer files
        file_success, file_msg = ContainerService.delete_customer_files(customer_id)
        if not file_success:
            logger.warning(f"Could not delete files for {customer_id}: {file_msg}")

        # Update customer status to terminated
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                UPDATE customers
                SET status = 'suspended',
                    suspension_reason = %s,
                    suspended_at = NOW(),
                    auto_suspended = TRUE
                WHERE id = %s AND status = 'active'
            """, (f"TERMINATED: {reason}", customer_id))

            if cursor.rowcount > 0:
                # Log the termination
                cursor.execute("""
                    INSERT INTO customer_suspension_log
                    (customer_id, action, reason, auto_action)
                    VALUES (%s, 'terminated', %s, TRUE)
                """, (customer_id, reason))

            conn.commit()
            logger.info(f"Customer {customer_id} terminated - containers and files deleted")
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error terminating customer {customer_id}: {e}")
            conn.rollback()
            return False
        finally:
            cursor.close()
            conn.close()

    def send_warning_email(self, customer_id, email, domain, days_until_suspension, reason, will_delete=False):
        """Send warning email before suspension/termination"""
        logger.info(f"Sending {days_until_suspension}-day warning to {email} (delete={will_delete})")

        try:
            if will_delete:
                message = (f"Warning: Your site and all data will be DELETED in {days_until_suspension} day(s) "
                          f"due to: {reason}. Please reactivate your subscription or download your data immediately.")
            else:
                message = (f"Warning: Your service will be suspended in {days_until_suspension} day(s) "
                          f"due to: {reason}. Please update your payment method or contact support to avoid interruption.")

            send_suspension_notification(
                to_email=email,
                domain=domain,
                reason=message
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send warning email to {email}: {e}")
            return False

    def check_warning_sent(self, customer_id, warning_type):
        """Check if a warning has already been sent"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT COUNT(*) FROM customer_suspension_log
                WHERE customer_id = %s
                  AND action = %s
                  AND created_at > DATE_SUB(NOW(), INTERVAL 7 DAY)
            """, (customer_id, f'warning_{warning_type}'))
            count = cursor.fetchone()[0]
            return count > 0
        finally:
            cursor.close()
            conn.close()

    def log_warning_sent(self, customer_id, warning_type, reason):
        """Log that a warning was sent"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO customer_suspension_log
                (customer_id, action, reason, auto_action)
                VALUES (%s, %s, %s, TRUE)
            """, (customer_id, f'warning_{warning_type}', reason))
            conn.commit()
        finally:
            cursor.close()
            conn.close()

    def process_cancelled_subscriptions(self):
        """Process all cancelled subscriptions"""
        subscriptions = self.get_cancelled_subscriptions()
        logger.info(f"Processing {len(subscriptions)} cancelled subscriptions")

        now = datetime.now()
        suspended_count = 0
        warning_count = 0

        for sub in subscriptions:
            customer_id = sub['customer_id']
            email = sub['email']
            domain = sub['domain']

            suspension_date = self.calculate_suspension_date(sub)
            days_until = (suspension_date - now).days

            reason = "Subscription cancelled"

            if days_until <= 0:
                # Grace period expired - TERMINATE (delete containers and files)
                if self.terminate_customer(customer_id, email, reason):
                    suspended_count += 1
                    # Send termination notification
                    try:
                        send_suspension_notification(
                            to_email=email,
                            domain=domain,
                            reason=f"Your subscription has been cancelled and the grace period has ended. "
                                   f"Your site and data have been deleted. Contact support if you need assistance."
                        )
                    except Exception as e:
                        logger.error(f"Failed to send termination notification: {e}")

            else:
                # Check if we should send a warning (will_delete=True for cancellations)
                for warning_day in WARNING_DAYS:
                    if days_until <= warning_day:
                        warning_type = f'{warning_day}day'
                        if not self.check_warning_sent(customer_id, warning_type):
                            if self.send_warning_email(customer_id, email, domain, days_until, reason, will_delete=True):
                                self.log_warning_sent(customer_id, warning_type, reason)
                                warning_count += 1
                        break

        logger.info(f"Cancelled subscriptions: {suspended_count} terminated, {warning_count} warnings sent")
        return suspended_count, warning_count

    def process_past_due_subscriptions(self):
        """Process past due subscriptions (payment failures)"""
        subscriptions = self.get_past_due_subscriptions()
        logger.info(f"Processing {len(subscriptions)} past due subscriptions")

        now = datetime.now()
        suspended_count = 0
        warning_count = 0

        for sub in subscriptions:
            customer_id = sub['customer_id']
            email = sub['email']
            domain = sub['domain']

            # For past due, use current_period_end as the base
            base_date = sub.get('current_period_end')
            if not base_date:
                continue

            if isinstance(base_date, str):
                base_date = datetime.fromisoformat(base_date)

            suspension_date = base_date + timedelta(days=GRACE_PERIOD_DAYS)
            days_until = (suspension_date - now).days

            reason = "Payment failed - subscription past due"

            if days_until <= 0:
                # Grace period expired - suspend
                if self.suspend_customer(customer_id, email, reason):
                    suspended_count += 1
                    try:
                        send_suspension_notification(
                            to_email=email,
                            domain=domain,
                            reason=f"Your payment has failed and the grace period has ended. "
                                   f"Your site has been suspended. Please update your payment method to reactivate."
                        )
                    except Exception as e:
                        logger.error(f"Failed to send suspension notification: {e}")

            else:
                # Check if we should send a warning
                for warning_day in WARNING_DAYS:
                    if days_until <= warning_day:
                        warning_type = f'pastdue_{warning_day}day'
                        if not self.check_warning_sent(customer_id, warning_type):
                            if self.send_warning_email(customer_id, email, domain, days_until, reason):
                                self.log_warning_sent(customer_id, warning_type, reason)
                                warning_count += 1
                        break

        logger.info(f"Past due subscriptions: {suspended_count} suspended, {warning_count} warnings sent")
        return suspended_count, warning_count

    def run_once(self):
        """Run one processing cycle"""
        logger.info("Starting subscription worker cycle")

        try:
            cancelled_suspended, cancelled_warnings = self.process_cancelled_subscriptions()
            pastdue_suspended, pastdue_warnings = self.process_past_due_subscriptions()

            total_suspended = cancelled_suspended + pastdue_suspended
            total_warnings = cancelled_warnings + pastdue_warnings

            logger.info(f"Cycle complete: {total_suspended} suspended, {total_warnings} warnings sent")
            return total_suspended, total_warnings

        except Exception as e:
            logger.error(f"Error in subscription worker cycle: {e}")
            raise

    def run_daemon(self, interval_minutes=15):
        """Run continuously as a daemon"""
        logger.info(f"Starting subscription worker daemon (interval: {interval_minutes} minutes)")

        while True:
            try:
                self.run_once()
            except Exception as e:
                logger.error(f"Daemon cycle error: {e}")

            time.sleep(interval_minutes * 60)


def main():
    parser = argparse.ArgumentParser(description='Subscription grace period worker')
    parser.add_argument('--daemon', action='store_true', help='Run continuously as a daemon')
    parser.add_argument('--interval', type=int, default=15, help='Daemon check interval in minutes')

    args = parser.parse_args()

    worker = SubscriptionWorker()

    if args.daemon:
        worker.run_daemon(interval_minutes=args.interval)
    else:
        worker.run_once()


if __name__ == '__main__':
    main()
