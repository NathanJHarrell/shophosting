-- Migration: Add multi-server support
-- Run: mysql -u shophosting_app -p shophosting_db < migrations/008_add_servers_table.sql

USE shophosting_db;

-- =============================================================================
-- Servers table - tracks available provisioning servers
-- =============================================================================

CREATE TABLE IF NOT EXISTS servers (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    hostname VARCHAR(255) NOT NULL UNIQUE,
    ip_address VARCHAR(45) NOT NULL,
    status ENUM('active', 'maintenance', 'offline') DEFAULT 'active',

    -- Capacity settings
    max_customers INT DEFAULT 50,
    port_range_start INT DEFAULT 8001,
    port_range_end INT DEFAULT 8100,

    -- Worker configuration
    redis_queue_name VARCHAR(100),

    -- Health monitoring
    last_heartbeat TIMESTAMP NULL,

    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    -- Indexes
    INDEX idx_status (status),
    INDEX idx_last_heartbeat (last_heartbeat)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================================
-- Add server_id to customers table
-- =============================================================================

-- Add server_id column if it doesn't exist
SET @column_exists = (
    SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = 'shophosting_db'
    AND TABLE_NAME = 'customers'
    AND COLUMN_NAME = 'server_id'
);

SET @sql = IF(@column_exists = 0,
    'ALTER TABLE customers ADD COLUMN server_id INT NULL AFTER web_port',
    'SELECT "server_id column already exists"');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Add foreign key constraint if it doesn't exist
SET @fk_exists = (
    SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS
    WHERE CONSTRAINT_SCHEMA = 'shophosting_db'
    AND TABLE_NAME = 'customers'
    AND CONSTRAINT_NAME = 'fk_customers_server'
);

SET @sql = IF(@fk_exists = 0,
    'ALTER TABLE customers ADD CONSTRAINT fk_customers_server FOREIGN KEY (server_id) REFERENCES servers(id) ON DELETE SET NULL',
    'SELECT "foreign key already exists"');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Add index on server_id for faster lookups
SET @index_exists = (
    SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = 'shophosting_db'
    AND TABLE_NAME = 'customers'
    AND INDEX_NAME = 'idx_server_id'
);

SET @sql = IF(@index_exists = 0,
    'ALTER TABLE customers ADD INDEX idx_server_id (server_id)',
    'SELECT "index already exists"');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- =============================================================================
-- Add server_id to provisioning_jobs table for tracking
-- =============================================================================

SET @column_exists = (
    SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = 'shophosting_db'
    AND TABLE_NAME = 'provisioning_jobs'
    AND COLUMN_NAME = 'server_id'
);

SET @sql = IF(@column_exists = 0,
    'ALTER TABLE provisioning_jobs ADD COLUMN server_id INT NULL AFTER customer_id',
    'SELECT "server_id column already exists in provisioning_jobs"');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- =============================================================================
-- Insert default server (current server as "Primary")
-- This ensures backward compatibility - all existing customers will use server 1
-- =============================================================================

INSERT INTO servers (name, hostname, ip_address, status, max_customers, port_range_start, port_range_end, redis_queue_name)
SELECT 'Primary', 'localhost', '127.0.0.1', 'active', 50, 8001, 8100, 'provisioning'
WHERE NOT EXISTS (SELECT 1 FROM servers WHERE hostname = 'localhost');

-- Update existing customers to use the primary server
UPDATE customers SET server_id = 1 WHERE server_id IS NULL;
