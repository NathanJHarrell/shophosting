"""
Module for enqueueing provisioning jobs
Called by your web application after customer signup
"""

import redis
from rq import Queue
from provisioning_worker import provision_customer_job
import logging

logger = logging.getLogger(__name__)

class ProvisioningQueue:
    """Handles enqueueing provisioning jobs"""
    
    def __init__(self, redis_host='localhost', redis_port=6379):
        self.redis_conn = redis.Redis(host=redis_host, port=redis_port, db=0)
        self.queue = Queue('provisioning', connection=self.redis_conn)
    
    def enqueue_customer(self, customer_data):
        """
        Enqueue a new customer for provisioning
        
        Args:
            customer_data (dict): Customer information
                Required fields:
                - customer_id: Unique customer ID
                - domain: Customer's domain
                - platform: 'woocommerce' or 'magento'
                - email: Customer email
                - web_port: Assigned port for web server
                
                Optional fields:
                - site_title: Site title
                - admin_user: Admin username (default: 'admin')
                - admin_password: Admin password (auto-generated if not provided)
                - memory_limit: Container memory limit (default: '1g')
                - cpu_limit: Container CPU limit (default: '1.0')
        
        Returns:
            job: RQ Job object
        """
        
        try:
            # Validate required fields
            required_fields = ['customer_id', 'domain', 'platform', 'email', 'web_port']
            for field in required_fields:
                if field not in customer_data:
                    raise ValueError(f"Missing required field: {field}")
            
            # Enqueue the job
            job = self.queue.enqueue(
                provision_customer_job,
                customer_data,
                job_timeout='30m',  # 30 minute timeout
                failure_ttl='7d'     # Keep failed jobs for 7 days
            )
            
            logger.info(f"Enqueued provisioning job {job.id} for customer {customer_data['customer_id']}")
            
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
    
    # Enqueue provisioning
    job = queue.enqueue_customer(customer_data)
    
    print(f"Provisioning job enqueued: {job.id}")
    print(f"Job status: {job.get_status()}")