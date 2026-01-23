"""
Admin Panel Models
Handles admin user authentication and database operations
"""

import os
import sys
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

# Add parent directory for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models import get_db_connection


class AdminUser:
    """Admin user model for admin panel authentication"""

    def __init__(self, id=None, email=None, password_hash=None, full_name=None,
                 role='admin', is_active=True, must_change_password=False, last_login_at=None,
                 created_at=None, updated_at=None):
        self.id = id
        self.email = email
        self.password_hash = password_hash
        self.full_name = full_name
        self.role = role
        self.is_active = is_active
        self.must_change_password = must_change_password
        self.last_login_at = last_login_at
        self.created_at = created_at
        self.updated_at = updated_at

    def set_password(self, password):
        """Hash and set password"""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        """Verify password against hash"""
        return check_password_hash(self.password_hash, password)

    def save(self):
        """Insert or update admin user in database"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            if self.id:
                cursor.execute("""
                    UPDATE admin_users SET
                        email = %s, password_hash = %s, full_name = %s,
                        role = %s, is_active = %s, must_change_password = %s, last_login_at = %s
                    WHERE id = %s
                """, (self.email, self.password_hash, self.full_name,
                      self.role, self.is_active, self.must_change_password, self.last_login_at, self.id))
            else:
                cursor.execute("""
                    INSERT INTO admin_users (email, password_hash, full_name, role, is_active, must_change_password)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (self.email, self.password_hash, self.full_name, self.role, self.is_active, self.must_change_password))
                self.id = cursor.lastrowid

            conn.commit()
            return self.id
        finally:
            cursor.close()
            conn.close()

    def update_last_login(self):
        """Update last login timestamp"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute(
                "UPDATE admin_users SET last_login_at = NOW() WHERE id = %s",
                (self.id,)
            )
            conn.commit()
            self.last_login_at = datetime.now()
        finally:
            cursor.close()
            conn.close()

    def to_dict(self):
        """Convert to dictionary"""
        return {
            'id': self.id,
            'email': self.email,
            'full_name': self.full_name,
            'role': self.role,
            'is_active': self.is_active,
            'must_change_password': self.must_change_password,
            'last_login_at': self.last_login_at.isoformat() if self.last_login_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

    @staticmethod
    def get_by_id(admin_id):
        """Get admin user by ID"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("SELECT * FROM admin_users WHERE id = %s", (admin_id,))
            row = cursor.fetchone()
            if row:
                return AdminUser(**row)
            return None
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_by_email(email):
        """Get admin user by email"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("SELECT * FROM admin_users WHERE email = %s", (email,))
            row = cursor.fetchone()
            if row:
                return AdminUser(**row)
            return None
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_all():
        """Get all admin users"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("SELECT * FROM admin_users ORDER BY created_at DESC")
            rows = cursor.fetchall()
            return [AdminUser(**row) for row in rows]
        finally:
            cursor.close()
            conn.close()


def log_admin_action(admin_id, action, entity_type=None, entity_id=None, details=None, ip_address=None):
    """Log admin action to audit_log table"""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            INSERT INTO audit_log
            (admin_user_id, action, entity_type, entity_id, details, ip_address, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, NOW())
        """, (admin_id, action, entity_type, entity_id, details, ip_address))
        conn.commit()
    finally:
        cursor.close()
        conn.close()
