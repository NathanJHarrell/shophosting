"""
ShopHosting.io Backup Worker - Handles customer backup and restore jobs
"""

import os
import subprocess
import logging
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, '/opt/shophosting/webapp')
from models import Customer, CustomerBackupJob
from dotenv import load_dotenv

# Load environment variables
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(env_path)

# Configure logging - only add file handler when running as worker
# When imported by webapp, just use basic console logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Add console handler if no handlers exist
if not logger.handlers:
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logger.addHandler(console_handler)


def _configure_file_logging():
    """Configure file logging for worker mode"""
    file_handler = logging.FileHandler('/opt/shophosting/logs/backup_worker.log')
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logger.addHandler(file_handler)


class BackupError(Exception):
    """Custom exception for backup operations"""
    pass


class BackupWorker:
    """Handles customer backup and restore operations"""

    def __init__(self):
        self.scripts_path = Path('/opt/shophosting/scripts')

    def create_backup(self, job_id):
        """
        Create a manual backup for a customer.
        """
        job = CustomerBackupJob.get_by_id(job_id)
        if not job:
            raise BackupError(f"Job {job_id} not found")

        customer = Customer.get_by_id(job.customer_id)
        if not customer:
            raise BackupError(f"Customer {job.customer_id} not found")

        logger.info(f"Starting backup job {job_id} for customer {customer.id} (type: {job.backup_type})")

        # Update job status to running
        job.update_status('running')

        try:
            # Run backup script
            result = subprocess.run(
                ['sudo', str(self.scripts_path / 'customer-backup.sh'),
                 str(customer.id), job.backup_type],
                capture_output=True,
                text=True,
                timeout=600  # 10 minute timeout
            )

            if result.returncode != 0:
                # Combine stdout and stderr for error message since script logs to stdout
                error_output = result.stderr or result.stdout or 'Unknown error'
                # Extract the last ERROR line if present
                for line in reversed(error_output.split('\n')):
                    if 'ERROR:' in line:
                        error_output = line.split('ERROR:')[-1].strip()
                        break
                raise BackupError(f"Backup failed: {error_output}")

            # Extract snapshot ID from output
            snapshot_id = None
            for line in result.stdout.split('\n'):
                if line.startswith('SNAPSHOT_ID='):
                    snapshot_id = line.split('=')[1].strip()
                    break

            job.snapshot_id = snapshot_id
            job.update_status('completed')

            logger.info(f"Backup job {job_id} completed successfully. Snapshot: {snapshot_id}")
            return snapshot_id

        except subprocess.TimeoutExpired:
            job.update_status('failed', 'Backup timed out after 10 minutes')
            raise BackupError("Backup timed out")

        except Exception as e:
            logger.error(f"Backup job {job_id} failed: {e}")
            job.update_status('failed', str(e))
            raise

    def restore_backup(self, job_id):
        """
        Restore a customer's site from a backup snapshot.
        """
        job = CustomerBackupJob.get_by_id(job_id)
        if not job:
            raise BackupError(f"Job {job_id} not found")

        if not job.snapshot_id:
            raise BackupError("No snapshot ID specified for restore")

        customer = Customer.get_by_id(job.customer_id)
        if not customer:
            raise BackupError(f"Customer {job.customer_id} not found")

        logger.info(f"Starting restore job {job_id} for customer {customer.id} "
                   f"(snapshot: {job.snapshot_id}, type: {job.backup_type})")

        # Update job status to running
        job.update_status('running')

        try:
            # Determine source (manual or daily) based on snapshot tags
            source = self._determine_backup_source(job.snapshot_id, customer.id)

            # Run restore script
            result = subprocess.run(
                ['sudo', str(self.scripts_path / 'customer-restore.sh'),
                 str(customer.id), job.snapshot_id, job.backup_type, source],
                capture_output=True,
                text=True,
                timeout=1200  # 20 minute timeout for restores
            )

            if result.returncode != 0:
                raise BackupError(f"Restore script failed: {result.stderr}")

            job.update_status('completed')

            logger.info(f"Restore job {job_id} completed successfully")
            return True

        except subprocess.TimeoutExpired:
            job.update_status('failed', 'Restore timed out after 20 minutes')
            raise BackupError("Restore timed out")

        except Exception as e:
            logger.error(f"Restore job {job_id} failed: {e}")
            job.update_status('failed', str(e))
            raise

    def _determine_backup_source(self, snapshot_id, customer_id):
        """Determine if snapshot is from manual or daily backups"""
        # Try manual repository first
        try:
            result = subprocess.run(
                ['sudo', 'bash', '-c',
                 f'export RESTIC_REPOSITORY="sftp:sh-backup@15.204.249.219:/home/sh-backup/manual-backups" && '
                 f'export RESTIC_PASSWORD_FILE="/opt/shophosting/.manual-restic-password" && '
                 f'restic snapshots --json {snapshot_id}'],
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode == 0 and snapshot_id in result.stdout:
                return 'manual'
        except Exception:
            pass

        # Fall back to daily repository
        return 'daily'


# =============================================================================
# RQ Job Functions
# =============================================================================

def create_backup_job(job_id):
    """RQ job wrapper for creating backup"""
    worker = BackupWorker()
    return worker.create_backup(job_id)


def restore_backup_job(job_id):
    """RQ job wrapper for restoring backup"""
    worker = BackupWorker()
    return worker.restore_backup(job_id)


if __name__ == '__main__':
    # Run as RQ worker
    from redis import Redis
    from rq import Worker, Queue

    # Enable file logging when running as worker
    _configure_file_logging()

    redis_host = os.getenv('REDIS_HOST', 'localhost')
    redis_conn = Redis(host=redis_host, port=6379)

    queues = [Queue('backups', connection=redis_conn)]

    logger.info("Starting backup worker...")
    worker = Worker(queues, connection=redis_conn)
    worker.work()
