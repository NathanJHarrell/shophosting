-- Migration: Add staging environments support
-- Run: mysql -u root -p shophosting_db < migrations/006_add_staging_environments.sql

USE shophosting_db;

-- Staging environments table
CREATE TABLE IF NOT EXISTS staging_environments (
    id INT AUTO_INCREMENT PRIMARY KEY,
    customer_id INT NOT NULL,
    name VARCHAR(100) NOT NULL,
    staging_domain VARCHAR(255) NOT NULL UNIQUE,
    status ENUM('creating', 'active', 'syncing', 'failed', 'deleted') DEFAULT 'creating',
    web_port INT UNIQUE,

    -- Container database credentials
    db_name VARCHAR(100),
    db_user VARCHAR(100),
    db_password VARCHAR(255),

    -- Tracking
    source_snapshot_date TIMESTAMP NULL,
    last_push_date TIMESTAMP NULL,

    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    -- Foreign keys and indexes
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
    INDEX idx_customer_id (customer_id),
    INDEX idx_status (status),
    INDEX idx_staging_domain (staging_domain)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Staging sync history table
CREATE TABLE IF NOT EXISTS staging_sync_history (
    id INT AUTO_INCREMENT PRIMARY KEY,
    staging_id INT NOT NULL,
    sync_type ENUM('create', 'push_files', 'push_db', 'push_all', 'pull_files', 'pull_db', 'pull_all') NOT NULL,
    status ENUM('pending', 'running', 'completed', 'failed') DEFAULT 'pending',
    started_at TIMESTAMP NULL,
    completed_at TIMESTAMP NULL,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (staging_id) REFERENCES staging_environments(id) ON DELETE CASCADE,
    INDEX idx_staging_id (staging_id),
    INDEX idx_status (status),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Add staging_count to customers for quick lookup (optional optimization)
-- Using a procedure to handle "column already exists" gracefully
DROP PROCEDURE IF EXISTS add_staging_count_column;
DELIMITER //
CREATE PROCEDURE add_staging_count_column()
BEGIN
    DECLARE CONTINUE HANDLER FOR 1060 BEGIN END;
    ALTER TABLE customers ADD COLUMN staging_count INT DEFAULT 0;
END //
DELIMITER ;
CALL add_staging_count_column();
DROP PROCEDURE IF EXISTS add_staging_count_column;
