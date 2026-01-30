-- Migration tracking table
-- This table tracks which migrations have been applied to prevent re-running

CREATE TABLE IF NOT EXISTS schema_migrations (
    id INT AUTO_INCREMENT PRIMARY KEY,
    filename VARCHAR(255) NOT NULL UNIQUE,
    checksum VARCHAR(64) NOT NULL COMMENT 'SHA256 hash of migration file contents',
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    applied_by VARCHAR(100) DEFAULT NULL COMMENT 'User or process that applied the migration',
    execution_time_ms INT DEFAULT NULL COMMENT 'How long the migration took to run',
    INDEX idx_filename (filename),
    INDEX idx_applied_at (applied_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
