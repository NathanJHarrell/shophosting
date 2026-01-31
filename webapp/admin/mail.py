"""
Mail Management Module
Provides mailbox, alias, and autoresponder management for Dovecot/Postfix
"""

import os
import re
import subprocess
import shutil
from datetime import datetime, date

# Constants
MAIL_DOMAIN = 'shophosting.io'
VMAIL_BASE = '/var/mail/vhosts/shophosting.io'
VMAIL_UID = 5000
VMAIL_GID = 5000

# Username validation regex - must start with letter/number, can contain . _ -
USERNAME_PATTERN = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._-]*$')


def hash_password(plain):
    """
    Hash a password using doveadm for Dovecot-compatible format.
    Returns SHA512-CRYPT hash suitable for virtual mailboxes.
    """
    result = subprocess.run(
        ['doveadm', 'pw', '-s', 'SHA512-CRYPT', '-p', plain],
        capture_output=True,
        text=True
    )
    return result.stdout.strip()


def get_maildir_size(username):
    """
    Get the size of a user's maildir in bytes.
    Returns 0 if maildir doesn't exist or on error.
    """
    maildir = os.path.join(VMAIL_BASE, username)
    result = subprocess.run(
        ['du', '-sb', maildir],
        capture_output=True,
        text=True
    )
    if result.returncode != 0 or not result.stdout:
        return 0
    try:
        # du output format: "12345\t/path/to/dir"
        return int(result.stdout.split('\t')[0])
    except (ValueError, IndexError):
        return 0


def create_maildir(username):
    """
    Create a maildir structure for a new mailbox.
    Creates cur, new, tmp subdirectories and sets proper ownership.
    """
    maildir = os.path.join(VMAIL_BASE, username)

    # Create main maildir and subdirectories
    for subdir in ['cur', 'new', 'tmp']:
        path = os.path.join(maildir, subdir)
        os.makedirs(path, mode=0o700, exist_ok=True)

    # Create sieve directory
    sieve_dir = os.path.join(maildir, 'sieve')
    os.makedirs(sieve_dir, mode=0o700, exist_ok=True)

    # Set ownership recursively
    for root, dirs, files in os.walk(maildir):
        os.chown(root, VMAIL_UID, VMAIL_GID)
        for d in dirs:
            os.chown(os.path.join(root, d), VMAIL_UID, VMAIL_GID)
        for f in files:
            os.chown(os.path.join(root, f), VMAIL_UID, VMAIL_GID)

    return maildir


def delete_maildir(username):
    """
    Delete a user's maildir.
    Returns True if deleted, False if didn't exist.
    """
    maildir = os.path.join(VMAIL_BASE, username)
    if os.path.exists(maildir):
        shutil.rmtree(maildir)
        return True
    return False


class Mailbox:
    """Mailbox model for virtual mailbox management."""

    @staticmethod
    def validate_username(username):
        """
        Validate a mailbox username.
        Must start with alphanumeric, can contain . _ -
        Returns True if valid, False otherwise.
        """
        if not username:
            return False
        if len(username) > 64:
            return False
        if not USERNAME_PATTERN.match(username):
            return False
        # Prevent directory traversal
        if '..' in username or '/' in username:
            return False
        return True

    @staticmethod
    def get_all(db, search=None, status=None, page=1, per_page=20):
        """
        Get all mailboxes with optional filtering and pagination.
        Returns (list of mailboxes, total count).
        """
        cursor = db.cursor(dictionary=True)

        # Build WHERE clause
        conditions = []
        params = []

        if search:
            conditions.append('(username LIKE %s OR email LIKE %s)')
            params.extend([f'%{search}%', f'%{search}%'])

        if status:
            if status == 'active':
                conditions.append('is_active = 1')
            elif status == 'inactive':
                conditions.append('is_active = 0')
            elif status == 'system':
                conditions.append('is_system_user = 1')

        where_clause = ''
        if conditions:
            where_clause = 'WHERE ' + ' AND '.join(conditions)

        # Get total count
        cursor.execute(f'SELECT COUNT(*) as total FROM mail_mailboxes {where_clause}', params)
        total = cursor.fetchone()['total']

        # Get paginated results
        offset = (page - 1) * per_page
        cursor.execute(f'''
            SELECT * FROM mail_mailboxes
            {where_clause}
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
        ''', params + [per_page, offset])

        mailboxes = cursor.fetchall()
        cursor.close()

        return mailboxes, total

    @staticmethod
    def get_by_id(db, mailbox_id):
        """Get a mailbox by its ID."""
        cursor = db.cursor(dictionary=True)
        cursor.execute('SELECT * FROM mail_mailboxes WHERE id = %s', (mailbox_id,))
        result = cursor.fetchone()
        cursor.close()
        return result

    @staticmethod
    def get_by_email(db, email):
        """Get a mailbox by its email address."""
        cursor = db.cursor(dictionary=True)
        cursor.execute('SELECT * FROM mail_mailboxes WHERE email = %s', (email,))
        result = cursor.fetchone()
        cursor.close()
        return result

    @staticmethod
    def create(db, username, password, quota_mb=1024, is_system_user=False):
        """
        Create a new mailbox.
        Returns the new mailbox ID on success, raises exception on failure.
        """
        if not Mailbox.validate_username(username):
            raise ValueError(f'Invalid username: {username}')

        email = f'{username}@{MAIL_DOMAIN}'

        # Check if email already exists
        existing = Mailbox.get_by_email(db, email)
        if existing:
            raise ValueError(f'Email already exists: {email}')

        # Hash password
        password_hash = hash_password(password)

        cursor = db.cursor()
        cursor.execute('''
            INSERT INTO mail_mailboxes
            (email, username, password_hash, quota_mb, is_active, is_system_user)
            VALUES (%s, %s, %s, %s, 1, %s)
        ''', (email, username, password_hash, quota_mb, is_system_user))

        mailbox_id = cursor.lastrowid
        db.commit()
        cursor.close()

        # Create physical maildir
        create_maildir(username)

        return mailbox_id

    @staticmethod
    def update(db, mailbox_id, **kwargs):
        """
        Update a mailbox's attributes.
        Allowed kwargs: quota_mb, is_active
        """
        allowed_fields = {'quota_mb', 'is_active'}
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}

        if not updates:
            return False

        cursor = db.cursor()

        set_clauses = []
        params = []

        if 'quota_mb' in updates:
            set_clauses.append('quota_mb = %s')
            params.append(updates['quota_mb'])

        if 'is_active' in updates:
            set_clauses.append('is_active = %s')
            params.append(1 if updates['is_active'] else 0)

        params.append(mailbox_id)

        cursor.execute(f'''
            UPDATE mail_mailboxes
            SET {', '.join(set_clauses)}
            WHERE id = %s
        ''', params)

        db.commit()
        cursor.close()
        return True

    @staticmethod
    def set_password(db, mailbox_id, new_password):
        """Update a mailbox's password."""
        password_hash = hash_password(new_password)

        cursor = db.cursor()
        cursor.execute('''
            UPDATE mail_mailboxes
            SET password_hash = %s
            WHERE id = %s
        ''', (password_hash, mailbox_id))

        db.commit()
        cursor.close()
        return True

    @staticmethod
    def delete(db, mailbox_id):
        """
        Delete a mailbox and its maildir.
        Returns True on success.
        """
        # Get mailbox info first
        mailbox = Mailbox.get_by_id(db, mailbox_id)
        if not mailbox:
            raise ValueError(f'Mailbox not found: {mailbox_id}')

        username = mailbox['username']

        # Delete from database
        cursor = db.cursor()

        # Delete aliases first (foreign key constraint)
        cursor.execute('DELETE FROM mail_aliases WHERE destination_mailbox_id = %s', (mailbox_id,))

        # Delete autoresponder if exists
        cursor.execute('DELETE FROM mail_autoresponders WHERE mailbox_id = %s', (mailbox_id,))

        # Delete mailbox
        cursor.execute('DELETE FROM mail_mailboxes WHERE id = %s', (mailbox_id,))

        db.commit()
        cursor.close()

        # Delete physical maildir
        delete_maildir(username)

        return True

    @staticmethod
    def get_stats(db):
        """
        Get mailbox statistics.
        Returns dict with total, active, inactive, system counts.
        """
        cursor = db.cursor(dictionary=True)

        cursor.execute('''
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END) as active,
                SUM(CASE WHEN is_active = 0 THEN 1 ELSE 0 END) as inactive,
                SUM(CASE WHEN is_system_user = 1 THEN 1 ELSE 0 END) as system_users
            FROM mail_mailboxes
        ''')

        result = cursor.fetchone()
        cursor.close()

        return {
            'total': result['total'] or 0,
            'active': result['active'] or 0,
            'inactive': result['inactive'] or 0,
            'system_users': result['system_users'] or 0
        }


class Alias:
    """Alias model for virtual alias management."""

    @staticmethod
    def get_all(db, mailbox_id=None):
        """
        Get all aliases, optionally filtered by destination mailbox.
        """
        cursor = db.cursor(dictionary=True)

        if mailbox_id:
            cursor.execute('''
                SELECT a.*, m.email as destination_email
                FROM mail_aliases a
                JOIN mail_mailboxes m ON a.destination_mailbox_id = m.id
                WHERE a.destination_mailbox_id = %s
                ORDER BY a.created_at DESC
            ''', (mailbox_id,))
        else:
            cursor.execute('''
                SELECT a.*, m.email as destination_email
                FROM mail_aliases a
                JOIN mail_mailboxes m ON a.destination_mailbox_id = m.id
                ORDER BY a.created_at DESC
            ''')

        aliases = cursor.fetchall()
        cursor.close()
        return aliases

    @staticmethod
    def create(db, alias, destination_mailbox_id):
        """
        Create a new alias.
        Returns the new alias ID.
        """
        # Validate alias format
        if '@' not in alias:
            alias = f'{alias}@{MAIL_DOMAIN}'

        # Check destination mailbox exists
        cursor = db.cursor(dictionary=True)
        cursor.execute('SELECT id, email FROM mail_mailboxes WHERE id = %s', (destination_mailbox_id,))
        mailbox = cursor.fetchone()
        if not mailbox:
            cursor.close()
            raise ValueError(f'Destination mailbox not found: {destination_mailbox_id}')

        destination = mailbox['email']

        # Check alias doesn't already exist
        cursor.execute('SELECT id FROM mail_aliases WHERE alias = %s', (alias,))
        if cursor.fetchone():
            cursor.close()
            raise ValueError(f'Alias already exists: {alias}')

        cursor.execute('''
            INSERT INTO mail_aliases (alias, destination, destination_mailbox_id, is_active)
            VALUES (%s, %s, %s, 1)
        ''', (alias, destination, destination_mailbox_id))

        alias_id = cursor.lastrowid
        db.commit()
        cursor.close()

        return alias_id

    @staticmethod
    def delete(db, alias_id):
        """Delete an alias by ID."""
        cursor = db.cursor()
        cursor.execute('DELETE FROM mail_aliases WHERE id = %s', (alias_id,))
        db.commit()
        cursor.close()
        return True


class Autoresponder:
    """Autoresponder model with Sieve script generation."""

    @staticmethod
    def get_by_mailbox(db, mailbox_id):
        """Get autoresponder settings for a mailbox."""
        cursor = db.cursor(dictionary=True)
        cursor.execute('''
            SELECT * FROM mail_autoresponders WHERE mailbox_id = %s
        ''', (mailbox_id,))
        result = cursor.fetchone()
        cursor.close()
        return result

    @staticmethod
    def save(db, mailbox_id, subject, body, is_active, start_date=None, end_date=None):
        """
        Save autoresponder settings.
        Creates or updates the autoresponder and regenerates the Sieve script.
        """
        # Get mailbox info
        mailbox = Mailbox.get_by_id(db, mailbox_id)
        if not mailbox:
            raise ValueError(f'Mailbox not found: {mailbox_id}')

        username = mailbox['username']

        cursor = db.cursor(dictionary=True)

        # Check if autoresponder already exists
        cursor.execute('SELECT id FROM mail_autoresponders WHERE mailbox_id = %s', (mailbox_id,))
        existing = cursor.fetchone()

        if existing:
            cursor.execute('''
                UPDATE mail_autoresponders
                SET subject = %s, body = %s, is_active = %s,
                    start_date = %s, end_date = %s
                WHERE mailbox_id = %s
            ''', (subject, body, is_active, start_date, end_date, mailbox_id))
        else:
            cursor.execute('''
                INSERT INTO mail_autoresponders
                (mailbox_id, subject, body, is_active, start_date, end_date)
                VALUES (%s, %s, %s, %s, %s, %s)
            ''', (mailbox_id, subject, body, is_active, start_date, end_date))

        db.commit()
        cursor.close()

        # Update Sieve script
        Autoresponder._update_sieve(username, subject, body, is_active, start_date, end_date)

        return True

    @staticmethod
    def _update_sieve(username, subject, body, is_active, start_date=None, end_date=None):
        """
        Generate and write a Sieve script for vacation autoresponse.
        """
        sieve_dir = os.path.join(VMAIL_BASE, username, 'sieve')
        sieve_file = os.path.join(sieve_dir, 'default.sieve')

        # Ensure sieve directory exists
        os.makedirs(sieve_dir, mode=0o700, exist_ok=True)

        if not is_active:
            # Remove sieve file if autoresponder is disabled
            if os.path.exists(sieve_file):
                os.remove(sieve_file)
            return

        # Build date conditions
        date_conditions = []
        today = date.today()

        if start_date and isinstance(start_date, (date, datetime)):
            start = start_date if isinstance(start_date, date) else start_date.date()
            if today < start:
                # Not yet active, don't create script
                if os.path.exists(sieve_file):
                    os.remove(sieve_file)
                return

        if end_date and isinstance(end_date, (date, datetime)):
            end = end_date if isinstance(end_date, date) else end_date.date()
            if today > end:
                # Expired, remove script
                if os.path.exists(sieve_file):
                    os.remove(sieve_file)
                return

        # Generate Sieve script
        sieve_content = f'''require ["vacation", "fileinto"];

# Autoresponder for {username}@{MAIL_DOMAIN}
# Generated: {datetime.now().isoformat()}

vacation :days 1 :subject "{subject}"
"{body}";
'''

        with open(sieve_file, 'w') as f:
            f.write(sieve_content)

        # Set proper ownership
        os.chown(sieve_file, VMAIL_UID, VMAIL_GID)
        os.chmod(sieve_file, 0o600)

        # Compile sieve script
        compiled_file = sieve_file.replace('.sieve', '.svbin')
        subprocess.run(
            ['sievec', sieve_file, compiled_file],
            capture_output=True
        )
        if os.path.exists(compiled_file):
            os.chown(compiled_file, VMAIL_UID, VMAIL_GID)
            os.chmod(compiled_file, 0o600)

    @staticmethod
    def get_all_active(db):
        """Get all active autoresponders."""
        cursor = db.cursor(dictionary=True)
        cursor.execute('''
            SELECT a.*, m.username, m.email
            FROM mail_autoresponders a
            JOIN mail_mailboxes m ON a.mailbox_id = m.id
            WHERE a.is_active = 1
            ORDER BY a.created_at DESC
        ''')
        results = cursor.fetchall()
        cursor.close()
        return results
