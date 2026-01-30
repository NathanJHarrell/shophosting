-- Migration: 014_add_cloudflare_tables.sql
-- Description: Add tables for Cloudflare DNS integration
-- Created: 2026-01-30

-- Table: customer_cloudflare_connections
-- Stores OAuth tokens for customer Cloudflare accounts
CREATE TABLE IF NOT EXISTS customer_cloudflare_connections (
    id INT AUTO_INCREMENT PRIMARY KEY,
    customer_id INT NOT NULL UNIQUE,
    cloudflare_zone_id VARCHAR(50) NULL,
    access_token TEXT NOT NULL,
    refresh_token TEXT NULL,
    token_expires_at DATETIME NULL,
    connected_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_sync_at DATETIME NULL,
    CONSTRAINT fk_cloudflare_customer_id FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
    INDEX idx_cloudflare_customer_id (customer_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Table: dns_records_cache
-- Caches DNS records from Cloudflare for fast display
CREATE TABLE IF NOT EXISTS dns_records_cache (
    id INT AUTO_INCREMENT PRIMARY KEY,
    customer_id INT NOT NULL,
    cloudflare_record_id VARCHAR(50) NOT NULL,
    record_type ENUM('A', 'CNAME', 'MX', 'TXT') NOT NULL,
    name VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    priority INT NULL,
    proxied BOOLEAN NOT NULL DEFAULT FALSE,
    ttl INT NOT NULL DEFAULT 1,
    synced_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_dns_cache_customer_id FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
    INDEX idx_dns_cache_customer_id (customer_id),
    UNIQUE KEY uk_cloudflare_record_id (cloudflare_record_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
