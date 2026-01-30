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
