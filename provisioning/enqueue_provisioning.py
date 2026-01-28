"""
Module for enqueueing provisioning jobs
Called by your web application after customer signup

Supports multi-server provisioning with automatic server selection.
"""

import os
import sys
import redis
from rq import Queue
from provisioning_worker import provision_customer_job
import logging

# Add webapp to path for model imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'webapp'))

logger = logging.getLogger(__name__)


class ProvisioningError(Exception):
    """Custom exception for provisioning failures"""
    pass


class ProvisioningQueue:
    """Handles enqueueing provisioning jobs with multi-server support"""

    def __init__(self, redis_host='localhost', redis_port=6379):
        self.redis_conn = redis.Redis(host=redis_host, port=redis_port, db=0)
        # Default queue for backward compatibility (single-server mode)
        self.default_queue = Queue('provisioning', connection=self.redis_conn)

    def get_queue_for_server(self, server):
        """
        Get or create a queue for a specific server.

        Args:
            server: Server model instance

        Returns:
            Queue: RQ Queue for this server
        """
        queue_name = server.get_queue_name()
        return Queue(queue_name, connection=self.redis_conn)

    def enqueue_customer(self, customer_data, server=None):
        """
        Enqueue a new customer for provisioning.

        If no server is specified, automatically selects the best available server
        based on load balancing (least customers).

        Args:
            customer_data (dict): Customer information
                Required fields:
                - customer_id: Unique customer ID
                - domain: Customer's domain
                - platform: 'woocommerce' or 'magento'
                - email: Customer email
                - web_port: Assigned port for web server

                Optional fields:
                - server_id: Explicit server assignment (auto-selected if not provided)
                - site_title: Site title
                - admin_user: Admin username (default: 'admin')
                - admin_password: Admin password (auto-generated if not provided)
                - memory_limit: Container memory limit (default: '1g')
                - cpu_limit: Container CPU limit (default: '1.0')

            server: Optional Server model instance. If not provided, auto-selects.

        Returns:
            tuple: (job, server) - RQ Job object and selected Server

        Raises:
            ProvisioningError: If no servers are available
            ValueError: If required fields are missing
        """
        try:
            # Validate required fields
            required_fields = ['customer_id', 'domain', 'platform', 'email', 'web_port']
            for field in required_fields:
                if field not in customer_data:
                    raise ValueError(f"Missing required field: {field}")

            # Auto-select server if not provided
            if server is None:
                server = self._select_server()

            if server is None:
                raise ProvisioningError("No available servers for provisioning")

            # Add server_id to job data
            customer_data['server_id'] = server.id

            # Get the appropriate queue for this server
            queue = self.get_queue_for_server(server)

            # Enqueue the job
            job = queue.enqueue(
                provision_customer_job,
                customer_data,
                job_timeout='30m',  # 30 minute timeout
                failure_ttl='7d'     # Keep failed jobs for 7 days
            )

            logger.info(
                f"Enqueued provisioning job {job.id} for customer {customer_data['customer_id']} "
                f"on server {server.name} (queue: {queue.name})"
            )

            return job, server

        except ProvisioningError:
            raise
        except Exception as e:
            logger.error(f"Failed to enqueue provisioning job: {e}")
            raise

    def _select_server(self):
        """
        Select the best server for provisioning.

        Uses ServerSelector for load-based selection.

        Returns:
            Server or None
        """
        try:
            from models import ServerSelector
            return ServerSelector.select_server()
        except ImportError:
            logger.warning("Could not import ServerSelector, using default queue")
            return None
        except Exception as e:
            logger.error(f"Server selection failed: {e}")
            return None

    def enqueue_customer_legacy(self, customer_data):
        """
        Legacy method for backward compatibility.
        Enqueues to default 'provisioning' queue without server selection.

        Returns:
            job: RQ Job object (not a tuple)
        """
        try:
            required_fields = ['customer_id', 'domain', 'platform', 'email', 'web_port']
            for field in required_fields:
                if field not in customer_data:
                    raise ValueError(f"Missing required field: {field}")

            job = self.default_queue.enqueue(
                provision_customer_job,
                customer_data,
                job_timeout='30m',
                failure_ttl='7d'
            )

            logger.info(f"Enqueued provisioning job {job.id} for customer {customer_data['customer_id']} (legacy mode)")

            return job

        except Exception as e:
            logger.error(f"Failed to enqueue provisioning job: {e}")
            raise

    def get_job_status(self, job_id):
        """Get status of a provisioning job"""
        from rq.job import Job

        try:
            job = Job.fetch(job_id, connection=self.redis_conn)

            return {
                'job_id': job.id,
                'status': job.get_status(),
                'created_at': job.created_at,
                'started_at': job.started_at,
                'ended_at': job.ended_at,
                'result': job.result if job.is_finished else None,
                'exc_info': job.exc_info if job.is_failed else None
            }

        except Exception as e:
            logger.error(f"Failed to fetch job status: {e}")
            return {'error': str(e)}


# Example usage in your web application
if __name__ == '__main__':
    # This is how your web app would use it

    queue = ProvisioningQueue()

    # Example: Customer just signed up
    customer_data = {
        'customer_id': '12345',
        'domain': 'testshop.com',
        'platform': 'woocommerce',
        'email': 'customer@testshop.com',
        'site_title': 'Test Shop',
        'web_port': 8001,
        'memory_limit': '1g',
        'cpu_limit': '1.0'
    }

    # Enqueue provisioning with auto server selection
    job, server = queue.enqueue_customer(customer_data)

    print(f"Provisioning job enqueued: {job.id}")
    print(f"Job status: {job.get_status()}")
    print(f"Assigned to server: {server.name} ({server.hostname})")
