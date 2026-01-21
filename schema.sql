-- ShopHosting.io Database Schema
-- Run this file to initialize the database:
-- mysql -u root -p < /opt/shophosting.io/schema.sql

-- Create database if not exists
CREATE DATABASE IF NOT EXISTS shophosting_db
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE shophosting_db;

-- Create application user if not exists
-- Note: Run these commands as MySQL root user
-- CREATE USER IF NOT EXISTS 'shophosting_app'@'localhost' IDENTIFIED BY 'YourSecurePasswordHere123!';
-- GRANT ALL PRIVILEGES ON shophosting_db.* TO 'shophosting_app'@'localhost';
-- FLUSH PRIVILEGES;

-- Customers table
CREATE TABLE IF NOT EXISTS customers (
    id INT AUTO_INCREMENT PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    company_name VARCHAR(255) NOT NULL,
    domain VARCHAR(255) NOT NULL UNIQUE,
    platform ENUM('woocommerce', 'magento') NOT NULL,
    status ENUM('pending', 'provisioning', 'active', 'suspended', 'failed') DEFAULT 'pending',
    web_port INT UNIQUE,

    -- Container database credentials (populated during provisioning)
    db_name VARCHAR(100),
    db_user VARCHAR(100),
    db_password VARCHAR(255),

    -- Store admin credentials (populated during provisioning)
    admin_user VARCHAR(100),
    admin_password VARCHAR(255),

    -- Error tracking
    error_message TEXT,

    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    -- Indexes
    INDEX idx_email (email),
    INDEX idx_domain (domain),
    INDEX idx_status (status),
    INDEX idx_web_port (web_port)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Provisioning jobs tracking (optional - for monitoring)
CREATE TABLE IF NOT EXISTS provisioning_jobs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    customer_id INT NOT NULL,
    job_id VARCHAR(100) NOT NULL,
    status ENUM('queued', 'started', 'finished', 'failed') DEFAULT 'queued',
    started_at TIMESTAMP NULL,
    finished_at TIMESTAMP NULL,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
    INDEX idx_job_id (job_id),
    INDEX idx_customer_id (customer_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Audit log (optional - for tracking changes)
CREATE TABLE IF NOT EXISTS audit_log (
    id INT AUTO_INCREMENT PRIMARY KEY,
    customer_id INT,
    action VARCHAR(100) NOT NULL,
    details TEXT,
    ip_address VARCHAR(45),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE SET NULL,
    INDEX idx_customer_id (customer_id),
    INDEX idx_action (action),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
