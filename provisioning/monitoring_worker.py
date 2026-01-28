#!/usr/bin/env python3
"""
ShopHosting.io Monitoring Worker
Performs health checks on all active customer sites
"""

import os
import sys
import time
import logging
import requests
import subprocess
import json
from datetime import datetime, timedelta

# Add webapp to path for model imports
sys.path.insert(0, '/opt/shophosting/webapp')

from dotenv import load_dotenv
load_dotenv('/opt/shophosting/.env')

from models import (
    Customer, CustomerMonitoringStatus, MonitoringCheck, MonitoringAlert
)

# Configuration - can be overridden via environment variables
CHECK_INTERVAL = int(os.getenv('MONITORING_CHECK_INTERVAL', '60'))  # seconds between cycles
HTTP_TIMEOUT = int(os.getenv('MONITORING_HTTP_TIMEOUT', '10'))  # HTTP request timeout
ALERT_THRESHOLD = int(os.getenv('MONITORING_ALERT_THRESHOLD', '3'))  # failures before alert
ALERT_COOLDOWN = int(os.getenv('MONITORING_ALERT_COOLDOWN', '300'))  # seconds between alerts
RESOURCE_WARNING_CPU = float(os.getenv('MONITORING_CPU_WARNING', '80'))  # % CPU threshold
RESOURCE_WARNING_MEMORY = float(os.getenv('MONITORING_MEMORY_WARNING', '85'))  # % memory threshold
CLEANUP_INTERVAL = int(os.getenv('MONITORING_CLEANUP_INTERVAL', '3600'))  # cleanup every hour

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('monitoring_worker')


class MonitoringWorker:
    """Main monitoring worker class"""

    def __init__(self):
        self.last_cleanup = datetime.now()

    def run(self):
        """Main loop - runs continuously"""
        logger.info("Monitoring worker started")
        logger.info(f"Configuration: interval={CHECK_INTERVAL}s, threshold={ALERT_THRESHOLD}, "
                   f"cooldown={ALERT_COOLDOWN}s, cpu_warn={RESOURCE_WARNING_CPU}%, "
                   f"mem_warn={RESOURCE_WARNING_MEMORY}%")

        while True:
            try:
                self.run_check_cycle()

                # Periodic cleanup of old data
                if (datetime.now() - self.last_cleanup).total_seconds() > CLEANUP_INTERVAL:
                    deleted = MonitoringCheck.cleanup_old_checks(hours=48)
                    if deleted > 0:
                        logger.info(f"Cleaned up {deleted} old monitoring checks")
                    self.last_cleanup = datetime.now()

            except Exception as e:
                logger.error(f"Check cycle error: {e}", exc_info=True)

            time.sleep(CHECK_INTERVAL)

    def run_check_cycle(self):
        """Run checks for all active customers"""
        customers = Customer.get_by_status('active')
        logger.info(f"Running checks for {len(customers)} active customers")

        for customer in customers:
            try:
                self.check_customer(customer)
            except Exception as e:
                logger.error(f"Error checking {customer.domain}: {e}", exc_info=True)

    def check_customer(self, customer):
        """Run all checks for a single customer"""
        status = CustomerMonitoringStatus.get_or_create(customer.id)

        # HTTP check
        http_ok, http_time = self.check_http(customer)
        http_status = 'up' if http_ok else 'down'

        status.update_http_status(http_status, http_time)
        MonitoringCheck(
            customer_id=customer.id,
            check_type='http',
            status=http_status,
            response_time_ms=http_time
        ).save()

        # Container + resource check
        container_ok, cpu, mem_mb, disk_mb = self.check_container(customer)
        container_status = 'up' if container_ok else 'down'

        # Check for degraded (high resource usage)
        if container_ok:
            if cpu is not None and cpu > RESOURCE_WARNING_CPU:
                container_status = 'degraded'
                logger.warning(f"{customer.domain}: High CPU usage ({cpu}%)")

            # Calculate memory percentage if we have the limit
            plan = customer.plan_id
            if plan and mem_mb:
                # Try to get memory limit from plan
                try:
                    from models import PricingPlan
                    pricing_plan = PricingPlan.get_by_id(plan)
                    if pricing_plan and pricing_plan.memory_limit:
                        # Parse memory limit (e.g., "1g", "512m")
                        limit_str = pricing_plan.memory_limit.lower()
                        if limit_str.endswith('g'):
                            limit_mb = float(limit_str[:-1]) * 1024
                        elif limit_str.endswith('m'):
                            limit_mb = float(limit_str[:-1])
                        else:
                            limit_mb = float(limit_str)

                        mem_percent = (mem_mb / limit_mb) * 100
                        if mem_percent > RESOURCE_WARNING_MEMORY:
                            container_status = 'degraded'
                            logger.warning(f"{customer.domain}: High memory usage ({mem_percent:.1f}%)")
                except Exception as e:
                    logger.debug(f"Could not check memory percentage: {e}")

        status.update_container_status(container_status, cpu, mem_mb, disk_mb)
        MonitoringCheck(
            customer_id=customer.id,
            check_type='container',
            status=container_status,
            details={'cpu': cpu, 'memory_mb': mem_mb, 'disk_mb': disk_mb}
        ).save()

        # Update uptime calculation
        status.calculate_uptime_24h()

        # Handle alerting
        self.process_alerts(customer, status, http_ok, container_ok)

    def check_http(self, customer):
        """
        HTTP health check.
        Returns: (success: bool, response_time_ms: int or None)
        """
        url = f"https://{customer.domain}/"

        try:
            start = time.time()
            resp = requests.get(
                url,
                timeout=HTTP_TIMEOUT,
                allow_redirects=True,
                headers={'User-Agent': 'ShopHosting-Monitor/1.0'}
            )
            elapsed_ms = int((time.time() - start) * 1000)

            # Consider 2xx and 3xx as success, 5xx as failure
            # 4xx might be auth issues, still means server is responding
            is_ok = resp.status_code < 500

            if not is_ok:
                logger.warning(f"{customer.domain}: HTTP {resp.status_code}")

            return is_ok, elapsed_ms

        except requests.exceptions.Timeout:
            logger.warning(f"{customer.domain}: HTTP timeout")
            return False, None
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"{customer.domain}: Connection error - {e}")
            return False, None
        except Exception as e:
            logger.error(f"{customer.domain}: HTTP check error - {e}")
            return False, None

    def check_container(self, customer):
        """
        Container health + resource check.
        Returns: (running: bool, cpu_percent: float, memory_mb: int, disk_mb: int)
        """
        container_name = f"customer-{customer.id}-web"

        try:
            # Check if container is running
            result = subprocess.run(
                ['docker', 'inspect', container_name, '--format', '{{.State.Running}}'],
                capture_output=True, text=True, timeout=5
            )

            if result.returncode != 0:
                logger.debug(f"{customer.domain}: Container not found")
                return False, None, None, None

            if 'true' not in result.stdout.lower():
                logger.warning(f"{customer.domain}: Container not running")
                return False, None, None, None

            # Get resource stats
            cpu, mem_mb, disk_mb = None, None, None

            stats_result = subprocess.run(
                ['docker', 'stats', container_name, '--no-stream', '--format',
                 '{{.CPUPerc}},{{.MemUsage}}'],
                capture_output=True, text=True, timeout=10
            )

            if stats_result.returncode == 0 and stats_result.stdout.strip():
                parts = stats_result.stdout.strip().split(',')
                if len(parts) >= 2:
                    # Parse CPU percentage
                    try:
                        cpu_str = parts[0].replace('%', '').strip()
                        cpu = float(cpu_str)
                    except ValueError:
                        pass

                    # Parse memory usage (format: "123MiB / 1GiB")
                    try:
                        mem_str = parts[1].split('/')[0].strip()
                        if 'GiB' in mem_str:
                            mem_mb = int(float(mem_str.replace('GiB', '').strip()) * 1024)
                        elif 'MiB' in mem_str:
                            mem_mb = int(float(mem_str.replace('MiB', '').strip()))
                        elif 'KiB' in mem_str:
                            mem_mb = int(float(mem_str.replace('KiB', '').strip()) / 1024)
                    except (ValueError, IndexError):
                        pass

            return True, cpu, mem_mb, disk_mb

        except subprocess.TimeoutExpired:
            logger.warning(f"{customer.domain}: Docker command timeout")
            return False, None, None, None
        except Exception as e:
            logger.error(f"{customer.domain}: Container check error - {e}")
            return False, None, None, None

    def process_alerts(self, customer, status, http_ok, container_ok):
        """Handle alert logic based on check results"""
        is_down = not http_ok or not container_ok

        if is_down:
            status.increment_failures()

            if status.should_alert(threshold=ALERT_THRESHOLD, cooldown_seconds=ALERT_COOLDOWN):
                # Determine what's down
                issues = []
                if not http_ok:
                    issues.append("HTTP")
                if not container_ok:
                    issues.append("Container")

                alert = MonitoringAlert(
                    customer_id=customer.id,
                    alert_type='down',
                    message=f"{customer.domain} is DOWN ({', '.join(issues)})",
                    details={'http': http_ok, 'container': container_ok}
                )
                alert.save()

                logger.error(f"ALERT: {alert.message}")
                self.send_alert_email(customer, alert)
                status.mark_alert_sent()
        else:
            # Check if recovering from failure
            if status.consecutive_failures >= ALERT_THRESHOLD:
                alert = MonitoringAlert(
                    customer_id=customer.id,
                    alert_type='recovered',
                    message=f"{customer.domain} has RECOVERED",
                    details={'previous_failures': status.consecutive_failures}
                )
                alert.save()

                logger.info(f"RECOVERY: {alert.message}")
                self.send_alert_email(customer, alert)

            status.reset_failures()

    def send_alert_email(self, customer, alert):
        """Send alert email to admins"""
        try:
            from email_utils import send_monitoring_alert
            success, message = send_monitoring_alert(customer, alert)
            if success:
                alert.mark_email_sent()
                logger.info(f"Alert email sent for {customer.domain}")
            else:
                logger.error(f"Failed to send alert email: {message}")
        except ImportError:
            logger.warning("send_monitoring_alert not available in email_utils")
        except Exception as e:
            logger.error(f"Failed to send alert email: {e}")


def main():
    """Entry point"""
    worker = MonitoringWorker()
    worker.run()


if __name__ == '__main__':
    main()
