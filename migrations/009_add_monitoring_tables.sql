-- Migration 009: Add Monitoring Tables
-- Adds tables for site monitoring, status tracking, and alerting

-- Monitoring check results (keeps last 24-48 hours of data)
CREATE TABLE IF NOT EXISTS monitoring_checks (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    customer_id INT NOT NULL,
    check_type ENUM('http', 'container', 'resources') NOT NULL,
    status ENUM('up', 'down', 'degraded', 'unknown') NOT NULL,
    response_time_ms INT NULL,
    details JSON NULL,
    checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_customer_checked (customer_id, checked_at),
    INDEX idx_checked_at (checked_at),
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
);

-- Customer monitoring status (current state - one row per customer)
CREATE TABLE IF NOT EXISTS customer_monitoring_status (
    customer_id INT PRIMARY KEY,
    http_status ENUM('up', 'down', 'degraded', 'unknown') DEFAULT 'unknown',
    container_status ENUM('up', 'down', 'degraded', 'unknown') DEFAULT 'unknown',
    last_http_check TIMESTAMP NULL,
    last_container_check TIMESTAMP NULL,
    last_http_response_ms INT NULL,
    cpu_percent DECIMAL(5,2) NULL,
    memory_percent DECIMAL(5,2) NULL,
    memory_usage_mb INT NULL,
    disk_usage_mb INT NULL,
    uptime_24h DECIMAL(5,2) DEFAULT 0.00,
    consecutive_failures INT DEFAULT 0,
    last_state_change TIMESTAMP NULL,
    last_alert_sent TIMESTAMP NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
);

-- Alert history
CREATE TABLE IF NOT EXISTS monitoring_alerts (
    id INT AUTO_INCREMENT PRIMARY KEY,
    customer_id INT NOT NULL,
    alert_type ENUM('down', 'degraded', 'recovered', 'resource_warning') NOT NULL,
    message TEXT NOT NULL,
    details JSON NULL,
    email_sent BOOLEAN DEFAULT FALSE,
    acknowledged BOOLEAN DEFAULT FALSE,
    acknowledged_by INT NULL,
    acknowledged_at TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_customer_created (customer_id, created_at),
    INDEX idx_unacknowledged (acknowledged, created_at),
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
    FOREIGN KEY (acknowledged_by) REFERENCES admin_users(id)
);
