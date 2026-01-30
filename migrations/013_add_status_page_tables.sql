-- Migration: 013_add_status_page_tables.sql
-- Description: Add tables for public status page feature
-- Created: 2026-01-29

-- Table: status_incidents
-- Tracks outages and issues affecting services
CREATE TABLE IF NOT EXISTS status_incidents (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    server_id INT UNSIGNED NULL,
    title VARCHAR(255) NOT NULL,
    status ENUM('investigating', 'identified', 'monitoring', 'resolved') NOT NULL DEFAULT 'investigating',
    severity ENUM('minor', 'major', 'critical') NOT NULL DEFAULT 'minor',
    is_auto_detected BOOLEAN NOT NULL DEFAULT FALSE,
    started_at DATETIME NOT NULL,
    resolved_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_status_incidents_server_id FOREIGN KEY (server_id) REFERENCES servers(id) ON DELETE SET NULL,
    INDEX idx_status_incidents_status_started (status, started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Table: status_incident_updates
-- Timeline updates for incidents
CREATE TABLE IF NOT EXISTS status_incident_updates (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    incident_id INT UNSIGNED NOT NULL,
    status ENUM('investigating', 'identified', 'monitoring', 'resolved') NOT NULL,
    message TEXT NOT NULL,
    created_by INT UNSIGNED NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_status_incident_updates_incident_id FOREIGN KEY (incident_id) REFERENCES status_incidents(id) ON DELETE CASCADE,
    CONSTRAINT fk_status_incident_updates_created_by FOREIGN KEY (created_by) REFERENCES admin_users(id) ON DELETE SET NULL,
    INDEX idx_status_incident_updates_incident_id (incident_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Table: status_maintenance
-- Scheduled maintenance windows
CREATE TABLE IF NOT EXISTS status_maintenance (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    server_id INT UNSIGNED NULL,
    title VARCHAR(255) NOT NULL,
    description TEXT NULL,
    scheduled_start DATETIME NOT NULL,
    scheduled_end DATETIME NOT NULL,
    status ENUM('scheduled', 'in_progress', 'completed', 'cancelled') NOT NULL DEFAULT 'scheduled',
    created_by INT UNSIGNED NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_status_maintenance_server_id FOREIGN KEY (server_id) REFERENCES servers(id) ON DELETE SET NULL,
    CONSTRAINT fk_status_maintenance_created_by FOREIGN KEY (created_by) REFERENCES admin_users(id) ON DELETE SET NULL,
    INDEX idx_status_maintenance_scheduled (scheduled_start, scheduled_end, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Table: status_overrides
-- Manual status overrides for services
CREATE TABLE IF NOT EXISTS status_overrides (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    service_name VARCHAR(100) NOT NULL UNIQUE,
    display_status ENUM('operational', 'degraded', 'partial_outage', 'major_outage', 'maintenance') NOT NULL,
    message TEXT NULL,
    created_by INT UNSIGNED NULL,
    expires_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_status_overrides_created_by FOREIGN KEY (created_by) REFERENCES admin_users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
