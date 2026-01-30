-- Migration: 012_add_settings_phase2_tables.sql
-- Description: Add tables for API keys, webhooks, notifications, and data exports
-- Date: 2026-01-29

-- Customer notification preferences
CREATE TABLE IF NOT EXISTS customer_notification_settings (
    id INT PRIMARY KEY AUTO_INCREMENT,
    customer_id INT NOT NULL UNIQUE,
    email_security_alerts BOOLEAN DEFAULT TRUE,
    email_login_alerts BOOLEAN DEFAULT TRUE,
    email_billing_alerts BOOLEAN DEFAULT TRUE,
    email_maintenance_alerts BOOLEAN DEFAULT TRUE,
    email_marketing BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- API keys for programmatic access
CREATE TABLE IF NOT EXISTS customer_api_keys (
    id INT PRIMARY KEY AUTO_INCREMENT,
    customer_id INT NOT NULL,
    name VARCHAR(100) NOT NULL,
    key_prefix VARCHAR(8) NOT NULL,
    key_hash VARCHAR(64) NOT NULL,
    scopes TEXT,
    last_used_at TIMESTAMP NULL,
    expires_at TIMESTAMP NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
    INDEX idx_key_prefix (key_prefix),
    INDEX idx_customer_active (customer_id, is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Webhooks for event notifications
CREATE TABLE IF NOT EXISTS customer_webhooks (
    id INT PRIMARY KEY AUTO_INCREMENT,
    customer_id INT NOT NULL,
    name VARCHAR(100) NOT NULL,
    url VARCHAR(500) NOT NULL,
    secret VARCHAR(64) NOT NULL,
    events TEXT NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    failure_count INT DEFAULT 0,
    last_triggered_at TIMESTAMP NULL,
    last_success_at TIMESTAMP NULL,
    last_failure_at TIMESTAMP NULL,
    last_failure_reason VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
    INDEX idx_customer_active (customer_id, is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Data export requests (GDPR)
CREATE TABLE IF NOT EXISTS customer_data_exports (
    id INT PRIMARY KEY AUTO_INCREMENT,
    customer_id INT NOT NULL,
    status ENUM('pending', 'processing', 'completed', 'failed', 'expired') DEFAULT 'pending',
    file_path VARCHAR(255),
    file_size_bytes BIGINT,
    expires_at TIMESTAMP NULL,
    error_message VARCHAR(255),
    requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
    INDEX idx_customer_status (customer_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Account deletion requests
CREATE TABLE IF NOT EXISTS customer_deletion_requests (
    id INT PRIMARY KEY AUTO_INCREMENT,
    customer_id INT NOT NULL UNIQUE,
    reason TEXT,
    scheduled_at TIMESTAMP NOT NULL,
    cancelled_at TIMESTAMP NULL,
    executed_at TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Add timezone to customers table
SET @column_exists = (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'customers' AND COLUMN_NAME = 'timezone');
SET @sql = IF(@column_exists = 0, 'ALTER TABLE customers ADD COLUMN timezone VARCHAR(50) DEFAULT ''America/New_York''', 'SELECT 1');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
