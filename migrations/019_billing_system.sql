-- Billing System Tables
-- Run with: mysql -u root shophosting < migrations/019_billing_system.sql

-- Add finance_admin role to admin_users
-- Note: MySQL ENUM modification requires redefining all values
ALTER TABLE admin_users
    MODIFY COLUMN role ENUM('super_admin', 'admin', 'support', 'finance_admin') DEFAULT 'admin';

-- Billing audit log - immutable audit trail for all billing actions
CREATE TABLE IF NOT EXISTS billing_audit_log (
    id INT AUTO_INCREMENT PRIMARY KEY,
    admin_user_id INT NOT NULL,
    action_type ENUM(
        'refund',
        'credit',
        'plan_change',
        'subscription_cancel',
        'subscription_pause',
        'subscription_resume',
        'invoice_create',
        'payment_retry',
        'payment_method_update',
        'coupon_apply',
        'settings_change'
    ) NOT NULL,
    target_customer_id INT NULL,
    target_invoice_id INT NULL,
    target_subscription_id INT NULL,
    amount_cents INT DEFAULT 0,
    currency VARCHAR(3) DEFAULT 'usd',
    before_state JSON NULL,
    after_state JSON NULL,
    reason TEXT NULL,
    stripe_request_id VARCHAR(255) NULL,
    ip_address VARCHAR(45) NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Indexes for searching
    INDEX idx_admin_user_id (admin_user_id),
    INDEX idx_action_type (action_type),
    INDEX idx_target_customer_id (target_customer_id),
    INDEX idx_target_invoice_id (target_invoice_id),
    INDEX idx_target_subscription_id (target_subscription_id),
    INDEX idx_created_at (created_at),

    -- Foreign keys
    FOREIGN KEY (admin_user_id) REFERENCES admin_users(id) ON DELETE RESTRICT,
    FOREIGN KEY (target_customer_id) REFERENCES customers(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Customer credits table
CREATE TABLE IF NOT EXISTS customer_credits (
    id INT AUTO_INCREMENT PRIMARY KEY,
    customer_id INT NOT NULL,
    amount_cents INT NOT NULL,
    currency VARCHAR(3) DEFAULT 'usd',
    reason TEXT NOT NULL,
    created_by_admin_id INT NOT NULL,
    applied_to_invoice_id INT NULL,
    expires_at TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Indexes
    INDEX idx_customer_id (customer_id),
    INDEX idx_created_by_admin_id (created_by_admin_id),
    INDEX idx_expires_at (expires_at),

    -- Foreign keys
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
    FOREIGN KEY (created_by_admin_id) REFERENCES admin_users(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Billing settings (key-value store)
CREATE TABLE IF NOT EXISTS billing_settings (
    setting_key VARCHAR(100) PRIMARY KEY,
    setting_value JSON NOT NULL,
    updated_by_admin_id INT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    -- Foreign key
    FOREIGN KEY (updated_by_admin_id) REFERENCES admin_users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Add fields to invoices table for manual invoices
-- Note: Run these separately if columns already exist (MySQL < 8.0.19 doesn't support IF NOT EXISTS)
-- Check if columns exist before adding:
SET @dbname = DATABASE();
SET @tablename = 'invoices';

-- Add manual column if not exists
SET @col_exists = (SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = @dbname AND table_name = @tablename AND column_name = 'manual');
SET @sql = IF(@col_exists = 0, 'ALTER TABLE invoices ADD COLUMN manual BOOLEAN DEFAULT FALSE', 'SELECT 1');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Add notes column if not exists
SET @col_exists = (SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = @dbname AND table_name = @tablename AND column_name = 'notes');
SET @sql = IF(@col_exists = 0, 'ALTER TABLE invoices ADD COLUMN notes TEXT NULL', 'SELECT 1');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Add created_by_admin_id column if not exists
SET @col_exists = (SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = @dbname AND table_name = @tablename AND column_name = 'created_by_admin_id');
SET @sql = IF(@col_exists = 0, 'ALTER TABLE invoices ADD COLUMN created_by_admin_id INT NULL', 'SELECT 1');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Add foreign key if not exists (check constraint name)
SET @fk_exists = (SELECT COUNT(*) FROM information_schema.table_constraints WHERE table_schema = @dbname AND table_name = @tablename AND constraint_name = 'fk_invoices_created_by_admin');
SET @sql = IF(@fk_exists = 0, 'ALTER TABLE invoices ADD CONSTRAINT fk_invoices_created_by_admin FOREIGN KEY (created_by_admin_id) REFERENCES admin_users(id) ON DELETE SET NULL', 'SELECT 1');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Insert default billing settings
INSERT INTO billing_settings (setting_key, setting_value, updated_by_admin_id) VALUES
    ('support_refund_limit_cents', '5000', NULL),
    ('default_credit_expiry_days', '365', NULL),
    ('require_refund_reason', 'true', NULL),
    ('require_credit_reason', 'true', NULL),
    ('enable_manual_invoices', 'true', NULL)
ON DUPLICATE KEY UPDATE setting_key = setting_key;
