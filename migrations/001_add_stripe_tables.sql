-- Stripe Payment Integration Migration
-- Run: mysql -u root -p shophosting_db < /opt/shophosting.io/migrations/001_add_stripe_tables.sql

USE shophosting_db;

-- Update customers status enum to include pending_payment
ALTER TABLE customers
    MODIFY COLUMN status ENUM('pending', 'pending_payment', 'provisioning', 'active', 'suspended', 'failed') DEFAULT 'pending';

-- Add stripe_customer_id to customers table
ALTER TABLE customers
    ADD COLUMN stripe_customer_id VARCHAR(100) UNIQUE AFTER error_message,
    ADD COLUMN plan_id INT AFTER stripe_customer_id,
    ADD INDEX idx_stripe_customer (stripe_customer_id);

-- Pricing Plans Table
CREATE TABLE IF NOT EXISTS pricing_plans (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    slug VARCHAR(50) NOT NULL UNIQUE,
    platform ENUM('woocommerce', 'magento') NOT NULL,
    tier_type ENUM('single', 'multi') NOT NULL,
    price_monthly DECIMAL(10,2) NOT NULL,
    store_limit INT NOT NULL DEFAULT 1,

    -- Stripe identifiers
    stripe_product_id VARCHAR(100),
    stripe_price_id VARCHAR(100),

    -- Features JSON
    features JSON,

    -- Resource limits
    memory_limit VARCHAR(10) DEFAULT '1g',
    cpu_limit VARCHAR(10) DEFAULT '1.0',

    -- Metadata
    is_active BOOLEAN DEFAULT TRUE,
    display_order INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_platform (platform),
    INDEX idx_active (is_active),
    INDEX idx_stripe_price (stripe_price_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Subscriptions Table
CREATE TABLE IF NOT EXISTS subscriptions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    customer_id INT NOT NULL,
    plan_id INT NOT NULL,

    -- Stripe identifiers
    stripe_subscription_id VARCHAR(100) NOT NULL UNIQUE,
    stripe_customer_id VARCHAR(100) NOT NULL,

    -- Subscription status
    status ENUM('incomplete', 'incomplete_expired', 'trialing', 'active',
                'past_due', 'canceled', 'unpaid', 'paused') NOT NULL DEFAULT 'incomplete',

    -- Billing dates
    current_period_start TIMESTAMP NULL,
    current_period_end TIMESTAMP NULL,
    cancel_at TIMESTAMP NULL,
    canceled_at TIMESTAMP NULL,

    -- Metadata
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
    FOREIGN KEY (plan_id) REFERENCES pricing_plans(id),
    INDEX idx_customer (customer_id),
    INDEX idx_stripe_sub (stripe_subscription_id),
    INDEX idx_stripe_cust (stripe_customer_id),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Invoices Table
CREATE TABLE IF NOT EXISTS invoices (
    id INT AUTO_INCREMENT PRIMARY KEY,
    customer_id INT NOT NULL,
    subscription_id INT,

    -- Stripe identifiers
    stripe_invoice_id VARCHAR(100) NOT NULL UNIQUE,
    stripe_payment_intent_id VARCHAR(100),

    -- Invoice details
    amount_due INT NOT NULL,
    amount_paid INT NOT NULL DEFAULT 0,
    currency VARCHAR(3) DEFAULT 'usd',
    status ENUM('draft', 'open', 'paid', 'uncollectible', 'void') NOT NULL,

    -- URLs
    invoice_pdf_url TEXT,
    hosted_invoice_url TEXT,

    -- Dates
    period_start TIMESTAMP NULL,
    period_end TIMESTAMP NULL,
    paid_at TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
    FOREIGN KEY (subscription_id) REFERENCES subscriptions(id) ON DELETE SET NULL,
    INDEX idx_customer (customer_id),
    INDEX idx_stripe_invoice (stripe_invoice_id),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Webhook Events Table (for idempotency)
CREATE TABLE IF NOT EXISTS stripe_webhook_events (
    id INT AUTO_INCREMENT PRIMARY KEY,
    stripe_event_id VARCHAR(100) NOT NULL UNIQUE,
    event_type VARCHAR(100) NOT NULL,
    payload JSON,
    processed BOOLEAN DEFAULT FALSE,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMP NULL,

    INDEX idx_event_id (stripe_event_id),
    INDEX idx_event_type (event_type),
    INDEX idx_processed (processed)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Insert pricing plans (Stripe IDs will be updated after creating products in Stripe)
-- WordPress Plans
INSERT INTO pricing_plans (name, slug, platform, tier_type, price_monthly, store_limit, features, memory_limit, cpu_limit, display_order) VALUES
('Commerce Core', 'wp-commerce-core', 'woocommerce', 'single', 99.00, 1,
 '{"daily_backups": true, "email_support": true, "premium_plugins": false, "support_24_7": false, "redis_cache": false, "staging": false}',
 '1g', '1.0', 1),
('Commerce Pro', 'wp-commerce-pro', 'woocommerce', 'single', 149.00, 1,
 '{"daily_backups": true, "email_support": true, "premium_plugins": true, "support_24_7": true, "redis_cache": true, "staging": true}',
 '2g', '2.0', 2),
('Commerce Scale', 'wp-commerce-scale', 'woocommerce', 'single', 249.00, 1,
 '{"daily_backups": true, "email_support": true, "premium_plugins": true, "support_24_7": true, "redis_cache": true, "staging": true, "sla_uptime": true, "advanced_security": true}',
 '4g', '4.0', 3),
('Multi-Store', 'wp-multi-store', 'woocommerce', 'multi', 399.00, 5,
 '{"daily_backups": true, "email_support": true, "premium_plugins": false, "support_24_7": false, "centralized_management": true}',
 '2g', '2.0', 4),
('Agency', 'wp-agency', 'woocommerce', 'multi', 749.00, 10,
 '{"daily_backups": true, "email_support": true, "premium_plugins": true, "support_24_7": true, "centralized_management": true, "white_label": true}',
 '4g', '4.0', 5),
('Agency Plus', 'wp-agency-plus', 'woocommerce', 'multi', 1299.00, 20,
 '{"daily_backups": true, "email_support": true, "premium_plugins": true, "support_24_7": true, "centralized_management": true, "white_label": true, "dedicated_support": true}',
 '8g', '8.0', 6);

-- Magento Plans (~25% higher)
INSERT INTO pricing_plans (name, slug, platform, tier_type, price_monthly, store_limit, features, memory_limit, cpu_limit, display_order) VALUES
('Commerce Core', 'mg-commerce-core', 'magento', 'single', 125.00, 1,
 '{"daily_backups": true, "email_support": true, "premium_plugins": false, "support_24_7": false, "redis_cache": false, "staging": false}',
 '2g', '1.0', 1),
('Commerce Pro', 'mg-commerce-pro', 'magento', 'single', 189.00, 1,
 '{"daily_backups": true, "email_support": true, "premium_plugins": true, "support_24_7": true, "redis_cache": true, "staging": true}',
 '4g', '2.0', 2),
('Commerce Scale', 'mg-commerce-scale', 'magento', 'single', 319.00, 1,
 '{"daily_backups": true, "email_support": true, "premium_plugins": true, "support_24_7": true, "redis_cache": true, "staging": true, "sla_uptime": true, "advanced_security": true}',
 '8g', '4.0', 3),
('Multi-Store', 'mg-multi-store', 'magento', 'multi', 499.00, 5,
 '{"daily_backups": true, "email_support": true, "premium_plugins": false, "support_24_7": false, "centralized_management": true}',
 '4g', '2.0', 4),
('Agency', 'mg-agency', 'magento', 'multi', 949.00, 10,
 '{"daily_backups": true, "email_support": true, "premium_plugins": true, "support_24_7": true, "centralized_management": true, "white_label": true}',
 '8g', '4.0', 5),
('Agency Plus', 'mg-agency-plus', 'magento', 'multi', 1649.00, 20,
 '{"daily_backups": true, "email_support": true, "premium_plugins": true, "support_24_7": true, "centralized_management": true, "white_label": true, "dedicated_support": true}',
 '16g', '8.0', 6);
