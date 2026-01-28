# Resource Limits & Quotas Design

**Date:** 2026-01-28
**Branch:** `feature/resource-limits`
**Status:** Approved for implementation

## Overview

Implement comprehensive resource management to protect customers from each other ("noisy neighbor" problem) and provide usage visibility.

## Components

### 1. Disk Quotas (Hard Enforcement)

**Technology:** ext4 project quotas

**Limits per tier:**

| Plan | Disk Quota |
|------|-----------|
| Commerce Core | 25GB |
| Commerce Pro | 50GB |
| Commerce Scale | 100GB |
| Multi-Store | 100GB |
| Agency | 200GB |
| Agency Plus | 500GB |

**Implementation:**
- Assign project ID (1000 + customer_id) to each customer directory
- Set quota via `setquota -P`
- Query usage via `repquota -P`

**Server setup required:**
```bash
apt install quota
# Add prjquota to /etc/fstab for root filesystem
mount -o remount /
quotacheck -ugm /
```

### 2. Bandwidth Monitoring (Soft Enforcement)

**Technology:** Nginx log parsing

**Limits per tier:**

| Plan | Bandwidth/Month |
|------|-----------------|
| Commerce Core | 250GB |
| Commerce Pro | 500GB |
| Commerce Scale | 1TB |
| Multi-Store | 1TB |
| Agency | 2TB |
| Agency Plus | 5TB |

**Implementation:**
- Parse `/var/log/nginx/customer-{id}-access.log` hourly
- Sum `$body_bytes_sent` field
- Store daily totals in database
- Aggregate monthly for billing period

**Enforcement:** Alerts only, no automatic throttling

### 3. Alerting

**Thresholds:**
- 80% → Warning email to customer
- 90% → Critical email to customer + admin

**Deduplication:** One alert per type per 24 hours

### 4. Dashboard Visibility

**Customer dashboard:**
- Disk usage progress bar with percentage
- Monthly bandwidth usage progress bar
- Clear display of limits

**Admin dashboard:**
- All customers' resource usage
- Filter by "approaching limits"
- Historical usage data

### 5. Pricing Page Updates

Display resource limits on public pricing page:
- "50GB SSD Storage"
- "500GB Bandwidth/month"

## Database Schema

```sql
-- Add to pricing_plans
ALTER TABLE pricing_plans
    ADD COLUMN disk_limit_gb INT NOT NULL DEFAULT 25,
    ADD COLUMN bandwidth_limit_gb INT NOT NULL DEFAULT 250;

-- Daily usage snapshots
CREATE TABLE resource_usage (
    id INT AUTO_INCREMENT PRIMARY KEY,
    customer_id INT NOT NULL,
    date DATE NOT NULL,
    disk_used_bytes BIGINT DEFAULT 0,
    bandwidth_used_bytes BIGINT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
    UNIQUE KEY unique_customer_date (customer_id, date),
    INDEX idx_date (date)
);

-- Alert history
CREATE TABLE resource_alerts (
    id INT AUTO_INCREMENT PRIMARY KEY,
    customer_id INT NOT NULL,
    alert_type ENUM('disk_warning', 'disk_critical', 'bandwidth_warning', 'bandwidth_critical'),
    threshold_percent INT,
    current_usage_bytes BIGINT,
    limit_bytes BIGINT,
    notified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
    INDEX idx_customer_type (customer_id, alert_type)
);
```

## Files to Create/Modify

### New Files
- `migrations/010_add_resource_limits.sql` - Database migration
- `provisioning/resource_worker.py` - Usage collection & alerting worker
- `provisioning/resource-worker.service` - Systemd service

### Modified Files
- `provisioning/provisioning_worker.py` - Add quota setup during provisioning
- `webapp/models.py` - Add ResourceUsage, ResourceAlert models
- `webapp/templates/dashboard.html` - Add usage display
- `webapp/admin/routes.py` - Add admin usage views
- `webapp/templates/pricing.html` - Show limits per plan
- `webapp/email_utils.py` - Add alert email templates

## Implementation Order

1. Database migration
2. Models (ResourceUsage, ResourceAlert, update PricingPlan)
3. Provisioning worker changes (quota setup)
4. Resource worker (usage collection + alerts)
5. Customer dashboard updates
6. Admin dashboard updates
7. Pricing page updates
8. Email templates

## Ops Tasks (Manual)

1. Install quota tools: `apt install quota`
2. Edit `/etc/fstab`: add `prjquota` option to root filesystem
3. Remount: `mount -o remount /` (or schedule reboot)
4. Initialize: `quotacheck -ugm /`
5. Deploy resource-worker.service

## Rollback Plan

- Quotas can be removed with `setquota -P <id> 0 0 0 0 /`
- Database columns are additive, no destructive changes
- Worker can be stopped without affecting site operation
