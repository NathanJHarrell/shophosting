-- Migration: Add provisioning_logs table for detailed provisioning progress tracking
-- Run this to add persistent logging for provisioning jobs

USE shophosting_db;

-- Create provisioning_logs table
CREATE TABLE IF NOT EXISTS provisioning_logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    job_id VARCHAR(100) NOT NULL,
    customer_id INT NOT NULL,
    log_level ENUM('INFO', 'WARNING', 'ERROR', 'DEBUG') DEFAULT 'INFO',
    message TEXT NOT NULL,
    step_name VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_job_id (job_id),
    INDEX idx_customer_id (customer_id),
    INDEX idx_created_at (created_at),
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Add indexes to provisioning_jobs for better query performance
CREATE INDEX idx_provisioning_jobs_status ON provisioning_jobs(status);
CREATE INDEX idx_provisioning_jobs_created_at ON provisioning_jobs(created_at);
