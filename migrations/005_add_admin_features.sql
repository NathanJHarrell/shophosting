-- Migration: Add admin user management features
-- Run: mysql -u root -p shophosting_db < migrations/005_add_admin_features.sql

USE shophosting_db;

-- Add must_change_password column to admin_users
ALTER TABLE admin_users
    ADD COLUMN must_change_password BOOLEAN DEFAULT FALSE AFTER is_active;

-- Add index for faster queries
CREATE INDEX idx_admin_users_must_change_password ON admin_users(must_change_password);
CREATE INDEX idx_admin_users_role ON admin_users(role);
