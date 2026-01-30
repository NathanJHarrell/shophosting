-- Migration: Create customer_backup_jobs table
-- Date: 2026-01-27

CREATE TABLE IF NOT EXISTS customer_backup_jobs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    customer_id INT NOT NULL,
    job_type ENUM('backup', 'restore') NOT NULL,
    backup_type ENUM('db', 'files', 'both') NOT NULL,
    snapshot_id VARCHAR(64) NULL,
    status ENUM('pending', 'running', 'completed', 'failed') DEFAULT 'pending',
    error_message TEXT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
    INDEX idx_customer_status (customer_id, status),
    INDEX idx_created_at (created_at)
);
