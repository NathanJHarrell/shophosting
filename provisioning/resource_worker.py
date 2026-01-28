"""
ShopHosting.io Resource Worker - Collects usage metrics and sends alerts
"""

import os
import sys
import subprocess
import logging
from pathlib import Path
from datetime import datetime, date
import time

sys.path.insert(0, '/opt/shophosting/webapp')

from models import Customer, PricingPlan, ResourceUsage, ResourceAlert, get_db_connection
from email_utils import send_resource_alert

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/opt/shophosting/logs/resource_worker.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class ResourceWorker:
    """Collects resource usage metrics and sends threshold alerts"""

    def __init__(self):
        self.customers_base = Path(os.getenv('CUSTOMERS_BASE_PATH', '/var/customers'))
        self.nginx_log_base = Path('/var/log/nginx')

    def collect_disk_usage(self, customer):
        """Collect disk usage for a customer using du or repquota"""
        customer_path = self.customers_base / f"customer-{customer.id}"

        if not customer_path.exists():
            logger.warning(f"Customer path not found: {customer_path}")
            return 0

        try:
            # Try repquota first if project quota is set
            if customer.quota_project_id:
                result = subprocess.run(
                    ['sudo', 'repquota', '-P', '-O', 'csv', '/'],
                    capture_output=True, text=True, timeout=30
                )
                if result.returncode == 0:
                    for line in result.stdout.split('\n'):
                        if line.startswith(f"#{customer.quota_project_id},"):
                            parts = line.split(',')
                            if len(parts) >= 3:
                                # repquota reports in KB
                                return int(parts[2]) * 1024

            # Fall back to du
            result = subprocess.run(
                ['du', '-sb', str(customer_path)],
                capture_output=True, text=True, timeout=60
            )
            if result.returncode == 0:
                return int(result.stdout.split()[0])

        except Exception as e:
            logger.error(f"Error collecting disk usage for customer {customer.id}: {e}")

        return 0

    def collect_bandwidth_usage(self, customer):
        """Collect bandwidth usage from Nginx access log"""
        log_path = self.nginx_log_base / f"customer-{customer.id}-access.log"

        if not log_path.exists():
            return 0

        try:
            # Sum bytes_sent from Nginx combined log format (field 10, 0-indexed 9)
            # Using awk for efficiency with large files
            result = subprocess.run(
                f"awk '{{sum += $10}} END {{print sum+0}}' {log_path}",
                shell=True, capture_output=True, text=True, timeout=60
            )
            if result.returncode == 0 and result.stdout.strip():
                return int(float(result.stdout.strip()))

        except Exception as e:
            logger.error(f"Error collecting bandwidth for customer {customer.id}: {e}")

        return 0

    def check_thresholds(self, customer, disk_bytes, bandwidth_bytes):
        """Check usage against limits and send alerts if needed"""
        plan = PricingPlan.get_by_id(customer.plan_id) if customer.plan_id else None
        if not plan:
            return

        disk_limit = plan.disk_limit_gb * 1024 * 1024 * 1024
        bandwidth_limit = plan.bandwidth_limit_gb * 1024 * 1024 * 1024

        # Get monthly bandwidth total
        monthly_bandwidth = ResourceUsage.get_monthly_bandwidth(customer.id) + bandwidth_bytes

        # Check disk thresholds
        if disk_limit > 0:
            disk_percent = (disk_bytes / disk_limit) * 100

            if disk_percent >= 90:
                if not ResourceAlert.was_recently_sent(customer.id, 'disk_critical'):
                    self._send_alert(customer, 'disk', 'critical', disk_bytes, disk_limit, disk_percent)
            elif disk_percent >= 80:
                if not ResourceAlert.was_recently_sent(customer.id, 'disk_warning'):
                    self._send_alert(customer, 'disk', 'warning', disk_bytes, disk_limit, disk_percent)

        # Check bandwidth thresholds
        if bandwidth_limit > 0:
            bw_percent = (monthly_bandwidth / bandwidth_limit) * 100

            if bw_percent >= 90:
                if not ResourceAlert.was_recently_sent(customer.id, 'bandwidth_critical'):
                    self._send_alert(customer, 'bandwidth', 'critical', monthly_bandwidth, bandwidth_limit, bw_percent)
            elif bw_percent >= 80:
                if not ResourceAlert.was_recently_sent(customer.id, 'bandwidth_warning'):
                    self._send_alert(customer, 'bandwidth', 'warning', monthly_bandwidth, bandwidth_limit, bw_percent)

    def _send_alert(self, customer, resource_type, alert_type, used_bytes, limit_bytes, percent):
        """Send alert and record it"""
        used_gb = used_bytes / (1024 * 1024 * 1024)
        limit_gb = limit_bytes / (1024 * 1024 * 1024)

        logger.info(f"Sending {alert_type} alert for {resource_type} to customer {customer.id} ({percent:.1f}%)")

        # Send email
        send_resource_alert(customer, alert_type, resource_type, used_gb, limit_gb, percent)

        # Record alert
        alert = ResourceAlert(
            customer_id=customer.id,
            alert_type=f"{resource_type}_{alert_type}",
            threshold_percent=int(percent),
            current_usage_bytes=used_bytes,
            limit_bytes=limit_bytes
        )
        alert.save()

    def run_collection_cycle(self):
        """Run one collection cycle for all active customers"""
        logger.info("Starting resource collection cycle")

        customers = Customer.get_by_status('active')
        today = date.today()

        for customer in customers:
            try:
                disk_bytes = self.collect_disk_usage(customer)
                bandwidth_bytes = self.collect_bandwidth_usage(customer)

                # Save daily usage
                usage = ResourceUsage(
                    customer_id=customer.id,
                    date=today,
                    disk_used_bytes=disk_bytes,
                    bandwidth_used_bytes=bandwidth_bytes
                )
                usage.save()

                # Check thresholds
                self.check_thresholds(customer, disk_bytes, bandwidth_bytes)

                logger.debug(f"Customer {customer.id}: disk={disk_bytes}, bandwidth={bandwidth_bytes}")

            except Exception as e:
                logger.error(f"Error processing customer {customer.id}: {e}")

        logger.info(f"Completed collection cycle for {len(customers)} customers")

    def run(self, interval=3600):
        """Run the worker continuously"""
        logger.info(f"Resource worker starting (interval: {interval}s)")

        while True:
            try:
                self.run_collection_cycle()
            except Exception as e:
                logger.error(f"Collection cycle failed: {e}")

            time.sleep(interval)


if __name__ == '__main__':
    worker = ResourceWorker()
    worker.run()
