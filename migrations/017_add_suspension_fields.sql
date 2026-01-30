-- Add suspension tracking fields to customers table
-- Supports automatic suspension when resource limits are exceeded

-- Add suspension reason field
SET @col_exists = (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'customers' AND COLUMN_NAME = 'suspension_reason');
SET @sql = IF(@col_exists = 0,
    'ALTER TABLE customers ADD COLUMN suspension_reason VARCHAR(255) NULL COMMENT ''Reason for suspension (resource_limit_exceeded, payment_failed, admin_action, etc)''',
    'SELECT ''Column suspension_reason already exists''');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Add suspended_at timestamp
SET @col_exists = (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'customers' AND COLUMN_NAME = 'suspended_at');
SET @sql = IF(@col_exists = 0,
    'ALTER TABLE customers ADD COLUMN suspended_at DATETIME NULL COMMENT ''When the suspension was applied''',
    'SELECT ''Column suspended_at already exists''');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Add auto_suspended flag to distinguish system vs admin suspensions
SET @col_exists = (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'customers' AND COLUMN_NAME = 'auto_suspended');
SET @sql = IF(@col_exists = 0,
    'ALTER TABLE customers ADD COLUMN auto_suspended BOOLEAN DEFAULT FALSE COMMENT ''TRUE if suspended by system (resource limits), FALSE if by admin''',
    'SELECT ''Column auto_suspended already exists''');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Add reactivated_at for tracking when customers are unsuspended
SET @col_exists = (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'customers' AND COLUMN_NAME = 'reactivated_at');
SET @sql = IF(@col_exists = 0,
    'ALTER TABLE customers ADD COLUMN reactivated_at DATETIME NULL COMMENT ''When the customer was last reactivated''',
    'SELECT ''Column reactivated_at already exists''');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Create table to log suspension history for auditing
CREATE TABLE IF NOT EXISTS customer_suspension_log (
    id INT AUTO_INCREMENT PRIMARY KEY,
    customer_id INT NOT NULL,
    action ENUM('suspended', 'reactivated') NOT NULL,
    reason VARCHAR(255) NULL,
    auto_action BOOLEAN DEFAULT FALSE COMMENT 'TRUE if action was automatic',
    actor_id INT NULL COMMENT 'Admin user ID if manual action, NULL if automatic',
    disk_usage_bytes BIGINT NULL COMMENT 'Disk usage at time of action',
    bandwidth_usage_bytes BIGINT NULL COMMENT 'Bandwidth usage at time of action',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_customer_id (customer_id),
    INDEX idx_created_at (created_at),
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Add index for efficient status lookups
SET @idx_exists = (SELECT COUNT(*) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'customers' AND INDEX_NAME = 'idx_status_auto_suspended');
SET @sql = IF(@idx_exists = 0,
    'ALTER TABLE customers ADD INDEX idx_status_auto_suspended (status, auto_suspended)',
    'SELECT ''Index idx_status_auto_suspended already exists''');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
