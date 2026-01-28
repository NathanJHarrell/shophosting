# Resource Limits Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement comprehensive resource limits (disk quotas, bandwidth monitoring, alerts, dashboards) to protect customers from each other.

**Architecture:** ext4 project quotas for hard disk limits, Nginx log parsing for bandwidth tracking, alert worker for threshold notifications, dashboard updates for visibility.

**Tech Stack:** Python, MySQL, ext4 quotas, Nginx logs, systemd

---

## Task 1: Database Migration

**Files:**
- Create: `migrations/010_add_resource_limits.sql`

**Step 1: Create the migration file**

```sql
-- Resource Limits Migration
-- Run: mysql -u root -p shophosting_db < migrations/010_add_resource_limits.sql

USE shophosting_db;

-- Add resource limit columns to pricing_plans table
ALTER TABLE pricing_plans
    ADD COLUMN disk_limit_gb INT NOT NULL DEFAULT 25 AFTER cpu_limit,
    ADD COLUMN bandwidth_limit_gb INT NOT NULL DEFAULT 250 AFTER disk_limit_gb;

-- Update WooCommerce plans with generous limits
UPDATE pricing_plans SET disk_limit_gb = 25,  bandwidth_limit_gb = 250  WHERE slug = 'wp-commerce-core';
UPDATE pricing_plans SET disk_limit_gb = 50,  bandwidth_limit_gb = 500  WHERE slug = 'wp-commerce-pro';
UPDATE pricing_plans SET disk_limit_gb = 100, bandwidth_limit_gb = 1000 WHERE slug = 'wp-commerce-scale';
UPDATE pricing_plans SET disk_limit_gb = 100, bandwidth_limit_gb = 1000 WHERE slug = 'wp-multi-store';
UPDATE pricing_plans SET disk_limit_gb = 200, bandwidth_limit_gb = 2000 WHERE slug = 'wp-agency';
UPDATE pricing_plans SET disk_limit_gb = 500, bandwidth_limit_gb = 5000 WHERE slug = 'wp-agency-plus';

-- Update Magento plans with generous limits
UPDATE pricing_plans SET disk_limit_gb = 25,  bandwidth_limit_gb = 250  WHERE slug = 'mg-commerce-core';
UPDATE pricing_plans SET disk_limit_gb = 50,  bandwidth_limit_gb = 500  WHERE slug = 'mg-commerce-pro';
UPDATE pricing_plans SET disk_limit_gb = 100, bandwidth_limit_gb = 1000 WHERE slug = 'mg-commerce-scale';
UPDATE pricing_plans SET disk_limit_gb = 100, bandwidth_limit_gb = 1000 WHERE slug = 'mg-multi-store';
UPDATE pricing_plans SET disk_limit_gb = 200, bandwidth_limit_gb = 2000 WHERE slug = 'mg-agency';
UPDATE pricing_plans SET disk_limit_gb = 500, bandwidth_limit_gb = 5000 WHERE slug = 'mg-agency-plus';

-- Daily resource usage snapshots
CREATE TABLE IF NOT EXISTS resource_usage (
    id INT AUTO_INCREMENT PRIMARY KEY,
    customer_id INT NOT NULL,
    date DATE NOT NULL,
    disk_used_bytes BIGINT DEFAULT 0,
    bandwidth_used_bytes BIGINT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
    UNIQUE KEY unique_customer_date (customer_id, date),
    INDEX idx_date (date),
    INDEX idx_customer (customer_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Resource alerts history
CREATE TABLE IF NOT EXISTS resource_alerts (
    id INT AUTO_INCREMENT PRIMARY KEY,
    customer_id INT NOT NULL,
    alert_type ENUM('disk_warning', 'disk_critical', 'bandwidth_warning', 'bandwidth_critical') NOT NULL,
    threshold_percent INT NOT NULL,
    current_usage_bytes BIGINT NOT NULL,
    limit_bytes BIGINT NOT NULL,
    notified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
    INDEX idx_customer_type (customer_id, alert_type),
    INDEX idx_notified (notified_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Add project_id column to customers for quota tracking
ALTER TABLE customers
    ADD COLUMN quota_project_id INT AFTER server_id;
```

**Step 2: Commit**

```bash
git add migrations/010_add_resource_limits.sql
git commit -m "feat(db): add resource limits migration

- Add disk_limit_gb and bandwidth_limit_gb to pricing_plans
- Create resource_usage table for daily snapshots
- Create resource_alerts table for alert history
- Add quota_project_id to customers for ext4 project quotas"
```

---

## Task 2: Update PricingPlan Model

**Files:**
- Modify: `webapp/models.py` (PricingPlan class around line 436-498)

**Step 1: Update PricingPlan __init__ to include new fields**

Find the PricingPlan `__init__` method and add the new parameters:

```python
def __init__(self, id=None, name=None, slug=None, platform=None, tier_type=None,
             price_monthly=None, store_limit=1, stripe_product_id=None,
             stripe_price_id=None, features=None, memory_limit='1g',
             cpu_limit='1.0', disk_limit_gb=25, bandwidth_limit_gb=250,
             is_active=True, display_order=0,
             created_at=None, updated_at=None):
    self.id = id
    self.name = name
    self.slug = slug
    self.platform = platform
    self.tier_type = tier_type
    self.price_monthly = price_monthly
    self.store_limit = store_limit
    self.stripe_product_id = stripe_product_id
    self.stripe_price_id = stripe_price_id
    self.features = features if features else {}
    self.memory_limit = memory_limit
    self.cpu_limit = cpu_limit
    self.disk_limit_gb = disk_limit_gb
    self.bandwidth_limit_gb = bandwidth_limit_gb
    self.is_active = is_active
    self.display_order = display_order
    self.created_at = created_at
    self.updated_at = updated_at
```

**Step 2: Commit**

```bash
git add webapp/models.py
git commit -m "feat(models): add disk and bandwidth limits to PricingPlan"
```

---

## Task 3: Add ResourceUsage Model

**Files:**
- Modify: `webapp/models.py` (add after PricingPlan class)

**Step 1: Add ResourceUsage class**

Add after the PricingPlan class:

```python
# =============================================================================
# ResourceUsage Model
# =============================================================================

class ResourceUsage:
    """Daily resource usage snapshot for a customer"""

    def __init__(self, id=None, customer_id=None, date=None,
                 disk_used_bytes=0, bandwidth_used_bytes=0, created_at=None):
        self.id = id
        self.customer_id = customer_id
        self.date = date
        self.disk_used_bytes = disk_used_bytes
        self.bandwidth_used_bytes = bandwidth_used_bytes
        self.created_at = created_at

    def save(self):
        """Save or update resource usage record"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            # Use INSERT ... ON DUPLICATE KEY UPDATE for upsert
            cursor.execute("""
                INSERT INTO resource_usage (customer_id, date, disk_used_bytes, bandwidth_used_bytes)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    disk_used_bytes = VALUES(disk_used_bytes),
                    bandwidth_used_bytes = VALUES(bandwidth_used_bytes)
            """, (self.customer_id, self.date, self.disk_used_bytes, self.bandwidth_used_bytes))
            conn.commit()
            if self.id is None:
                self.id = cursor.lastrowid
            return self
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_for_customer(customer_id, date):
        """Get usage for a specific customer and date"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute(
                "SELECT * FROM resource_usage WHERE customer_id = %s AND date = %s",
                (customer_id, date)
            )
            row = cursor.fetchone()
            if row:
                return ResourceUsage(**row)
            return None
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_monthly_bandwidth(customer_id):
        """Get total bandwidth used in current billing month"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT COALESCE(SUM(bandwidth_used_bytes), 0)
                FROM resource_usage
                WHERE customer_id = %s
                AND date >= DATE_FORMAT(NOW(), '%%Y-%%m-01')
            """, (customer_id,))
            result = cursor.fetchone()
            return result[0] if result else 0
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_current_disk_usage(customer_id):
        """Get most recent disk usage for customer"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT disk_used_bytes FROM resource_usage
                WHERE customer_id = %s
                ORDER BY date DESC LIMIT 1
            """, (customer_id,))
            result = cursor.fetchone()
            return result[0] if result else 0
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_usage_history(customer_id, days=30):
        """Get usage history for last N days"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("""
                SELECT * FROM resource_usage
                WHERE customer_id = %s
                AND date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
                ORDER BY date ASC
            """, (customer_id, days))
            rows = cursor.fetchall()
            return [ResourceUsage(**row) for row in rows]
        finally:
            cursor.close()
            conn.close()
```

**Step 2: Commit**

```bash
git add webapp/models.py
git commit -m "feat(models): add ResourceUsage model for tracking disk/bandwidth"
```

---

## Task 4: Add ResourceAlert Model

**Files:**
- Modify: `webapp/models.py` (add after ResourceUsage class)

**Step 1: Add ResourceAlert class**

```python
# =============================================================================
# ResourceAlert Model
# =============================================================================

class ResourceAlert:
    """Resource limit alert record"""

    ALERT_TYPES = ['disk_warning', 'disk_critical', 'bandwidth_warning', 'bandwidth_critical']

    def __init__(self, id=None, customer_id=None, alert_type=None,
                 threshold_percent=None, current_usage_bytes=None,
                 limit_bytes=None, notified_at=None):
        self.id = id
        self.customer_id = customer_id
        self.alert_type = alert_type
        self.threshold_percent = threshold_percent
        self.current_usage_bytes = current_usage_bytes
        self.limit_bytes = limit_bytes
        self.notified_at = notified_at

    def save(self):
        """Save alert record"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO resource_alerts
                (customer_id, alert_type, threshold_percent, current_usage_bytes, limit_bytes)
                VALUES (%s, %s, %s, %s, %s)
            """, (self.customer_id, self.alert_type, self.threshold_percent,
                  self.current_usage_bytes, self.limit_bytes))
            conn.commit()
            self.id = cursor.lastrowid
            return self
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def was_recently_sent(customer_id, alert_type, hours=24):
        """Check if this alert type was sent recently"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT COUNT(*) FROM resource_alerts
                WHERE customer_id = %s
                AND alert_type = %s
                AND notified_at > DATE_SUB(NOW(), INTERVAL %s HOUR)
            """, (customer_id, alert_type, hours))
            count = cursor.fetchone()[0]
            return count > 0
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_recent_for_customer(customer_id, limit=10):
        """Get recent alerts for a customer"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("""
                SELECT * FROM resource_alerts
                WHERE customer_id = %s
                ORDER BY notified_at DESC
                LIMIT %s
            """, (customer_id, limit))
            rows = cursor.fetchall()
            return [ResourceAlert(**row) for row in rows]
        finally:
            cursor.close()
            conn.close()
```

**Step 2: Commit**

```bash
git add webapp/models.py
git commit -m "feat(models): add ResourceAlert model for alert history"
```

---

## Task 5: Update Customer Model

**Files:**
- Modify: `webapp/models.py` (Customer class)

**Step 1: Add quota_project_id to Customer __init__**

Find the Customer `__init__` and add `quota_project_id` parameter:

```python
def __init__(self, id=None, email=None, password_hash=None, company_name=None,
             domain=None, platform=None, status='pending', web_port=None,
             server_id=None, quota_project_id=None, db_name=None, db_user=None,
             db_password=None, admin_user=None, admin_password=None,
             error_message=None, stripe_customer_id=None, plan_id=None,
             staging_count=None, created_at=None, updated_at=None):
    # ... existing assignments ...
    self.quota_project_id = quota_project_id
```

**Step 2: Add resource usage helper methods to Customer**

Add these methods to the Customer class:

```python
def get_resource_usage(self):
    """Get current resource usage with limits"""
    plan = PricingPlan.get_by_id(self.plan_id) if self.plan_id else None

    disk_used = ResourceUsage.get_current_disk_usage(self.id)
    bandwidth_used = ResourceUsage.get_monthly_bandwidth(self.id)

    disk_limit = (plan.disk_limit_gb * 1024 * 1024 * 1024) if plan else 25 * 1024 * 1024 * 1024
    bandwidth_limit = (plan.bandwidth_limit_gb * 1024 * 1024 * 1024) if plan else 250 * 1024 * 1024 * 1024

    return {
        'disk': {
            'used_bytes': disk_used,
            'limit_bytes': disk_limit,
            'used_gb': round(disk_used / (1024 * 1024 * 1024), 2),
            'limit_gb': plan.disk_limit_gb if plan else 25,
            'percent': round((disk_used / disk_limit) * 100, 1) if disk_limit > 0 else 0
        },
        'bandwidth': {
            'used_bytes': bandwidth_used,
            'limit_bytes': bandwidth_limit,
            'used_gb': round(bandwidth_used / (1024 * 1024 * 1024), 2),
            'limit_gb': plan.bandwidth_limit_gb if plan else 250,
            'percent': round((bandwidth_used / bandwidth_limit) * 100, 1) if bandwidth_limit > 0 else 0
        }
    }
```

**Step 3: Update Customer.save() to include quota_project_id**

Update the INSERT and UPDATE queries in `save()` to include the new column.

**Step 4: Commit**

```bash
git add webapp/models.py
git commit -m "feat(models): add quota_project_id and resource usage to Customer"
```

---

## Task 6: Add Resource Alert Email Templates

**Files:**
- Modify: `webapp/email_utils.py`

**Step 1: Add resource alert email function**

Add after the existing email functions:

```python
def send_resource_alert(customer, alert_type, resource_type, used_gb, limit_gb, percent):
    """
    Send resource limit alert email to customer.

    Args:
        customer: Customer object
        alert_type: 'warning' or 'critical'
        resource_type: 'disk' or 'bandwidth'
        used_gb: Current usage in GB
        limit_gb: Limit in GB
        percent: Usage percentage
    """
    resource_name = 'Disk Space' if resource_type == 'disk' else 'Monthly Bandwidth'

    if alert_type == 'warning':
        subject = f"Warning: {resource_name} at {percent}% - Action Recommended"
        urgency = "approaching"
        color = "#f59e0b"  # Warning orange
    else:
        subject = f"Critical: {resource_name} at {percent}% - Immediate Action Required"
        urgency = "nearly reached"
        color = "#ef4444"  # Critical red

    action_text = ""
    if resource_type == 'disk':
        action_text = """
        <p>To free up space, consider:</p>
        <ul>
            <li>Deleting unused media files</li>
            <li>Clearing old backups</li>
            <li>Removing unused plugins/themes</li>
        </ul>
        """
    else:
        action_text = """
        <p>High bandwidth usage may indicate:</p>
        <ul>
            <li>Increased traffic (great news!)</li>
            <li>Large file downloads</li>
            <li>Unoptimized images</li>
        </ul>
        """

    html_body = f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
        <div style="background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%); padding: 30px; border-radius: 12px; color: white;">
            <h1 style="margin: 0 0 10px 0; font-size: 24px;">{resource_name} Alert</h1>
            <p style="margin: 0; opacity: 0.8;">for {customer.domain}</p>
        </div>

        <div style="padding: 30px 0;">
            <div style="background: {color}15; border: 1px solid {color}40; border-radius: 8px; padding: 20px; margin-bottom: 20px;">
                <p style="margin: 0; color: {color}; font-weight: 600; font-size: 18px;">
                    You've {urgency} your {resource_name.lower()} limit
                </p>
            </div>

            <div style="background: #f8fafc; border-radius: 8px; padding: 20px; margin-bottom: 20px;">
                <div style="display: flex; justify-content: space-between; margin-bottom: 10px;">
                    <span style="color: #64748b;">Current Usage</span>
                    <span style="font-weight: 600;">{used_gb:.1f} GB / {limit_gb} GB ({percent}%)</span>
                </div>
                <div style="background: #e2e8f0; border-radius: 4px; height: 8px; overflow: hidden;">
                    <div style="background: {color}; height: 100%; width: {min(percent, 100)}%;"></div>
                </div>
            </div>

            {action_text}

            <p>Need more resources? <a href="https://shophosting.io/dashboard" style="color: #0088ff;">Upgrade your plan</a> for increased limits.</p>
        </div>

        <div style="border-top: 1px solid #e2e8f0; padding-top: 20px; color: #64748b; font-size: 14px;">
            <p>Questions? Contact us at <a href="mailto:support@shophosting.io" style="color: #0088ff;">support@shophosting.io</a></p>
        </div>
    </body>
    </html>
    """

    return send_email(customer.email, subject, html_body)
```

**Step 2: Commit**

```bash
git add webapp/email_utils.py
git commit -m "feat(email): add resource limit alert email templates"
```

---

## Task 7: Create Resource Worker

**Files:**
- Create: `provisioning/resource_worker.py`

**Step 1: Create the resource worker**

```python
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
```

**Step 2: Commit**

```bash
git add provisioning/resource_worker.py
git commit -m "feat(worker): add resource worker for usage collection and alerts"
```

---

## Task 8: Create Resource Worker Service

**Files:**
- Create: `provisioning/resource-worker.service`

**Step 1: Create systemd service file**

```ini
[Unit]
Description=ShopHosting Resource Usage Worker
After=network.target mysql.service

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=/opt/shophosting/provisioning
Environment="PATH=/opt/shophosting/provisioning/venv/bin:/usr/local/bin:/usr/bin:/bin"
EnvironmentFile=/opt/shophosting/.env
ExecStart=/opt/shophosting/provisioning/venv/bin/python /opt/shophosting/provisioning/resource_worker.py
Restart=always
RestartSec=10

# Logging
StandardOutput=append:/opt/shophosting/logs/resource_worker.log
StandardError=append:/opt/shophosting/logs/resource_worker.log

[Install]
WantedBy=multi-user.target
```

**Step 2: Commit**

```bash
git add provisioning/resource-worker.service
git commit -m "feat(service): add systemd service for resource worker"
```

---

## Task 9: Update Provisioning Worker for Quota Setup

**Files:**
- Modify: `provisioning/provisioning_worker.py`

**Step 1: Add quota setup method to ProvisioningWorker class**

Add this method after `create_customer_directory`:

```python
def setup_disk_quota(self, customer_id, disk_limit_gb):
    """Set up ext4 project quota for customer directory"""
    customer_path = self.base_path / f"customer-{customer_id}"
    project_id = 1000 + customer_id  # Offset to avoid conflicts

    try:
        # Check if quotas are enabled
        check = subprocess.run(['sudo', 'quotaon', '-p', '/'], capture_output=True, text=True)
        if 'project quota' not in check.stdout.lower() and 'project quota' not in check.stderr.lower():
            logger.warning("Project quotas not enabled on filesystem, skipping quota setup")
            return None

        # Assign project ID to directory
        subprocess.run(
            ['sudo', 'chattr', '+P', '-p', str(project_id), str(customer_path)],
            check=True, capture_output=True, timeout=30
        )

        # Set quota (soft=hard for strict enforcement)
        limit_kb = disk_limit_gb * 1024 * 1024
        subprocess.run(
            ['sudo', 'setquota', '-P', str(project_id),
             str(limit_kb), str(limit_kb),  # soft, hard block limits
             '0', '0',  # no inode limits
             '/'],
            check=True, capture_output=True, timeout=30
        )

        logger.info(f"Set disk quota {disk_limit_gb}GB for customer {customer_id} (project {project_id})")
        return project_id

    except subprocess.CalledProcessError as e:
        logger.warning(f"Failed to set quota for customer {customer_id}: {e}")
        return None
    except Exception as e:
        logger.warning(f"Quota setup error for customer {customer_id}: {e}")
        return None
```

**Step 2: Update provision_customer to call quota setup**

In the `provision_customer` method, after `create_customer_directory` and after generating config:

```python
# Set up disk quota
disk_limit_gb = job_data.get('disk_limit_gb', 25)
project_id = self.setup_disk_quota(customer_id, disk_limit_gb)
if project_id:
    config['quota_project_id'] = project_id
```

**Step 3: Update save_customer_credentials to save quota_project_id**

```python
def save_customer_credentials(self, customer_id, credentials):
    """Save customer credentials to database"""
    try:
        conn = self.get_db_connection()
        cursor = conn.cursor()

        quota_project_id = credentials.get('quota_project_id')
        # ... rest of method, adding quota_project_id to the UPDATE query
```

**Step 4: Commit**

```bash
git add provisioning/provisioning_worker.py
git commit -m "feat(provisioning): add disk quota setup during provisioning"
```

---

## Task 10: Update Customer Dashboard

**Files:**
- Modify: `webapp/templates/dashboard.html`

**Step 1: Add resource usage styles**

Add to the `<style>` section:

```css
/* Resource Usage */
.resource-usage-card {
    margin-top: 24px;
}

.resource-bars {
    padding: 24px 28px;
}

.resource-item {
    margin-bottom: 20px;
}

.resource-item:last-child {
    margin-bottom: 0;
}

.resource-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 8px;
}

.resource-label {
    color: var(--text-secondary);
    font-size: 0.925rem;
    font-weight: 500;
}

.resource-value {
    color: var(--text-primary);
    font-size: 0.9rem;
    font-weight: 600;
}

.resource-bar {
    height: 8px;
    background: var(--bg-surface);
    border-radius: 4px;
    overflow: hidden;
}

.resource-bar-fill {
    height: 100%;
    border-radius: 4px;
    transition: width 0.3s ease;
}

.resource-bar-fill.healthy {
    background: var(--success);
}

.resource-bar-fill.warning {
    background: var(--warning);
}

.resource-bar-fill.critical {
    background: var(--error);
}
```

**Step 2: Add resource usage section to dashboard**

Add after the credentials card section (inside the `{% elif customer.status == 'active' and credentials %}` block):

```html
<!-- Resource Usage -->
<div class="info-card resource-usage-card">
    <div class="info-card-header">
        <h2 class="info-card-title">Resource Usage</h2>
    </div>
    <div class="resource-bars">
        {% set usage = customer.get_resource_usage() %}

        <!-- Disk Usage -->
        <div class="resource-item">
            <div class="resource-header">
                <span class="resource-label">Disk Space</span>
                <span class="resource-value">{{ usage.disk.used_gb }} / {{ usage.disk.limit_gb }} GB ({{ usage.disk.percent }}%)</span>
            </div>
            <div class="resource-bar">
                <div class="resource-bar-fill {% if usage.disk.percent >= 90 %}critical{% elif usage.disk.percent >= 80 %}warning{% else %}healthy{% endif %}"
                     style="width: {{ [usage.disk.percent, 100] | min }}%"></div>
            </div>
        </div>

        <!-- Bandwidth Usage -->
        <div class="resource-item">
            <div class="resource-header">
                <span class="resource-label">Bandwidth (This Month)</span>
                <span class="resource-value">{{ usage.bandwidth.used_gb }} / {{ usage.bandwidth.limit_gb }} GB ({{ usage.bandwidth.percent }}%)</span>
            </div>
            <div class="resource-bar">
                <div class="resource-bar-fill {% if usage.bandwidth.percent >= 90 %}critical{% elif usage.bandwidth.percent >= 80 %}warning{% else %}healthy{% endif %}"
                     style="width: {{ [usage.bandwidth.percent, 100] | min }}%"></div>
            </div>
        </div>
    </div>
</div>
```

**Step 3: Commit**

```bash
git add webapp/templates/dashboard.html
git commit -m "feat(dashboard): add resource usage display with progress bars"
```

---

## Task 11: Update Pricing Page

**Files:**
- Modify: `webapp/templates/pricing.html`

**Step 1: Add disk and bandwidth to plan features**

In both WooCommerce and Magento plan cards, update the features list to include storage and bandwidth. Find the `<ul class="plan-features">` sections and add:

```html
<li>{{ plan.disk_limit_gb }}GB SSD Storage</li>
<li>{{ plan.bandwidth_limit_gb }}GB Bandwidth/month</li>
```

Add these after the "Daily backups" line in each plan card.

**Step 2: Commit**

```bash
git add webapp/templates/pricing.html
git commit -m "feat(pricing): display disk and bandwidth limits on pricing page"
```

---

## Task 12: Add Admin Resource View

**Files:**
- Modify: `webapp/admin/routes.py`

**Step 1: Add resource usage endpoint for admin**

Add a new route:

```python
@admin_bp.route('/customers/<int:customer_id>/resources')
@admin_required
def customer_resources(customer_id):
    """View detailed resource usage for a customer"""
    customer = Customer.get_by_id(customer_id)
    if not customer:
        flash('Customer not found', 'error')
        return redirect(url_for('admin.customers'))

    usage = customer.get_resource_usage()
    history = ResourceUsage.get_usage_history(customer_id, days=30)
    alerts = ResourceAlert.get_recent_for_customer(customer_id, limit=20)
    plan = PricingPlan.get_by_id(customer.plan_id) if customer.plan_id else None

    return render_template('admin/customer_resources.html',
                         customer=customer,
                         usage=usage,
                         history=history,
                         alerts=alerts,
                         plan=plan)
```

**Step 2: Import the new models at the top of the file**

```python
from models import Customer, PricingPlan, ResourceUsage, ResourceAlert
```

**Step 3: Commit**

```bash
git add webapp/admin/routes.py
git commit -m "feat(admin): add customer resource usage view"
```

---

## Task 13: Create Admin Resource Template

**Files:**
- Create: `webapp/templates/admin/customer_resources.html`

**Step 1: Create the template**

```html
{% extends "admin/base_admin.html" %}

{% block title %}Resource Usage - {{ customer.domain }} - Admin{% endblock %}

{% block content %}
<div class="admin-header">
    <h1>Resource Usage: {{ customer.domain }}</h1>
    <a href="{{ url_for('admin.customer_detail', customer_id=customer.id) }}" class="btn btn-secondary">Back to Customer</a>
</div>

<div class="admin-grid">
    <!-- Current Usage -->
    <div class="admin-card">
        <h3>Current Usage</h3>
        <div class="resource-summary">
            <div class="resource-stat">
                <span class="label">Disk Space</span>
                <span class="value {% if usage.disk.percent >= 90 %}critical{% elif usage.disk.percent >= 80 %}warning{% endif %}">
                    {{ usage.disk.used_gb }} / {{ usage.disk.limit_gb }} GB ({{ usage.disk.percent }}%)
                </span>
                <div class="progress-bar">
                    <div class="progress-fill {% if usage.disk.percent >= 90 %}critical{% elif usage.disk.percent >= 80 %}warning{% endif %}"
                         style="width: {{ [usage.disk.percent, 100] | min }}%"></div>
                </div>
            </div>
            <div class="resource-stat">
                <span class="label">Monthly Bandwidth</span>
                <span class="value {% if usage.bandwidth.percent >= 90 %}critical{% elif usage.bandwidth.percent >= 80 %}warning{% endif %}">
                    {{ usage.bandwidth.used_gb }} / {{ usage.bandwidth.limit_gb }} GB ({{ usage.bandwidth.percent }}%)
                </span>
                <div class="progress-bar">
                    <div class="progress-fill {% if usage.bandwidth.percent >= 90 %}critical{% elif usage.bandwidth.percent >= 80 %}warning{% endif %}"
                         style="width: {{ [usage.bandwidth.percent, 100] | min }}%"></div>
                </div>
            </div>
        </div>
    </div>

    <!-- Plan Info -->
    <div class="admin-card">
        <h3>Plan Limits</h3>
        {% if plan %}
        <table class="admin-table">
            <tr><td>Plan</td><td>{{ plan.name }}</td></tr>
            <tr><td>Disk Limit</td><td>{{ plan.disk_limit_gb }} GB</td></tr>
            <tr><td>Bandwidth Limit</td><td>{{ plan.bandwidth_limit_gb }} GB/month</td></tr>
            <tr><td>Memory</td><td>{{ plan.memory_limit }}</td></tr>
            <tr><td>CPU</td><td>{{ plan.cpu_limit }}</td></tr>
        </table>
        {% else %}
        <p>No plan assigned</p>
        {% endif %}
    </div>
</div>

<!-- Recent Alerts -->
<div class="admin-card">
    <h3>Recent Alerts</h3>
    {% if alerts %}
    <table class="admin-table">
        <thead>
            <tr>
                <th>Type</th>
                <th>Threshold</th>
                <th>Usage</th>
                <th>Date</th>
            </tr>
        </thead>
        <tbody>
            {% for alert in alerts %}
            <tr>
                <td><span class="badge badge-{{ 'error' if 'critical' in alert.alert_type else 'warning' }}">{{ alert.alert_type }}</span></td>
                <td>{{ alert.threshold_percent }}%</td>
                <td>{{ (alert.current_usage_bytes / 1073741824) | round(2) }} GB</td>
                <td>{{ alert.notified_at.strftime('%Y-%m-%d %H:%M') }}</td>
            </tr>
            {% endfor %}
        </tbody>
    </table>
    {% else %}
    <p>No alerts recorded</p>
    {% endif %}
</div>

<!-- Usage History -->
<div class="admin-card">
    <h3>30-Day History</h3>
    {% if history %}
    <table class="admin-table">
        <thead>
            <tr>
                <th>Date</th>
                <th>Disk Used</th>
                <th>Bandwidth</th>
            </tr>
        </thead>
        <tbody>
            {% for day in history %}
            <tr>
                <td>{{ day.date }}</td>
                <td>{{ (day.disk_used_bytes / 1073741824) | round(2) }} GB</td>
                <td>{{ (day.bandwidth_used_bytes / 1073741824) | round(2) }} GB</td>
            </tr>
            {% endfor %}
        </tbody>
    </table>
    {% else %}
    <p>No history available</p>
    {% endif %}
</div>

<style>
.resource-summary { display: grid; gap: 20px; }
.resource-stat { margin-bottom: 16px; }
.resource-stat .label { display: block; color: var(--text-secondary); margin-bottom: 4px; }
.resource-stat .value { display: block; font-size: 1.25rem; font-weight: 600; margin-bottom: 8px; }
.resource-stat .value.warning { color: var(--warning); }
.resource-stat .value.critical { color: var(--error); }
.progress-bar { height: 8px; background: var(--bg-surface); border-radius: 4px; overflow: hidden; }
.progress-fill { height: 100%; background: var(--success); border-radius: 4px; }
.progress-fill.warning { background: var(--warning); }
.progress-fill.critical { background: var(--error); }
</style>
{% endblock %}
```

**Step 2: Commit**

```bash
git add webapp/templates/admin/customer_resources.html
git commit -m "feat(admin): add customer resource usage detail template"
```

---

## Task 14: Final Integration Commit

**Step 1: Verify all files are committed**

```bash
git status
```

**Step 2: Create integration commit if needed**

If there are any remaining changes:

```bash
git add -A
git commit -m "feat(resources): complete resource limits implementation

- Database migration for limits and usage tracking
- PricingPlan, ResourceUsage, ResourceAlert models
- Resource worker for usage collection and alerts
- Customer dashboard with usage display
- Pricing page with disk/bandwidth limits
- Admin resource usage views
- Email templates for limit alerts"
```

---

## Post-Implementation: Ops Tasks

These require manual execution on the server:

1. **Run database migration:**
   ```bash
   mysql -u root -p shophosting_db < /opt/shophosting/migrations/010_add_resource_limits.sql
   ```

2. **Install quota tools:**
   ```bash
   sudo apt install quota
   ```

3. **Enable project quotas (requires remount):**
   ```bash
   # Edit /etc/fstab, add prjquota to root filesystem options
   sudo mount -o remount /
   sudo quotacheck -ugm /
   sudo quotaon /
   ```

4. **Deploy resource worker:**
   ```bash
   sudo cp /opt/shophosting/provisioning/resource-worker.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable resource-worker
   sudo systemctl start resource-worker
   ```

5. **Verify worker is running:**
   ```bash
   sudo systemctl status resource-worker
   tail -f /opt/shophosting/logs/resource_worker.log
   ```
