-- Migration: 011_add_settings_security_tables.sql
-- Description: Add tables for 2FA, login history, and verification tokens
-- Date: 2026-01-29

-- Add password_changed_at to customers table (ignore error if already exists)
SET @column_exists = (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'customers' AND COLUMN_NAME = 'password_changed_at');
SET @sql = IF(@column_exists = 0, 'ALTER TABLE customers ADD COLUMN password_changed_at TIMESTAMP NULL', 'SELECT 1');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 2FA settings per customer
CREATE TABLE IF NOT EXISTS customer_2fa_settings (
    id INT PRIMARY KEY AUTO_INCREMENT,
    customer_id INT NOT NULL UNIQUE,
    totp_secret VARCHAR(32),
    is_enabled BOOLEAN DEFAULT FALSE,
    backup_codes TEXT,
    backup_codes_remaining INT DEFAULT 10,
    last_used_at TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Login history for audit trail and session display
CREATE TABLE IF NOT EXISTS customer_login_history (
    id INT PRIMARY KEY AUTO_INCREMENT,
    customer_id INT NOT NULL,
    ip_address VARCHAR(45),
    user_agent VARCHAR(500),
    location VARCHAR(100),
    success BOOLEAN DEFAULT TRUE,
    failure_reason VARCHAR(100),
    session_id VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
    INDEX idx_customer_created (customer_id, created_at DESC),
    INDEX idx_session (session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Verification tokens for 2FA email recovery and future email change
CREATE TABLE IF NOT EXISTS customer_verification_tokens (
    id INT PRIMARY KEY AUTO_INCREMENT,
    customer_id INT NOT NULL,
    token VARCHAR(64) NOT NULL UNIQUE,
    token_type ENUM('2fa_recovery', 'email_change', 'password_reset') NOT NULL,
    new_value VARCHAR(255),
    expires_at TIMESTAMP NOT NULL,
    used_at TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
    INDEX idx_token (token),
    INDEX idx_customer_type (customer_id, token_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
