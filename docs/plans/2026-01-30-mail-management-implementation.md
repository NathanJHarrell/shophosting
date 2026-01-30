# Mail Management Admin Interface Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a mailbox management interface to the super admin panel supporting mixed authentication (system PAM users + virtual MySQL users) with quotas, aliases, forwarding, catch-all, and autoresponders.

**Architecture:** Flask admin blueprint extension with MySQL-backed virtual users. Dovecot handles auth (MySQL first, PAM fallback) and mailbox access. Postfix delivers to virtual mailboxes via MySQL lookups. Maildir format for virtual users at `/var/mail/vhosts/shophosting.io/<user>/`.

**Tech Stack:** Python/Flask, MySQL, Dovecot, Postfix, Jinja2 templates, doveadm for password hashing

---

## Task 1: Database Migration

**Files:**
- Create: `migrations/011_mail_management.sql`

**Step 1: Write the migration file**

```sql
-- Mail Management Tables
-- Run with: mysql -u root shophosting < migrations/011_mail_management.sql

-- Virtual mailboxes (email accounts)
CREATE TABLE IF NOT EXISTS mail_mailboxes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    username VARCHAR(64) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    quota_mb INT DEFAULT 1024,
    is_active BOOLEAN DEFAULT TRUE,
    is_system_user BOOLEAN DEFAULT FALSE,
    forward_to TEXT NULL,
    is_catch_all BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_email (email),
    INDEX idx_username (username),
    INDEX idx_is_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Email aliases
CREATE TABLE IF NOT EXISTS mail_aliases (
    id INT AUTO_INCREMENT PRIMARY KEY,
    alias VARCHAR(255) NOT NULL UNIQUE,
    destination_mailbox_id INT NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (destination_mailbox_id) REFERENCES mail_mailboxes(id) ON DELETE CASCADE,
    INDEX idx_alias (alias)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Autoresponders
CREATE TABLE IF NOT EXISTS mail_autoresponders (
    id INT AUTO_INCREMENT PRIMARY KEY,
    mailbox_id INT NOT NULL UNIQUE,
    subject VARCHAR(255) NOT NULL,
    body TEXT NOT NULL,
    is_active BOOLEAN DEFAULT FALSE,
    start_date DATE NULL,
    end_date DATE NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (mailbox_id) REFERENCES mail_mailboxes(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

**Step 2: Run the migration**

Run: `sudo mysql shophosting < migrations/011_mail_management.sql`
Expected: No output (success)

**Step 3: Verify tables created**

Run: `sudo mysql shophosting -e "SHOW TABLES LIKE 'mail_%';"`
Expected:
```
+------------------------------------+
| Tables_in_shophosting (mail_%)     |
+------------------------------------+
| mail_aliases                       |
| mail_autoresponders                |
| mail_mailboxes                     |
+------------------------------------+
```

**Step 4: Commit**

```bash
git add migrations/011_mail_management.sql
git commit -m "feat(mail): add database migration for mail management tables"
```

---

## Task 2: Create vmail User and Directory Structure

**Files:**
- None (system configuration)

**Step 1: Create vmail system user**

Run:
```bash
sudo groupadd -g 5000 vmail 2>/dev/null || true
sudo useradd -u 5000 -g vmail -s /usr/sbin/nologin -d /var/mail/vhosts -M vmail 2>/dev/null || true
```
Expected: No output or "already exists" message

**Step 2: Create mailbox directory structure**

Run:
```bash
sudo mkdir -p /var/mail/vhosts/shophosting.io
sudo chown -R vmail:vmail /var/mail/vhosts
sudo chmod -R 770 /var/mail/vhosts
```
Expected: No output (success)

**Step 3: Verify setup**

Run: `ls -la /var/mail/vhosts/`
Expected: Directory owned by vmail:vmail with 770 permissions

**Step 4: Create MySQL mail user**

Run:
```bash
sudo mysql -e "CREATE USER IF NOT EXISTS 'mailuser'@'localhost' IDENTIFIED BY 'mailpass123';"
sudo mysql -e "GRANT SELECT ON shophosting.mail_mailboxes TO 'mailuser'@'localhost';"
sudo mysql -e "GRANT SELECT ON shophosting.mail_aliases TO 'mailuser'@'localhost';"
sudo mysql -e "FLUSH PRIVILEGES;"
```
Expected: No output (success)

---

## Task 3: Configure Dovecot Mixed Authentication

**Files:**
- Create: `/etc/dovecot/conf.d/auth-mixed.conf.ext`
- Create: `/etc/dovecot/dovecot-sql.conf.ext`
- Modify: `/etc/dovecot/conf.d/10-auth.conf`
- Modify: `/etc/dovecot/conf.d/10-mail.conf`

**Step 1: Create SQL configuration file**

Create `/etc/dovecot/dovecot-sql.conf.ext`:
```conf
driver = mysql
connect = host=localhost dbname=shophosting user=mailuser password=mailpass123

# Password query - only for virtual users
password_query = SELECT email as user, password_hash as password \
  FROM mail_mailboxes \
  WHERE email = '%u' AND is_active = 1 AND is_system_user = 0

# User query - mailbox location for virtual users
user_query = SELECT CONCAT('/var/mail/vhosts/shophosting.io/', username) as home, \
  5000 as uid, 5000 as gid, CONCAT('*:bytes=', quota_mb * 1024 * 1024) as quota_rule \
  FROM mail_mailboxes \
  WHERE email = '%u' AND is_active = 1 AND is_system_user = 0
```

Run:
```bash
sudo tee /etc/dovecot/dovecot-sql.conf.ext << 'EOF'
driver = mysql
connect = host=localhost dbname=shophosting user=mailuser password=mailpass123

password_query = SELECT email as user, password_hash as password \
  FROM mail_mailboxes \
  WHERE email = '%u' AND is_active = 1 AND is_system_user = 0

user_query = SELECT CONCAT('/var/mail/vhosts/shophosting.io/', username) as home, \
  5000 as uid, 5000 as gid, CONCAT('*:bytes=', quota_mb * 1024 * 1024) as quota_rule \
  FROM mail_mailboxes \
  WHERE email = '%u' AND is_active = 1 AND is_system_user = 0
EOF
sudo chmod 640 /etc/dovecot/dovecot-sql.conf.ext
sudo chown root:dovecot /etc/dovecot/dovecot-sql.conf.ext
```

**Step 2: Create mixed auth configuration**

Create `/etc/dovecot/conf.d/auth-mixed.conf.ext`:
```conf
# Virtual users from MySQL (checked first)
passdb {
  driver = sql
  args = /etc/dovecot/dovecot-sql.conf.ext
}

# Fall back to system users (PAM)
passdb {
  driver = pam
  args = dovecot
}

# Virtual user mailbox locations
userdb {
  driver = sql
  args = /etc/dovecot/dovecot-sql.conf.ext
}

# System user mailbox locations
userdb {
  driver = passwd
}
```

Run:
```bash
sudo tee /etc/dovecot/conf.d/auth-mixed.conf.ext << 'EOF'
passdb {
  driver = sql
  args = /etc/dovecot/dovecot-sql.conf.ext
}

passdb {
  driver = pam
  args = dovecot
}

userdb {
  driver = sql
  args = /etc/dovecot/dovecot-sql.conf.ext
}

userdb {
  driver = passwd
}
EOF
```

**Step 3: Update 10-auth.conf**

Run:
```bash
sudo sed -i 's/^!include auth-system.conf.ext/#!include auth-system.conf.ext/' /etc/dovecot/conf.d/10-auth.conf
echo '!include auth-mixed.conf.ext' | sudo tee -a /etc/dovecot/conf.d/10-auth.conf
```

**Step 4: Update 10-mail.conf for virtual users**

Run:
```bash
sudo sed -i 's|^mail_location = .*|mail_location = maildir:/var/mail/vhosts/%d/%n|' /etc/dovecot/conf.d/10-mail.conf
```

Note: System users will still work because userdb passwd fallback returns their home directory.

**Step 5: Test configuration and restart**

Run:
```bash
sudo doveconf -n | grep -E "(passdb|userdb|mail_location)" | head -20
sudo systemctl restart dovecot
sudo systemctl status dovecot | head -5
```
Expected: Service active and running

---

## Task 4: Configure Postfix Virtual Delivery

**Files:**
- Create: `/etc/postfix/mysql-virtual-mailboxes.cf`
- Create: `/etc/postfix/mysql-virtual-aliases.cf`
- Modify: `/etc/postfix/main.cf`

**Step 1: Create virtual mailbox lookup**

Run:
```bash
sudo tee /etc/postfix/mysql-virtual-mailboxes.cf << 'EOF'
hosts = localhost
user = mailuser
password = mailpass123
dbname = shophosting
query = SELECT CONCAT(username, '/') FROM mail_mailboxes WHERE email = '%s' AND is_active = 1
EOF
sudo chmod 640 /etc/postfix/mysql-virtual-mailboxes.cf
sudo chown root:postfix /etc/postfix/mysql-virtual-mailboxes.cf
```

**Step 2: Create virtual alias lookup**

Run:
```bash
sudo tee /etc/postfix/mysql-virtual-aliases.cf << 'EOF'
hosts = localhost
user = mailuser
password = mailpass123
dbname = shophosting
query = SELECT COALESCE(
    (SELECT m.email FROM mail_aliases a JOIN mail_mailboxes m ON a.destination_mailbox_id = m.id WHERE a.alias = '%s' AND a.is_active = 1 LIMIT 1),
    (SELECT forward_to FROM mail_mailboxes WHERE email = '%s' AND forward_to IS NOT NULL AND is_active = 1 LIMIT 1),
    (SELECT email FROM mail_mailboxes WHERE is_catch_all = 1 AND is_active = 1 AND '%s' LIKE '%%@shophosting.io' LIMIT 1)
)
EOF
sudo chmod 640 /etc/postfix/mysql-virtual-aliases.cf
sudo chown root:postfix /etc/postfix/mysql-virtual-aliases.cf
```

**Step 3: Update main.cf**

Run:
```bash
sudo tee -a /etc/postfix/main.cf << 'EOF'

# Virtual mailbox configuration
virtual_mailbox_domains = shophosting.io
virtual_mailbox_base = /var/mail/vhosts
virtual_mailbox_maps = mysql:/etc/postfix/mysql-virtual-mailboxes.cf
virtual_alias_maps = mysql:/etc/postfix/mysql-virtual-aliases.cf
virtual_uid_maps = static:5000
virtual_gid_maps = static:5000
virtual_minimum_uid = 5000
EOF

# Remove shophosting.io from mydestination
sudo sed -i 's/shophosting.io, //g' /etc/postfix/main.cf
sudo sed -i 's/, shophosting.io//g' /etc/postfix/main.cf
```

**Step 4: Test and restart Postfix**

Run:
```bash
sudo postfix check
sudo systemctl restart postfix
sudo systemctl status postfix | head -5
```
Expected: No errors, service running

---

## Task 5: Backend Mail Module

**Files:**
- Create: `webapp/admin/mail.py`
- Test: `webapp/tests/test_mail.py`

**Step 1: Write the test file**

Create `webapp/tests/test_mail.py`:
```python
"""Tests for mail management module."""
import pytest
from unittest.mock import patch, MagicMock

# Test password hashing
class TestPasswordHashing:
    def test_hash_password_returns_string(self):
        from admin.mail import hash_password
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                stdout='{SHA512-CRYPT}$6$rounds=5000$saltsalt$hashhash\n',
                returncode=0
            )
            result = hash_password('testpass')
            assert result.startswith('{SHA512-CRYPT}')
            assert '\n' not in result

    def test_hash_password_calls_doveadm(self):
        from admin.mail import hash_password
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(stdout='hash\n', returncode=0)
            hash_password('testpass')
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert 'doveadm' in args
            assert 'pw' in args


class TestMailboxModel:
    def test_validate_username_valid(self):
        from admin.mail import Mailbox
        assert Mailbox.validate_username('john') == True
        assert Mailbox.validate_username('john.doe') == True
        assert Mailbox.validate_username('john_doe123') == True

    def test_validate_username_invalid(self):
        from admin.mail import Mailbox
        assert Mailbox.validate_username('') == False
        assert Mailbox.validate_username('john@domain') == False
        assert Mailbox.validate_username('john doe') == False
        assert Mailbox.validate_username('../etc') == False


class TestMaildirSize:
    def test_get_maildir_size_returns_int(self):
        from admin.mail import get_maildir_size
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                stdout='12345\t/var/mail/vhosts/shophosting.io/test\n',
                returncode=0
            )
            result = get_maildir_size('test')
            assert result == 12345

    def test_get_maildir_size_nonexistent_returns_zero(self):
        from admin.mail import get_maildir_size
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(stdout='', returncode=1)
            result = get_maildir_size('nonexistent')
            assert result == 0
```

**Step 2: Run tests to verify they fail**

Run: `cd /opt/shophosting/.worktrees/mail-management/webapp && python3 -m pytest tests/test_mail.py -v`
Expected: FAIL with "No module named 'admin.mail'"

**Step 3: Write the mail module**

Create `webapp/admin/mail.py`:
```python
"""Mail management module for virtual mailboxes."""
import subprocess
import re
import os
from typing import Optional, List, Dict, Any
from datetime import date

# Domain configuration
MAIL_DOMAIN = 'shophosting.io'
VMAIL_BASE = '/var/mail/vhosts'
VMAIL_UID = 5000
VMAIL_GID = 5000


def hash_password(plain: str) -> str:
    """Hash password using Dovecot's doveadm for compatibility."""
    result = subprocess.run(
        ['doveadm', 'pw', '-s', 'SHA512-CRYPT', '-p', plain],
        capture_output=True, text=True
    )
    return result.stdout.strip()


def get_maildir_size(username: str) -> int:
    """Get maildir size in bytes."""
    path = f"{VMAIL_BASE}/{MAIL_DOMAIN}/{username}"
    result = subprocess.run(
        ['du', '-sb', path],
        capture_output=True, text=True
    )
    if result.returncode == 0 and result.stdout:
        try:
            return int(result.stdout.split()[0])
        except (ValueError, IndexError):
            return 0
    return 0


def create_maildir(username: str) -> bool:
    """Create maildir structure for a new virtual user."""
    path = f"{VMAIL_BASE}/{MAIL_DOMAIN}/{username}"
    try:
        os.makedirs(f"{path}/cur", exist_ok=True)
        os.makedirs(f"{path}/new", exist_ok=True)
        os.makedirs(f"{path}/tmp", exist_ok=True)
        # Set ownership to vmail
        subprocess.run(['chown', '-R', f'{VMAIL_UID}:{VMAIL_GID}', path], check=True)
        subprocess.run(['chmod', '-R', '700', path], check=True)
        return True
    except Exception:
        return False


def delete_maildir(username: str) -> bool:
    """Delete maildir for a user."""
    path = f"{VMAIL_BASE}/{MAIL_DOMAIN}/{username}"
    try:
        subprocess.run(['rm', '-rf', path], check=True)
        return True
    except Exception:
        return False


class Mailbox:
    """Virtual mailbox management."""

    @staticmethod
    def validate_username(username: str) -> bool:
        """Validate username format."""
        if not username or len(username) > 64:
            return False
        # Only allow alphanumeric, dots, underscores, hyphens
        pattern = r'^[a-zA-Z0-9][a-zA-Z0-9._-]*$'
        return bool(re.match(pattern, username))

    @staticmethod
    def get_all(db, search: str = None, status: str = None, page: int = 1, per_page: int = 25) -> Dict[str, Any]:
        """Get paginated list of mailboxes."""
        cursor = db.cursor(dictionary=True)

        where_clauses = []
        params = []

        if search:
            where_clauses.append("(email LIKE %s OR username LIKE %s)")
            params.extend([f'%{search}%', f'%{search}%'])

        if status == 'active':
            where_clauses.append("is_active = 1")
        elif status == 'inactive':
            where_clauses.append("is_active = 0")

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

        # Count total
        cursor.execute(f"SELECT COUNT(*) as total FROM mail_mailboxes WHERE {where_sql}", params)
        total = cursor.fetchone()['total']

        # Get page
        offset = (page - 1) * per_page
        cursor.execute(f"""
            SELECT id, email, username, quota_mb, is_active, is_system_user,
                   forward_to, is_catch_all, created_at, updated_at
            FROM mail_mailboxes
            WHERE {where_sql}
            ORDER BY email
            LIMIT %s OFFSET %s
        """, params + [per_page, offset])

        mailboxes = cursor.fetchall()
        cursor.close()

        return {
            'mailboxes': mailboxes,
            'total': total,
            'page': page,
            'per_page': per_page,
            'pages': (total + per_page - 1) // per_page
        }

    @staticmethod
    def get_by_id(db, mailbox_id: int) -> Optional[Dict]:
        """Get mailbox by ID."""
        cursor = db.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, email, username, quota_mb, is_active, is_system_user,
                   forward_to, is_catch_all, created_at, updated_at
            FROM mail_mailboxes WHERE id = %s
        """, (mailbox_id,))
        result = cursor.fetchone()
        cursor.close()
        return result

    @staticmethod
    def get_by_email(db, email: str) -> Optional[Dict]:
        """Get mailbox by email."""
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM mail_mailboxes WHERE email = %s", (email,))
        result = cursor.fetchone()
        cursor.close()
        return result

    @staticmethod
    def create(db, username: str, password: str, quota_mb: int = 1024,
               is_system_user: bool = False) -> Optional[int]:
        """Create a new mailbox."""
        if not Mailbox.validate_username(username):
            return None

        email = f"{username}@{MAIL_DOMAIN}"
        password_hash = hash_password(password) if not is_system_user else ''

        cursor = db.cursor()
        try:
            cursor.execute("""
                INSERT INTO mail_mailboxes (email, username, password_hash, quota_mb, is_system_user)
                VALUES (%s, %s, %s, %s, %s)
            """, (email, username, password_hash, quota_mb, is_system_user))
            db.commit()
            mailbox_id = cursor.lastrowid

            # Create maildir for virtual users
            if not is_system_user:
                create_maildir(username)

            return mailbox_id
        except Exception:
            db.rollback()
            return None
        finally:
            cursor.close()

    @staticmethod
    def update(db, mailbox_id: int, **kwargs) -> bool:
        """Update mailbox fields."""
        allowed_fields = ['quota_mb', 'is_active', 'forward_to', 'is_catch_all']
        updates = []
        params = []

        for field in allowed_fields:
            if field in kwargs:
                updates.append(f"{field} = %s")
                params.append(kwargs[field])

        if not updates:
            return False

        params.append(mailbox_id)
        cursor = db.cursor()
        try:
            cursor.execute(f"""
                UPDATE mail_mailboxes SET {', '.join(updates)} WHERE id = %s
            """, params)
            db.commit()
            return cursor.rowcount > 0
        except Exception:
            db.rollback()
            return False
        finally:
            cursor.close()

    @staticmethod
    def set_password(db, mailbox_id: int, new_password: str) -> bool:
        """Update mailbox password."""
        password_hash = hash_password(new_password)
        cursor = db.cursor()
        try:
            cursor.execute("""
                UPDATE mail_mailboxes SET password_hash = %s
                WHERE id = %s AND is_system_user = 0
            """, (password_hash, mailbox_id))
            db.commit()
            return cursor.rowcount > 0
        except Exception:
            db.rollback()
            return False
        finally:
            cursor.close()

    @staticmethod
    def delete(db, mailbox_id: int) -> bool:
        """Delete mailbox and its maildir."""
        mailbox = Mailbox.get_by_id(db, mailbox_id)
        if not mailbox:
            return False

        cursor = db.cursor()
        try:
            cursor.execute("DELETE FROM mail_mailboxes WHERE id = %s", (mailbox_id,))
            db.commit()

            # Delete maildir for virtual users
            if not mailbox['is_system_user']:
                delete_maildir(mailbox['username'])

            return True
        except Exception:
            db.rollback()
            return False
        finally:
            cursor.close()

    @staticmethod
    def get_stats(db) -> Dict[str, int]:
        """Get mailbox statistics."""
        cursor = db.cursor(dictionary=True)
        cursor.execute("""
            SELECT
                COUNT(*) as total,
                SUM(is_active = 1) as active,
                SUM(is_active = 0) as inactive,
                SUM(is_system_user = 1) as system_users,
                SUM(is_system_user = 0) as virtual_users,
                SUM(is_catch_all = 1) as catch_all
            FROM mail_mailboxes
        """)
        result = cursor.fetchone()
        cursor.close()
        return result


class Alias:
    """Email alias management."""

    @staticmethod
    def get_all(db, mailbox_id: int = None) -> List[Dict]:
        """Get all aliases, optionally filtered by mailbox."""
        cursor = db.cursor(dictionary=True)
        if mailbox_id:
            cursor.execute("""
                SELECT a.*, m.email as destination_email
                FROM mail_aliases a
                JOIN mail_mailboxes m ON a.destination_mailbox_id = m.id
                WHERE a.destination_mailbox_id = %s
                ORDER BY a.alias
            """, (mailbox_id,))
        else:
            cursor.execute("""
                SELECT a.*, m.email as destination_email
                FROM mail_aliases a
                JOIN mail_mailboxes m ON a.destination_mailbox_id = m.id
                ORDER BY a.alias
            """)
        result = cursor.fetchall()
        cursor.close()
        return result

    @staticmethod
    def create(db, alias: str, destination_mailbox_id: int) -> Optional[int]:
        """Create a new alias."""
        # Ensure alias has domain
        if '@' not in alias:
            alias = f"{alias}@{MAIL_DOMAIN}"

        cursor = db.cursor()
        try:
            cursor.execute("""
                INSERT INTO mail_aliases (alias, destination_mailbox_id)
                VALUES (%s, %s)
            """, (alias, destination_mailbox_id))
            db.commit()
            return cursor.lastrowid
        except Exception:
            db.rollback()
            return None
        finally:
            cursor.close()

    @staticmethod
    def delete(db, alias_id: int) -> bool:
        """Delete an alias."""
        cursor = db.cursor()
        try:
            cursor.execute("DELETE FROM mail_aliases WHERE id = %s", (alias_id,))
            db.commit()
            return cursor.rowcount > 0
        except Exception:
            db.rollback()
            return False
        finally:
            cursor.close()


class Autoresponder:
    """Autoresponder (vacation message) management."""

    @staticmethod
    def get_by_mailbox(db, mailbox_id: int) -> Optional[Dict]:
        """Get autoresponder for a mailbox."""
        cursor = db.cursor(dictionary=True)
        cursor.execute("""
            SELECT * FROM mail_autoresponders WHERE mailbox_id = %s
        """, (mailbox_id,))
        result = cursor.fetchone()
        cursor.close()
        return result

    @staticmethod
    def save(db, mailbox_id: int, subject: str, body: str,
             is_active: bool = False, start_date: date = None,
             end_date: date = None) -> bool:
        """Create or update autoresponder."""
        cursor = db.cursor()
        try:
            # Try update first
            cursor.execute("""
                UPDATE mail_autoresponders
                SET subject = %s, body = %s, is_active = %s,
                    start_date = %s, end_date = %s
                WHERE mailbox_id = %s
            """, (subject, body, is_active, start_date, end_date, mailbox_id))

            if cursor.rowcount == 0:
                # Insert if not exists
                cursor.execute("""
                    INSERT INTO mail_autoresponders
                    (mailbox_id, subject, body, is_active, start_date, end_date)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (mailbox_id, subject, body, is_active, start_date, end_date))

            db.commit()

            # Update sieve script if autoresponder is active
            mailbox = Mailbox.get_by_id(db, mailbox_id)
            if mailbox and not mailbox['is_system_user']:
                Autoresponder._update_sieve(mailbox['username'], subject, body, is_active, start_date, end_date)

            return True
        except Exception:
            db.rollback()
            return False
        finally:
            cursor.close()

    @staticmethod
    def _update_sieve(username: str, subject: str, body: str,
                      is_active: bool, start_date: date, end_date: date):
        """Update Sieve script for autoresponder."""
        sieve_dir = f"{VMAIL_BASE}/{MAIL_DOMAIN}/{username}/sieve"
        sieve_file = f"{sieve_dir}/default.sieve"

        if not is_active:
            # Remove sieve script if inactive
            if os.path.exists(sieve_file):
                os.remove(sieve_file)
            return

        # Create sieve directory
        os.makedirs(sieve_dir, exist_ok=True)

        # Build date conditions
        date_conditions = []
        if start_date:
            date_conditions.append(f'currentdate :value "ge" "date" "{start_date}"')
        if end_date:
            date_conditions.append(f'currentdate :value "le" "date" "{end_date}"')

        date_check = " allof(" + ", ".join(date_conditions) + ")" if date_conditions else ""

        # Write sieve script
        sieve_content = f'''require ["vacation", "date", "relational"];

vacation :days 1 :subject "{subject}"{date_check}
"{body}";
'''

        with open(sieve_file, 'w') as f:
            f.write(sieve_content)

        # Set permissions
        subprocess.run(['chown', '-R', f'{VMAIL_UID}:{VMAIL_GID}', sieve_dir])
        subprocess.run(['chmod', '700', sieve_dir])
        subprocess.run(['chmod', '600', sieve_file])

        # Compile sieve script
        subprocess.run(['sievec', sieve_file], capture_output=True)

    @staticmethod
    def get_all_active(db) -> List[Dict]:
        """Get all active autoresponders."""
        cursor = db.cursor(dictionary=True)
        cursor.execute("""
            SELECT a.*, m.email, m.username
            FROM mail_autoresponders a
            JOIN mail_mailboxes m ON a.mailbox_id = m.id
            WHERE a.is_active = 1
        """)
        result = cursor.fetchall()
        cursor.close()
        return result
```

**Step 4: Run tests to verify they pass**

Run: `cd /opt/shophosting/.worktrees/mail-management/webapp && python3 -m pytest tests/test_mail.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add webapp/admin/mail.py webapp/tests/test_mail.py
git commit -m "feat(mail): add mail management backend module

- Mailbox CRUD with MySQL storage
- Alias management
- Autoresponder with Sieve script generation
- Password hashing via doveadm
- Maildir creation/deletion"
```

---

## Task 6: Admin Routes for Mail Management

**Files:**
- Create: `webapp/admin/mail_routes.py`
- Modify: `webapp/admin/__init__.py`

**Step 1: Create mail routes**

Create `webapp/admin/mail_routes.py`:
```python
"""Admin routes for mail management."""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from functools import wraps
from .routes import admin_required, get_db_connection, log_admin_action
from .mail import Mailbox, Alias, Autoresponder, get_maildir_size, MAIL_DOMAIN

mail_bp = Blueprint('mail', __name__, url_prefix='/mail')


@mail_bp.route('/')
@admin_required
def dashboard():
    """Mail management dashboard."""
    db = get_db_connection()
    stats = Mailbox.get_stats(db)
    recent = Mailbox.get_all(db, page=1, per_page=10)
    db.close()
    return render_template('admin/mail_dashboard.html', stats=stats, recent=recent['mailboxes'])


@mail_bp.route('/mailboxes')
@admin_required
def mailboxes():
    """List all mailboxes."""
    db = get_db_connection()
    search = request.args.get('search', '')
    status = request.args.get('status', '')
    page = int(request.args.get('page', 1))

    result = Mailbox.get_all(db, search=search, status=status, page=page)

    # Add usage info
    for mb in result['mailboxes']:
        if not mb['is_system_user']:
            mb['usage_bytes'] = get_maildir_size(mb['username'])
            mb['usage_mb'] = mb['usage_bytes'] / (1024 * 1024)
            mb['usage_percent'] = (mb['usage_mb'] / mb['quota_mb'] * 100) if mb['quota_mb'] else 0
        else:
            mb['usage_bytes'] = 0
            mb['usage_mb'] = 0
            mb['usage_percent'] = 0

    db.close()
    return render_template('admin/mail_mailboxes.html',
                          mailboxes=result['mailboxes'],
                          total=result['total'],
                          page=result['page'],
                          pages=result['pages'],
                          search=search,
                          status=status)


@mail_bp.route('/mailboxes/create', methods=['GET', 'POST'])
@admin_required
def create_mailbox():
    """Create a new mailbox."""
    if request.method == 'POST':
        username = request.form.get('username', '').strip().lower()
        password = request.form.get('password', '')
        quota_mb = int(request.form.get('quota_mb', 1024))

        if not Mailbox.validate_username(username):
            flash('Invalid username. Use only letters, numbers, dots, underscores, hyphens.', 'error')
            return render_template('admin/mail_mailbox_form.html', mailbox=None, domain=MAIL_DOMAIN)

        if len(password) < 8:
            flash('Password must be at least 8 characters.', 'error')
            return render_template('admin/mail_mailbox_form.html', mailbox=None, domain=MAIL_DOMAIN)

        db = get_db_connection()

        # Check if exists
        if Mailbox.get_by_email(db, f"{username}@{MAIL_DOMAIN}"):
            flash('Mailbox already exists.', 'error')
            db.close()
            return render_template('admin/mail_mailbox_form.html', mailbox=None, domain=MAIL_DOMAIN)

        mailbox_id = Mailbox.create(db, username, password, quota_mb)
        if mailbox_id:
            log_admin_action('create_mailbox', 'mailbox', mailbox_id,
                           f'Created mailbox {username}@{MAIL_DOMAIN}')
            flash(f'Mailbox {username}@{MAIL_DOMAIN} created successfully.', 'success')
            db.close()
            return redirect(url_for('admin.mail.mailboxes'))
        else:
            flash('Failed to create mailbox.', 'error')

        db.close()

    return render_template('admin/mail_mailbox_form.html', mailbox=None, domain=MAIL_DOMAIN)


@mail_bp.route('/mailboxes/<int:mailbox_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_mailbox(mailbox_id):
    """Edit a mailbox."""
    db = get_db_connection()
    mailbox = Mailbox.get_by_id(db, mailbox_id)

    if not mailbox:
        flash('Mailbox not found.', 'error')
        db.close()
        return redirect(url_for('admin.mail.mailboxes'))

    # Get aliases and autoresponder
    aliases = Alias.get_all(db, mailbox_id)
    autoresponder = Autoresponder.get_by_mailbox(db, mailbox_id)

    # Get usage
    if not mailbox['is_system_user']:
        mailbox['usage_bytes'] = get_maildir_size(mailbox['username'])
        mailbox['usage_mb'] = mailbox['usage_bytes'] / (1024 * 1024)

    if request.method == 'POST':
        action = request.form.get('action', 'update')

        if action == 'update':
            quota_mb = int(request.form.get('quota_mb', 1024))
            is_active = request.form.get('is_active') == 'on'
            forward_to = request.form.get('forward_to', '').strip() or None
            is_catch_all = request.form.get('is_catch_all') == 'on'

            Mailbox.update(db, mailbox_id,
                          quota_mb=quota_mb,
                          is_active=is_active,
                          forward_to=forward_to,
                          is_catch_all=is_catch_all)
            log_admin_action('update_mailbox', 'mailbox', mailbox_id,
                           f'Updated mailbox {mailbox["email"]}')
            flash('Mailbox updated.', 'success')

        elif action == 'password':
            new_password = request.form.get('new_password', '')
            if len(new_password) >= 8:
                if Mailbox.set_password(db, mailbox_id, new_password):
                    log_admin_action('reset_mailbox_password', 'mailbox', mailbox_id,
                                   f'Reset password for {mailbox["email"]}')
                    flash('Password updated.', 'success')
                else:
                    flash('Cannot change password for system users.', 'error')
            else:
                flash('Password must be at least 8 characters.', 'error')

        elif action == 'autoresponder':
            subject = request.form.get('ar_subject', '')
            body = request.form.get('ar_body', '')
            is_active = request.form.get('ar_active') == 'on'
            start_date = request.form.get('ar_start') or None
            end_date = request.form.get('ar_end') or None

            Autoresponder.save(db, mailbox_id, subject, body, is_active, start_date, end_date)
            log_admin_action('update_autoresponder', 'mailbox', mailbox_id,
                           f'Updated autoresponder for {mailbox["email"]}')
            flash('Autoresponder updated.', 'success')

        db.close()
        return redirect(url_for('admin.mail.edit_mailbox', mailbox_id=mailbox_id))

    db.close()
    return render_template('admin/mail_mailbox_form.html',
                          mailbox=mailbox,
                          aliases=aliases,
                          autoresponder=autoresponder,
                          domain=MAIL_DOMAIN)


@mail_bp.route('/mailboxes/<int:mailbox_id>/delete', methods=['POST'])
@admin_required
def delete_mailbox(mailbox_id):
    """Delete a mailbox."""
    db = get_db_connection()
    mailbox = Mailbox.get_by_id(db, mailbox_id)

    if not mailbox:
        flash('Mailbox not found.', 'error')
    elif mailbox['is_system_user']:
        flash('Cannot delete system user mailboxes from here.', 'error')
    else:
        email = mailbox['email']
        if Mailbox.delete(db, mailbox_id):
            log_admin_action('delete_mailbox', 'mailbox', mailbox_id, f'Deleted mailbox {email}')
            flash(f'Mailbox {email} deleted.', 'success')
        else:
            flash('Failed to delete mailbox.', 'error')

    db.close()
    return redirect(url_for('admin.mail.mailboxes'))


@mail_bp.route('/aliases')
@admin_required
def aliases():
    """List all aliases."""
    db = get_db_connection()
    all_aliases = Alias.get_all(db)
    db.close()
    return render_template('admin/mail_aliases.html', aliases=all_aliases, domain=MAIL_DOMAIN)


@mail_bp.route('/aliases/create', methods=['GET', 'POST'])
@admin_required
def create_alias():
    """Create a new alias."""
    db = get_db_connection()

    if request.method == 'POST':
        alias = request.form.get('alias', '').strip().lower()
        destination_id = int(request.form.get('destination_mailbox_id', 0))

        if not alias:
            flash('Alias cannot be empty.', 'error')
        else:
            alias_id = Alias.create(db, alias, destination_id)
            if alias_id:
                log_admin_action('create_alias', 'alias', alias_id, f'Created alias {alias}')
                flash(f'Alias {alias} created.', 'success')
                db.close()
                return redirect(url_for('admin.mail.aliases'))
            else:
                flash('Failed to create alias. It may already exist.', 'error')

    # Get all mailboxes for dropdown
    mailboxes = Mailbox.get_all(db, per_page=1000)['mailboxes']
    db.close()
    return render_template('admin/mail_alias_form.html',
                          alias=None,
                          mailboxes=mailboxes,
                          domain=MAIL_DOMAIN)


@mail_bp.route('/aliases/<int:alias_id>/delete', methods=['POST'])
@admin_required
def delete_alias(alias_id):
    """Delete an alias."""
    db = get_db_connection()
    if Alias.delete(db, alias_id):
        log_admin_action('delete_alias', 'alias', alias_id, 'Deleted alias')
        flash('Alias deleted.', 'success')
    else:
        flash('Failed to delete alias.', 'error')
    db.close()
    return redirect(url_for('admin.mail.aliases'))


@mail_bp.route('/catch-all', methods=['GET', 'POST'])
@admin_required
def catch_all():
    """Configure catch-all mailbox."""
    db = get_db_connection()

    if request.method == 'POST':
        mailbox_id = request.form.get('mailbox_id')

        # Clear existing catch-all
        cursor = db.cursor()
        cursor.execute("UPDATE mail_mailboxes SET is_catch_all = 0 WHERE is_catch_all = 1")

        if mailbox_id:
            cursor.execute("UPDATE mail_mailboxes SET is_catch_all = 1 WHERE id = %s", (mailbox_id,))
            log_admin_action('set_catch_all', 'mailbox', mailbox_id, 'Set as catch-all')
            flash('Catch-all mailbox updated.', 'success')
        else:
            flash('Catch-all disabled.', 'success')

        db.commit()
        cursor.close()

    # Get current catch-all
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM mail_mailboxes WHERE is_catch_all = 1 LIMIT 1")
    current = cursor.fetchone()
    cursor.close()

    mailboxes = Mailbox.get_all(db, status='active', per_page=1000)['mailboxes']
    db.close()

    return render_template('admin/mail_catch_all.html',
                          current=current,
                          mailboxes=mailboxes,
                          domain=MAIL_DOMAIN)


@mail_bp.route('/api/stats')
@admin_required
def api_stats():
    """Get mail statistics as JSON."""
    db = get_db_connection()
    stats = Mailbox.get_stats(db)
    db.close()
    return jsonify(stats)


@mail_bp.route('/api/usage/<int:mailbox_id>')
@admin_required
def api_usage(mailbox_id):
    """Get mailbox usage as JSON."""
    db = get_db_connection()
    mailbox = Mailbox.get_by_id(db, mailbox_id)
    db.close()

    if not mailbox:
        return jsonify({'error': 'Not found'}), 404

    usage_bytes = get_maildir_size(mailbox['username'])
    return jsonify({
        'usage_bytes': usage_bytes,
        'usage_mb': usage_bytes / (1024 * 1024),
        'quota_mb': mailbox['quota_mb'],
        'percent': (usage_bytes / (mailbox['quota_mb'] * 1024 * 1024) * 100) if mailbox['quota_mb'] else 0
    })
```

**Step 2: Register blueprint in admin __init__.py**

Add to `webapp/admin/__init__.py`:
```python
from .mail_routes import mail_bp
admin_bp.register_blueprint(mail_bp)
```

Run:
```bash
# Check current content
cat webapp/admin/__init__.py

# Add the import and registration
```

**Step 3: Commit**

```bash
git add webapp/admin/mail_routes.py webapp/admin/__init__.py
git commit -m "feat(mail): add admin routes for mail management

- Dashboard with stats
- Mailbox CRUD routes
- Alias management
- Catch-all configuration
- API endpoints for stats and usage"
```

---

## Task 7: Admin Templates

**Files:**
- Create: `webapp/templates/admin/mail_dashboard.html`
- Create: `webapp/templates/admin/mail_mailboxes.html`
- Create: `webapp/templates/admin/mail_mailbox_form.html`
- Create: `webapp/templates/admin/mail_aliases.html`
- Create: `webapp/templates/admin/mail_alias_form.html`
- Create: `webapp/templates/admin/mail_catch_all.html`

**Step 1: Create mail dashboard template**

Create `webapp/templates/admin/mail_dashboard.html`:
```html
{% extends "admin/base_admin.html" %}

{% block title %}Mail Management - Admin{% endblock %}

{% block content %}
<div class="content-header">
    <h1>Mail Management</h1>
    <p>Manage mailboxes, aliases, and email settings for {{ domain or 'shophosting.io' }}</p>
</div>

<div class="stats-grid">
    <div class="stat-card">
        <div class="stat-value">{{ stats.total or 0 }}</div>
        <div class="stat-label">Total Mailboxes</div>
    </div>
    <div class="stat-card">
        <div class="stat-value">{{ stats.active or 0 }}</div>
        <div class="stat-label">Active</div>
    </div>
    <div class="stat-card">
        <div class="stat-value">{{ stats.virtual_users or 0 }}</div>
        <div class="stat-label">Virtual Users</div>
    </div>
    <div class="stat-card">
        <div class="stat-value">{{ stats.system_users or 0 }}</div>
        <div class="stat-label">System Users</div>
    </div>
</div>

<div class="quick-actions">
    <a href="{{ url_for('admin.mail.create_mailbox') }}" class="btn btn-primary">
        + New Mailbox
    </a>
    <a href="{{ url_for('admin.mail.mailboxes') }}" class="btn btn-secondary">
        View All Mailboxes
    </a>
    <a href="{{ url_for('admin.mail.aliases') }}" class="btn btn-secondary">
        Manage Aliases
    </a>
    <a href="{{ url_for('admin.mail.catch_all') }}" class="btn btn-secondary">
        Catch-All Settings
    </a>
</div>

<div class="card mt-4">
    <div class="card-header">
        <h3>Recent Mailboxes</h3>
    </div>
    <table class="data-table">
        <thead>
            <tr>
                <th>Email</th>
                <th>Type</th>
                <th>Status</th>
                <th>Created</th>
            </tr>
        </thead>
        <tbody>
            {% for mb in recent %}
            <tr>
                <td>
                    <a href="{{ url_for('admin.mail.edit_mailbox', mailbox_id=mb.id) }}">
                        {{ mb.email }}
                    </a>
                </td>
                <td>
                    {% if mb.is_system_user %}
                        <span class="badge badge-info">System</span>
                    {% else %}
                        <span class="badge badge-secondary">Virtual</span>
                    {% endif %}
                </td>
                <td>
                    {% if mb.is_active %}
                        <span class="badge badge-success">Active</span>
                    {% else %}
                        <span class="badge badge-danger">Inactive</span>
                    {% endif %}
                </td>
                <td>{{ mb.created_at.strftime('%Y-%m-%d') if mb.created_at else '-' }}</td>
            </tr>
            {% else %}
            <tr>
                <td colspan="4" class="text-center">No mailboxes yet</td>
            </tr>
            {% endfor %}
        </tbody>
    </table>
</div>
{% endblock %}
```

**Step 2: Create mailboxes list template**

Create `webapp/templates/admin/mail_mailboxes.html`:
```html
{% extends "admin/base_admin.html" %}

{% block title %}Mailboxes - Admin{% endblock %}

{% block content %}
<div class="content-header">
    <h1>Mailboxes</h1>
    <a href="{{ url_for('admin.mail.create_mailbox') }}" class="btn btn-primary">+ New Mailbox</a>
</div>

<div class="card">
    <div class="card-header">
        <form method="GET" class="filter-form">
            <input type="text" name="search" value="{{ search }}" placeholder="Search email..." class="form-control">
            <select name="status" class="form-control">
                <option value="">All Status</option>
                <option value="active" {% if status == 'active' %}selected{% endif %}>Active</option>
                <option value="inactive" {% if status == 'inactive' %}selected{% endif %}>Inactive</option>
            </select>
            <button type="submit" class="btn btn-secondary">Filter</button>
        </form>
    </div>

    <table class="data-table">
        <thead>
            <tr>
                <th>Email</th>
                <th>Type</th>
                <th>Quota</th>
                <th>Usage</th>
                <th>Status</th>
                <th>Actions</th>
            </tr>
        </thead>
        <tbody>
            {% for mb in mailboxes %}
            <tr>
                <td>
                    <a href="{{ url_for('admin.mail.edit_mailbox', mailbox_id=mb.id) }}">
                        {{ mb.email }}
                    </a>
                    {% if mb.is_catch_all %}
                        <span class="badge badge-warning">catch-all</span>
                    {% endif %}
                    {% if mb.forward_to %}
                        <span class="badge badge-info">forwarding</span>
                    {% endif %}
                </td>
                <td>
                    {% if mb.is_system_user %}
                        <span class="badge badge-info">System</span>
                    {% else %}
                        <span class="badge badge-secondary">Virtual</span>
                    {% endif %}
                </td>
                <td>{{ mb.quota_mb }} MB</td>
                <td>
                    {% if not mb.is_system_user %}
                        <div class="progress-bar-small">
                            <div class="progress-fill" style="width: {{ mb.usage_percent|min(100) }}%"></div>
                        </div>
                        <small>{{ "%.1f"|format(mb.usage_mb) }} / {{ mb.quota_mb }} MB</small>
                    {% else %}
                        <span class="text-muted">N/A</span>
                    {% endif %}
                </td>
                <td>
                    {% if mb.is_active %}
                        <span class="badge badge-success">Active</span>
                    {% else %}
                        <span class="badge badge-danger">Inactive</span>
                    {% endif %}
                </td>
                <td>
                    <a href="{{ url_for('admin.mail.edit_mailbox', mailbox_id=mb.id) }}" class="btn btn-sm">Edit</a>
                    {% if not mb.is_system_user %}
                    <form method="POST" action="{{ url_for('admin.mail.delete_mailbox', mailbox_id=mb.id) }}"
                          style="display:inline" onsubmit="return confirm('Delete this mailbox?')">
                        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                        <button type="submit" class="btn btn-sm btn-danger">Delete</button>
                    </form>
                    {% endif %}
                </td>
            </tr>
            {% else %}
            <tr>
                <td colspan="6" class="text-center">No mailboxes found</td>
            </tr>
            {% endfor %}
        </tbody>
    </table>

    {% if pages > 1 %}
    <div class="pagination">
        {% for p in range(1, pages + 1) %}
            <a href="?page={{ p }}&search={{ search }}&status={{ status }}"
               class="{% if p == page %}active{% endif %}">{{ p }}</a>
        {% endfor %}
    </div>
    {% endif %}
</div>
{% endblock %}
```

**Step 3: Create mailbox form template**

Create `webapp/templates/admin/mail_mailbox_form.html`:
```html
{% extends "admin/base_admin.html" %}

{% block title %}{% if mailbox %}Edit{% else %}Create{% endif %} Mailbox - Admin{% endblock %}

{% block content %}
<div class="content-header">
    <h1>{% if mailbox %}Edit Mailbox: {{ mailbox.email }}{% else %}Create Mailbox{% endif %}</h1>
    <a href="{{ url_for('admin.mail.mailboxes') }}" class="btn btn-secondary">Back to List</a>
</div>

<div class="card">
    <form method="POST">
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
        <input type="hidden" name="action" value="update">

        <div class="form-group">
            <label>Email Address</label>
            {% if mailbox %}
                <input type="text" value="{{ mailbox.email }}" class="form-control" disabled>
                {% if mailbox.is_system_user %}
                    <small class="text-info">System user - password managed via Linux</small>
                {% endif %}
            {% else %}
                <div class="input-group">
                    <input type="text" name="username" required class="form-control"
                           placeholder="username" pattern="[a-zA-Z0-9._-]+">
                    <span class="input-group-text">@{{ domain }}</span>
                </div>
            {% endif %}
        </div>

        {% if not mailbox %}
        <div class="form-group">
            <label>Password</label>
            <input type="password" name="password" required class="form-control" minlength="8">
            <small>Minimum 8 characters</small>
        </div>
        {% endif %}

        <div class="form-group">
            <label>Quota (MB)</label>
            <input type="number" name="quota_mb" value="{{ mailbox.quota_mb if mailbox else 1024 }}"
                   class="form-control" min="1" max="102400">
        </div>

        {% if mailbox %}
        <div class="form-group">
            <label>
                <input type="checkbox" name="is_active" {% if mailbox.is_active %}checked{% endif %}>
                Active
            </label>
        </div>

        <div class="form-group">
            <label>Forward To (optional)</label>
            <input type="email" name="forward_to" value="{{ mailbox.forward_to or '' }}"
                   class="form-control" placeholder="external@example.com">
            <small>Forward copies of incoming mail to this address</small>
        </div>

        <div class="form-group">
            <label>
                <input type="checkbox" name="is_catch_all" {% if mailbox.is_catch_all %}checked{% endif %}>
                Catch-All (receive mail for non-existent addresses)
            </label>
        </div>

        {% if not mailbox.is_system_user %}
        <div class="form-group">
            <label>Usage</label>
            <div class="progress-bar">
                <div class="progress-fill" style="width: {{ (mailbox.usage_mb / mailbox.quota_mb * 100)|min(100) if mailbox.quota_mb else 0 }}%"></div>
            </div>
            <small>{{ "%.2f"|format(mailbox.usage_mb) }} MB / {{ mailbox.quota_mb }} MB</small>
        </div>
        {% endif %}
        {% endif %}

        <button type="submit" class="btn btn-primary">
            {% if mailbox %}Save Changes{% else %}Create Mailbox{% endif %}
        </button>
    </form>
</div>

{% if mailbox and not mailbox.is_system_user %}
<div class="card mt-4">
    <div class="card-header">
        <h3>Change Password</h3>
    </div>
    <form method="POST">
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
        <input type="hidden" name="action" value="password">

        <div class="form-group">
            <label>New Password</label>
            <input type="password" name="new_password" required class="form-control" minlength="8">
        </div>

        <button type="submit" class="btn btn-warning">Update Password</button>
    </form>
</div>

<div class="card mt-4">
    <div class="card-header">
        <h3>Autoresponder</h3>
    </div>
    <form method="POST">
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
        <input type="hidden" name="action" value="autoresponder">

        <div class="form-group">
            <label>
                <input type="checkbox" name="ar_active" {% if autoresponder and autoresponder.is_active %}checked{% endif %}>
                Enable Autoresponder
            </label>
        </div>

        <div class="form-group">
            <label>Subject</label>
            <input type="text" name="ar_subject" class="form-control"
                   value="{{ autoresponder.subject if autoresponder else 'Out of Office' }}">
        </div>

        <div class="form-group">
            <label>Message</label>
            <textarea name="ar_body" class="form-control" rows="4">{{ autoresponder.body if autoresponder else '' }}</textarea>
        </div>

        <div class="form-row">
            <div class="form-group col-md-6">
                <label>Start Date (optional)</label>
                <input type="date" name="ar_start" class="form-control"
                       value="{{ autoresponder.start_date if autoresponder else '' }}">
            </div>
            <div class="form-group col-md-6">
                <label>End Date (optional)</label>
                <input type="date" name="ar_end" class="form-control"
                       value="{{ autoresponder.end_date if autoresponder else '' }}">
            </div>
        </div>

        <button type="submit" class="btn btn-primary">Save Autoresponder</button>
    </form>
</div>

<div class="card mt-4">
    <div class="card-header">
        <h3>Aliases</h3>
        <a href="{{ url_for('admin.mail.create_alias') }}?destination={{ mailbox.id }}" class="btn btn-sm btn-primary">+ Add Alias</a>
    </div>
    <table class="data-table">
        <thead>
            <tr>
                <th>Alias</th>
                <th>Actions</th>
            </tr>
        </thead>
        <tbody>
            {% for alias in aliases %}
            <tr>
                <td>{{ alias.alias }}</td>
                <td>
                    <form method="POST" action="{{ url_for('admin.mail.delete_alias', alias_id=alias.id) }}" style="display:inline">
                        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                        <button type="submit" class="btn btn-sm btn-danger" onclick="return confirm('Delete this alias?')">Delete</button>
                    </form>
                </td>
            </tr>
            {% else %}
            <tr>
                <td colspan="2" class="text-center">No aliases</td>
            </tr>
            {% endfor %}
        </tbody>
    </table>
</div>
{% endif %}
{% endblock %}
```

**Step 4: Create aliases template**

Create `webapp/templates/admin/mail_aliases.html`:
```html
{% extends "admin/base_admin.html" %}

{% block title %}Email Aliases - Admin{% endblock %}

{% block content %}
<div class="content-header">
    <h1>Email Aliases</h1>
    <a href="{{ url_for('admin.mail.create_alias') }}" class="btn btn-primary">+ New Alias</a>
</div>

<div class="card">
    <table class="data-table">
        <thead>
            <tr>
                <th>Alias</th>
                <th>Destination</th>
                <th>Status</th>
                <th>Actions</th>
            </tr>
        </thead>
        <tbody>
            {% for alias in aliases %}
            <tr>
                <td>{{ alias.alias }}</td>
                <td>
                    <a href="{{ url_for('admin.mail.edit_mailbox', mailbox_id=alias.destination_mailbox_id) }}">
                        {{ alias.destination_email }}
                    </a>
                </td>
                <td>
                    {% if alias.is_active %}
                        <span class="badge badge-success">Active</span>
                    {% else %}
                        <span class="badge badge-danger">Inactive</span>
                    {% endif %}
                </td>
                <td>
                    <form method="POST" action="{{ url_for('admin.mail.delete_alias', alias_id=alias.id) }}" style="display:inline">
                        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                        <button type="submit" class="btn btn-sm btn-danger" onclick="return confirm('Delete this alias?')">Delete</button>
                    </form>
                </td>
            </tr>
            {% else %}
            <tr>
                <td colspan="4" class="text-center">No aliases configured</td>
            </tr>
            {% endfor %}
        </tbody>
    </table>
</div>
{% endblock %}
```

**Step 5: Create alias form template**

Create `webapp/templates/admin/mail_alias_form.html`:
```html
{% extends "admin/base_admin.html" %}

{% block title %}Create Alias - Admin{% endblock %}

{% block content %}
<div class="content-header">
    <h1>Create Email Alias</h1>
    <a href="{{ url_for('admin.mail.aliases') }}" class="btn btn-secondary">Back to Aliases</a>
</div>

<div class="card">
    <form method="POST">
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">

        <div class="form-group">
            <label>Alias Address</label>
            <div class="input-group">
                <input type="text" name="alias" required class="form-control" placeholder="alias">
                <span class="input-group-text">@{{ domain }}</span>
            </div>
            <small>This address will forward to the destination mailbox</small>
        </div>

        <div class="form-group">
            <label>Destination Mailbox</label>
            <select name="destination_mailbox_id" required class="form-control">
                <option value="">Select mailbox...</option>
                {% for mb in mailboxes %}
                    <option value="{{ mb.id }}" {% if request.args.get('destination') == mb.id|string %}selected{% endif %}>
                        {{ mb.email }}
                    </option>
                {% endfor %}
            </select>
        </div>

        <button type="submit" class="btn btn-primary">Create Alias</button>
    </form>
</div>
{% endblock %}
```

**Step 6: Create catch-all template**

Create `webapp/templates/admin/mail_catch_all.html`:
```html
{% extends "admin/base_admin.html" %}

{% block title %}Catch-All Settings - Admin{% endblock %}

{% block content %}
<div class="content-header">
    <h1>Catch-All Settings</h1>
    <a href="{{ url_for('admin.mail.dashboard') }}" class="btn btn-secondary">Back to Mail</a>
</div>

<div class="card">
    <div class="card-header">
        <p>The catch-all mailbox receives all mail sent to non-existent addresses at @{{ domain }}</p>
    </div>

    <form method="POST">
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">

        <div class="form-group">
            <label>Catch-All Mailbox</label>
            <select name="mailbox_id" class="form-control">
                <option value="">Disabled - bounce unknown addresses</option>
                {% for mb in mailboxes %}
                    <option value="{{ mb.id }}" {% if current and current.id == mb.id %}selected{% endif %}>
                        {{ mb.email }}
                    </option>
                {% endfor %}
            </select>
        </div>

        {% if current %}
        <div class="alert alert-info">
            Currently receiving catch-all mail: <strong>{{ current.email }}</strong>
        </div>
        {% endif %}

        <button type="submit" class="btn btn-primary">Save Settings</button>
    </form>
</div>
{% endblock %}
```

**Step 7: Commit templates**

```bash
git add webapp/templates/admin/mail_*.html
git commit -m "feat(mail): add admin templates for mail management

- Dashboard with stats overview
- Mailbox list with search/filter/pagination
- Mailbox create/edit form with password, quota, autoresponder
- Alias list and create form
- Catch-all configuration page"
```

---

## Task 8: Add Mail to Admin Sidebar

**Files:**
- Modify: `webapp/templates/admin/base_admin.html`

**Step 1: Find sidebar section in base template**

Look for the sidebar navigation in `base_admin.html` and add the Mail section.

**Step 2: Add Mail navigation items**

Add after existing nav items (look for pattern like "Customers", "Tickets", etc.):
```html
<li class="nav-section">
    <span class="nav-section-title">Mail</span>
    <ul>
        <li><a href="{{ url_for('admin.mail.dashboard') }}">Dashboard</a></li>
        <li><a href="{{ url_for('admin.mail.mailboxes') }}">Mailboxes</a></li>
        <li><a href="{{ url_for('admin.mail.aliases') }}">Aliases</a></li>
        <li><a href="{{ url_for('admin.mail.catch_all') }}">Catch-All</a></li>
    </ul>
</li>
```

**Step 3: Commit**

```bash
git add webapp/templates/admin/base_admin.html
git commit -m "feat(mail): add mail section to admin sidebar navigation"
```

---

## Task 9: Import Existing System User

**Files:**
- None (database operation)

**Step 1: Import agileweb as system user**

Run:
```bash
sudo mysql shophosting -e "
INSERT INTO mail_mailboxes (email, username, password_hash, quota_mb, is_active, is_system_user)
VALUES ('agileweb@shophosting.io', 'agileweb', '', 0, 1, 1)
ON DUPLICATE KEY UPDATE is_system_user = 1;
"
```

**Step 2: Verify import**

Run: `sudo mysql shophosting -e "SELECT * FROM mail_mailboxes;"`
Expected: agileweb row with is_system_user = 1

**Step 3: Test system user still works**

Send test email:
```bash
echo "Test after migration" | mail -s "Test" agileweb@shophosting.io
```

Check delivery:
```bash
sudo tail -5 /var/log/mail.log
```

---

## Task 10: Test Virtual Mailbox Creation

**Step 1: Create test virtual mailbox via admin**

Navigate to `/admin/mail/mailboxes/create` and create:
- Username: `testuser`
- Password: `TestPass123!`
- Quota: 100 MB

**Step 2: Verify maildir created**

Run: `sudo ls -la /var/mail/vhosts/shophosting.io/testuser/`
Expected: cur, new, tmp directories

**Step 3: Send test email**

```bash
echo "Test to virtual user" | mail -s "Virtual Test" testuser@shophosting.io
```

**Step 4: Check delivery**

```bash
sudo tail -10 /var/log/mail.log | grep testuser
sudo ls /var/mail/vhosts/shophosting.io/testuser/new/
```
Expected: Mail delivered to maildir

**Step 5: Test IMAP login**

```bash
echo -e "a001 LOGIN testuser@shophosting.io TestPass123!\na002 LIST \"\" \"*\"\na003 LOGOUT" | \
  openssl s_client -quiet -connect localhost:993 2>/dev/null
```
Expected: `a001 OK` login successful

---

## Task 11: Final Verification and Commit

**Step 1: Run all tests**

```bash
cd /opt/shophosting/.worktrees/mail-management/webapp
python3 -m pytest tests/test_mail.py -v
```

**Step 2: Test admin panel manually**

- Navigate to `/admin/mail`
- Verify dashboard loads
- Test create/edit/delete mailbox
- Test alias creation
- Test catch-all configuration

**Step 3: Final commit**

```bash
git add -A
git status
git commit -m "feat(mail): complete mail management admin interface

Features:
- Virtual mailboxes with MySQL backend
- Mixed auth (system + virtual users)
- Quotas, forwarding, aliases
- Catch-all configuration
- Autoresponders with Dovecot Sieve
- Full admin UI with dashboard

Closes: mail-management feature"
```

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | Database migration | `migrations/011_mail_management.sql` |
| 2 | vmail user + directories | System config |
| 3 | Dovecot mixed auth | `/etc/dovecot/` configs |
| 4 | Postfix virtual delivery | `/etc/postfix/` configs |
| 5 | Backend mail module | `webapp/admin/mail.py` |
| 6 | Admin routes | `webapp/admin/mail_routes.py` |
| 7 | Admin templates | 6 template files |
| 8 | Sidebar navigation | `base_admin.html` |
| 9 | Import system user | Database |
| 10 | Test virtual mailbox | Manual testing |
| 11 | Final verification | Testing + commit |
