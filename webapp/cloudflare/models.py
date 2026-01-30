# webapp/cloudflare/models.py
"""
Cloudflare Integration Models

Provides database models for storing Cloudflare OAuth tokens and caching DNS records.
Tokens are encrypted using Fernet symmetric encryption derived from the app's SECRET_KEY.
"""

import os
import base64
import hashlib
from datetime import datetime
from cryptography.fernet import Fernet

# Import database connection from main models module
from models import get_db_connection


# =============================================================================
# Encryption Helpers
# =============================================================================

def get_encryption_key():
    """
    Derive a Fernet-compatible encryption key from SECRET_KEY.

    Fernet requires a 32-byte base64-encoded key. We derive this by hashing
    the SECRET_KEY with SHA-256 and base64-encoding the result.

    Returns:
        bytes: A Fernet-compatible encryption key
    """
    secret_key = os.getenv('SECRET_KEY')
    if not secret_key:
        raise RuntimeError("SECRET_KEY environment variable is required for token encryption")

    # Hash the secret key to get exactly 32 bytes
    key_bytes = hashlib.sha256(secret_key.encode()).digest()
    # Fernet needs a URL-safe base64-encoded 32-byte key
    return base64.urlsafe_b64encode(key_bytes)


def encrypt_token(token):
    """
    Encrypt a token string using Fernet symmetric encryption.

    Args:
        token: The plaintext token string to encrypt

    Returns:
        str: The encrypted token as a base64-encoded string, or None if token is None
    """
    if token is None:
        return None

    fernet = Fernet(get_encryption_key())
    encrypted = fernet.encrypt(token.encode())
    return encrypted.decode()


def decrypt_token(encrypted_token):
    """
    Decrypt a Fernet-encrypted token.

    Args:
        encrypted_token: The encrypted token string

    Returns:
        str: The decrypted plaintext token, or None if encrypted_token is None
    """
    if encrypted_token is None:
        return None

    fernet = Fernet(get_encryption_key())
    decrypted = fernet.decrypt(encrypted_token.encode())
    return decrypted.decode()


# =============================================================================
# CloudflareConnection Model
# =============================================================================

class CloudflareConnection:
    """
    Model for customer Cloudflare OAuth connections.

    Stores encrypted OAuth tokens and zone information for a customer's
    Cloudflare account connection.
    """

    def __init__(self, id=None, customer_id=None, cloudflare_zone_id=None,
                 access_token=None, refresh_token=None, token_expires_at=None,
                 connected_at=None, last_sync_at=None):
        self.id = id
        self.customer_id = customer_id
        self.cloudflare_zone_id = cloudflare_zone_id
        # Store encrypted tokens internally
        self._access_token = access_token
        self._refresh_token = refresh_token
        self.token_expires_at = token_expires_at
        self.connected_at = connected_at or datetime.now()
        self.last_sync_at = last_sync_at

    # =========================================================================
    # Token Properties (handle encryption/decryption transparently)
    # =========================================================================

    @property
    def access_token(self):
        """Get decrypted access token."""
        return decrypt_token(self._access_token)

    @access_token.setter
    def access_token(self, value):
        """Set and encrypt access token."""
        self._access_token = encrypt_token(value)

    @property
    def refresh_token(self):
        """Get decrypted refresh token."""
        return decrypt_token(self._refresh_token)

    @refresh_token.setter
    def refresh_token(self, value):
        """Set and encrypt refresh token."""
        self._refresh_token = encrypt_token(value)

    # =========================================================================
    # Token Validation
    # =========================================================================

    def is_token_expired(self):
        """
        Check if the access token has expired.

        Returns:
            bool: True if token is expired or no expiry is set, False otherwise
        """
        if self.token_expires_at is None:
            return True
        return datetime.now() >= self.token_expires_at

    # =========================================================================
    # Database Operations
    # =========================================================================

    def save(self):
        """
        Save the connection to the database.

        Inserts a new record if id is None, otherwise updates the existing record.

        Returns:
            CloudflareConnection: The saved instance with updated id
        """
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            if self.id is None:
                # Insert new connection
                cursor.execute("""
                    INSERT INTO customer_cloudflare_connections
                    (customer_id, cloudflare_zone_id, access_token, refresh_token,
                     token_expires_at, connected_at, last_sync_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (
                    self.customer_id, self.cloudflare_zone_id,
                    self._access_token, self._refresh_token,
                    self.token_expires_at, self.connected_at, self.last_sync_at
                ))
                self.id = cursor.lastrowid
            else:
                # Update existing connection
                cursor.execute("""
                    UPDATE customer_cloudflare_connections SET
                        cloudflare_zone_id = %s,
                        access_token = %s,
                        refresh_token = %s,
                        token_expires_at = %s,
                        last_sync_at = %s
                    WHERE id = %s
                """, (
                    self.cloudflare_zone_id, self._access_token, self._refresh_token,
                    self.token_expires_at, self.last_sync_at, self.id
                ))

            conn.commit()
            return self
        finally:
            cursor.close()
            conn.close()

    def delete(self):
        """
        Delete the connection from the database.

        Returns:
            bool: True if a record was deleted, False otherwise
        """
        if self.id is None:
            return False

        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute(
                "DELETE FROM customer_cloudflare_connections WHERE id = %s",
                (self.id,)
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_by_customer_id(customer_id):
        """
        Get the Cloudflare connection for a customer.

        Args:
            customer_id: The customer ID to look up

        Returns:
            CloudflareConnection: The connection if found, None otherwise
        """
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute(
                "SELECT * FROM customer_cloudflare_connections WHERE customer_id = %s",
                (customer_id,)
            )
            row = cursor.fetchone()

            if row:
                return CloudflareConnection(
                    id=row['id'],
                    customer_id=row['customer_id'],
                    cloudflare_zone_id=row['cloudflare_zone_id'],
                    access_token=row['access_token'],  # Stored encrypted
                    refresh_token=row['refresh_token'],  # Stored encrypted
                    token_expires_at=row['token_expires_at'],
                    connected_at=row['connected_at'],
                    last_sync_at=row['last_sync_at']
                )
            return None
        finally:
            cursor.close()
            conn.close()


# =============================================================================
# DNSRecordCache Model
# =============================================================================

class DNSRecordCache:
    """
    Model for caching DNS records from Cloudflare.

    Stores a local copy of DNS records for fast display without
    hitting the Cloudflare API on every page load.
    """

    def __init__(self, id=None, customer_id=None, cloudflare_record_id=None,
                 record_type=None, name=None, content=None, priority=None,
                 proxied=False, ttl=1, synced_at=None):
        self.id = id
        self.customer_id = customer_id
        self.cloudflare_record_id = cloudflare_record_id
        self.record_type = record_type
        self.name = name
        self.content = content
        self.priority = priority
        self.proxied = proxied
        self.ttl = ttl
        self.synced_at = synced_at or datetime.now()

    # =========================================================================
    # Database Operations
    # =========================================================================

    def save(self):
        """
        Save the DNS record cache entry to the database.

        Uses INSERT ... ON DUPLICATE KEY UPDATE to handle upserts by
        cloudflare_record_id.

        Returns:
            DNSRecordCache: The saved instance with updated id
        """
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            # Use upsert pattern - insert or update if cloudflare_record_id exists
            cursor.execute("""
                INSERT INTO dns_records_cache
                (customer_id, cloudflare_record_id, record_type, name, content,
                 priority, proxied, ttl, synced_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    record_type = VALUES(record_type),
                    name = VALUES(name),
                    content = VALUES(content),
                    priority = VALUES(priority),
                    proxied = VALUES(proxied),
                    ttl = VALUES(ttl),
                    synced_at = VALUES(synced_at)
            """, (
                self.customer_id, self.cloudflare_record_id, self.record_type,
                self.name, self.content, self.priority, self.proxied,
                self.ttl, self.synced_at
            ))

            if self.id is None:
                self.id = cursor.lastrowid

            conn.commit()
            return self
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_by_customer_id(customer_id):
        """
        Get all cached DNS records for a customer.

        Args:
            customer_id: The customer ID to look up

        Returns:
            list[DNSRecordCache]: List of cached DNS records
        """
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute("""
                SELECT * FROM dns_records_cache
                WHERE customer_id = %s
                ORDER BY record_type, name
            """, (customer_id,))
            rows = cursor.fetchall()

            return [DNSRecordCache(**row) for row in rows]
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def delete_by_cloudflare_id(cloudflare_record_id):
        """
        Delete a cached DNS record by its Cloudflare record ID.

        Args:
            cloudflare_record_id: The Cloudflare record ID to delete

        Returns:
            bool: True if a record was deleted, False otherwise
        """
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute(
                "DELETE FROM dns_records_cache WHERE cloudflare_record_id = %s",
                (cloudflare_record_id,)
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def clear_customer_cache(customer_id):
        """
        Delete all cached DNS records for a customer.

        Used when disconnecting Cloudflare or forcing a full resync.

        Args:
            customer_id: The customer ID whose cache to clear

        Returns:
            int: Number of records deleted
        """
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute(
                "DELETE FROM dns_records_cache WHERE customer_id = %s",
                (customer_id,)
            )
            conn.commit()
            return cursor.rowcount
        finally:
            cursor.close()
            conn.close()
