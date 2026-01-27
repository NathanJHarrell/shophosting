-- Migration: Add consultation appointments table
-- Run: mysql -u root -p shophosting_db < migrations/007_add_consultations_table.sql

USE shophosting_db;

-- Consultation appointments (from scheduler form - prospects, not existing customers)
CREATE TABLE IF NOT EXISTS consultation_appointments (
    id INT AUTO_INCREMENT PRIMARY KEY,
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100) NOT NULL,
    email VARCHAR(255) NOT NULL,
    phone VARCHAR(50) NOT NULL,
    scheduled_date DATE NOT NULL,
    scheduled_time VARCHAR(10) NOT NULL,
    timezone VARCHAR(50) DEFAULT 'EST',
    status ENUM('pending', 'confirmed', 'completed', 'cancelled', 'no_show') DEFAULT 'pending',
    notes TEXT,
    assigned_admin_id INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    FOREIGN KEY (assigned_admin_id) REFERENCES admin_users(id) ON DELETE SET NULL,

    INDEX idx_status (status),
    INDEX idx_scheduled_date (scheduled_date),
    INDEX idx_email (email),
    INDEX idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
