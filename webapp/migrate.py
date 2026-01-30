#!/usr/bin/env python3
"""
Database Migration Runner for ShopHosting.io

Tracks and applies SQL migrations in order, ensuring idempotency.
Migrations are applied based on filename sort order (001_, 002_, etc).

Usage:
    python migrate.py              # Apply all pending migrations
    python migrate.py --status     # Show migration status
    python migrate.py --dry-run    # Show what would be applied
"""

import os
import sys
import glob
import hashlib
import time
import argparse
import mysql.connector
from mysql.connector import Error as MySQLError

# Add webapp to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'migrations')


def get_db_connection():
    """Get a database connection using environment variables"""
    return mysql.connector.connect(
        host=os.getenv('DB_HOST', 'localhost'),
        user=os.getenv('DB_USER', 'shophosting_app'),
        password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME', 'shophosting_db'),
        autocommit=False,
        consume_results=True  # Auto-consume unread results
    )


def calculate_checksum(filepath):
    """Calculate SHA256 checksum of a file"""
    with open(filepath, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def ensure_migrations_table(conn, cursor):
    """Ensure the schema_migrations table exists"""
    # First check if table exists
    cursor.execute("""
        SELECT COUNT(*) FROM information_schema.tables
        WHERE table_schema = DATABASE() AND table_name = 'schema_migrations'
    """)

    if cursor.fetchone()[0] == 0:
        # Apply the migrations tracking table first
        tracking_migration = os.path.join(MIGRATIONS_DIR, '000_migrations_tracking.sql')
        if os.path.exists(tracking_migration):
            print("Creating schema_migrations table...")
            with open(tracking_migration, 'r') as f:
                sql = f.read()
            for statement in sql.split(';'):
                statement = statement.strip()
                if statement:
                    cursor.execute(statement)
            conn.commit()
        else:
            raise FileNotFoundError(f"Migration tracking file not found: {tracking_migration}")


def get_applied_migrations(cursor):
    """Get set of already-applied migration filenames and their checksums"""
    cursor.execute("SELECT filename, checksum FROM schema_migrations")
    return {row[0]: row[1] for row in cursor.fetchall()}


def get_pending_migrations(cursor):
    """Get list of migrations that haven't been applied yet"""
    applied = get_applied_migrations(cursor)

    # Get all migration files, sorted by name
    all_files = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, '*.sql')))

    pending = []
    for filepath in all_files:
        filename = os.path.basename(filepath)

        # Skip the tracking table migration if already applied
        if filename == '000_migrations_tracking.sql':
            continue

        if filename not in applied:
            pending.append(filepath)
        else:
            # Check if file has been modified
            current_checksum = calculate_checksum(filepath)
            if applied[filename] != current_checksum:
                print(f"WARNING: Migration {filename} has been modified since it was applied!")
                print(f"  Applied checksum: {applied[filename][:16]}...")
                print(f"  Current checksum: {current_checksum[:16]}...")

    return pending


def apply_migration(cursor, filepath):
    """Apply a single migration file"""
    filename = os.path.basename(filepath)
    checksum = calculate_checksum(filepath)

    print(f"Applying migration: {filename}")

    with open(filepath, 'r') as f:
        sql_content = f.read()

    start_time = time.time()

    # Execute each statement in the migration
    # Split on semicolons but be careful with stored procedures
    statements = []
    current_statement = []
    delimiter = ';'

    for line in sql_content.split('\n'):
        stripped = line.strip()

        # Handle DELIMITER changes (for stored procedures)
        if stripped.upper().startswith('DELIMITER'):
            parts = stripped.split()
            if len(parts) > 1:
                delimiter = parts[1]
            continue

        current_statement.append(line)

        if stripped.endswith(delimiter):
            statement = '\n'.join(current_statement)
            if delimiter != ';':
                statement = statement.rstrip(delimiter)
            statement = statement.strip()
            if statement:
                statements.append(statement)
            current_statement = []

    # Don't forget the last statement if no trailing delimiter
    if current_statement:
        statement = '\n'.join(current_statement).strip()
        if statement:
            statements.append(statement)

    # Execute statements
    for statement in statements:
        if statement.strip():
            try:
                cursor.execute(statement)
                # Consume any results to avoid "Unread result found" error
                try:
                    cursor.fetchall()
                except Exception:
                    pass  # No results to fetch, which is fine
            except MySQLError as e:
                # Some errors are expected (e.g., "column already exists" in idempotent migrations)
                if e.errno in (1060, 1061, 1062, 1068):  # Duplicate column/key/entry errors
                    print(f"  Note: {e.msg} (continuing)")
                else:
                    raise

    execution_time_ms = int((time.time() - start_time) * 1000)

    # Record the migration
    cursor.execute("""
        INSERT INTO schema_migrations (filename, checksum, applied_by, execution_time_ms)
        VALUES (%s, %s, %s, %s)
    """, (filename, checksum, os.getenv('USER', 'migrate.py'), execution_time_ms))

    print(f"  Applied in {execution_time_ms}ms")
    return True


def run_migrations(dry_run=False):
    """Run all pending migrations"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Ensure tracking table exists
        ensure_migrations_table(conn, cursor)

        # Get pending migrations
        pending = get_pending_migrations(cursor)

        if not pending:
            print("No pending migrations.")
            return True

        print(f"Found {len(pending)} pending migration(s):")
        for filepath in pending:
            print(f"  - {os.path.basename(filepath)}")

        if dry_run:
            print("\nDry run mode - no changes applied.")
            return True

        print()

        # Apply each migration in a transaction
        for filepath in pending:
            try:
                apply_migration(cursor, filepath)
                conn.commit()
            except Exception as e:
                conn.rollback()
                print(f"ERROR applying {os.path.basename(filepath)}: {e}")
                print("Rolling back and stopping.")
                return False

        print(f"\nSuccessfully applied {len(pending)} migration(s).")
        return True

    except MySQLError as e:
        print(f"Database error: {e}")
        return False
    finally:
        if conn:
            conn.close()


def show_status():
    """Show current migration status"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Check if tracking table exists
        cursor.execute("""
            SELECT COUNT(*) FROM information_schema.tables
            WHERE table_schema = DATABASE() AND table_name = 'schema_migrations'
        """)

        if cursor.fetchone()[0] == 0:
            print("Migration tracking table does not exist yet.")
            print("Run 'python migrate.py' to initialize.")
            return

        # Get applied migrations
        cursor.execute("""
            SELECT filename, applied_at, execution_time_ms
            FROM schema_migrations
            ORDER BY filename
        """)
        applied = cursor.fetchall()

        # Get all migration files
        all_files = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, '*.sql')))
        all_filenames = {os.path.basename(f) for f in all_files}
        applied_filenames = {row[0] for row in applied}

        print("Migration Status:")
        print("=" * 60)

        if applied:
            print("\nApplied migrations:")
            for filename, applied_at, exec_time in applied:
                status = "OK" if filename in all_filenames else "MISSING FILE"
                print(f"  [x] {filename}")
                print(f"      Applied: {applied_at}, Execution: {exec_time}ms")
                if status == "MISSING FILE":
                    print(f"      WARNING: Migration file no longer exists!")

        pending = all_filenames - applied_filenames - {'000_migrations_tracking.sql'}
        if pending:
            print(f"\nPending migrations ({len(pending)}):")
            for filename in sorted(pending):
                print(f"  [ ] {filename}")
        else:
            print("\nNo pending migrations.")

    except MySQLError as e:
        print(f"Database error: {e}")
    finally:
        if conn:
            conn.close()


def main():
    parser = argparse.ArgumentParser(description='Database Migration Runner')
    parser.add_argument('--status', action='store_true', help='Show migration status')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be applied without applying')
    args = parser.parse_args()

    # Validate environment
    if not os.getenv('DB_PASSWORD'):
        print("ERROR: DB_PASSWORD environment variable is required.")
        print("Set it or source the .env file: source /opt/shophosting/.env")
        sys.exit(1)

    if args.status:
        show_status()
    else:
        success = run_migrations(dry_run=args.dry_run)
        sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
