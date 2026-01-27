-- CMS Tables Migration
-- Run: mysql -u root -p shophosting_db < /opt/shophosting/migrations/006_add_cms_tables.sql

USE shophosting_db;

CREATE TABLE IF NOT EXISTS page_content (
    id INT AUTO_INCREMENT PRIMARY KEY,
    page_slug VARCHAR(50) NOT NULL UNIQUE,
    title VARCHAR(255),
    content LONGTEXT NOT NULL,
    is_published BOOLEAN DEFAULT FALSE,
    published_at TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_page_slug (page_slug),
    INDEX idx_is_published (is_published)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS page_versions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    page_id INT NOT NULL,
    content LONGTEXT NOT NULL,
    changed_by_admin_id INT NULL,
    change_summary VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (page_id) REFERENCES page_content(id) ON DELETE CASCADE,
    FOREIGN KEY (changed_by_admin_id) REFERENCES admin_users(id) ON DELETE SET NULL,
    INDEX idx_page_id (page_id),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO page_content (page_slug, title, content, is_published) VALUES
('home', 'Homepage', '{"hero":{"headline":"Your Store. Our Servers. Zero Headaches.","subheadline":"Enterprise-grade WooCommerce and Magento hosting powered by custom Docker containers.","cta_text":"Start Free Consultation","cta_link":"/signup"},"stats":{"stores_count":"100+","uptime":"99.9%","hours_saved":"5000+"},"features":[],"platforms":[],"cta":{"headline":"Ready to Scale?","subheadline":"Book a free consultation with our hosting architects.","button_text":"Get Started","button_link":"/signup"}}', TRUE),
('pricing', 'Pricing', '{"header":{"headline":"One Price. Everything Included.","subheadline":"Transparent pricing with no hidden fees. All plans include enterprise features."},"woocommerce":{"plans":[1,2,3,4,5,6],"comparison_features":["Daily Backups","Email Support","Premium Plugins","24/7 Support","Redis Cache","Staging Environment","SLA Uptime Guarantee","Advanced Security","Centralized Management","White Label","Dedicated Support"]},"magento":{"plans":[7,8,9,10,11,12],"comparison_features":["Daily Backups","Email Support","24/7 Support","Redis Cache","Staging Environment","SLA Uptime Guarantee","Advanced Security","Centralized Management","White Label","Dedicated Support"]},"faq":[]}', TRUE),
('features', 'Features', '{"hero":{"headline":"Built for Performance","subheadline":"Every aspect of our platform is engineered for speed, reliability, and security."},"features_grid":[],"security_section":{},"performance_section":{}}', TRUE),
('about', 'About', '{"hero":{"headline":"Our Mission","subheadline":"Democratizing enterprise-grade e-commerce hosting."},"story_section":{},"values_section":{},"team_section":{}}', TRUE),
('contact', 'Contact', '{"hero":{"headline":"Get in Touch","subheadline":"Have questions? Our team is here to help."},"contact_info":{},"form_section":{}}', TRUE);
