"""
ShopHosting.io Provisioning Worker - Handles automated customer provisioning with Nginx
"""

import os
import subprocess
import secrets
import string
import logging
from pathlib import Path
import sys
sys.path.insert(0, '/opt/shophosting/webapp')
from models import PortManager
from jinja2 import Template
import redis
from rq import Worker, Queue
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import mysql.connector
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables from .env file
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(env_path)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/opt/shophosting/logs/provisioning_worker.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class ProvisioningError(Exception):
    """Custom exception for provisioning failures"""
    pass


class ProvisioningWorker:
    """Handles provisioning of new customer containers with Nginx reverse proxy"""

    def __init__(self, base_path=None):
        # Use environment variable for base path, with fallback
        if base_path is None:
            base_path = os.getenv('CUSTOMERS_BASE_PATH', '/var/customers')
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

        # Database configuration from environment variables
        self.db_config = {
            'host': os.getenv('DB_HOST', 'localhost'),
            'user': os.getenv('DB_USER', 'shophosting_app'),
            'password': os.getenv('DB_PASSWORD', ''),
            'database': os.getenv('DB_NAME', 'shophosting_db')
        }

        # Validate database password is set
        if not self.db_config['password']:
            logger.warning("DB_PASSWORD not set in environment variables!")
    
    def get_db_connection(self):
        """Get database connection"""
        try:
            return mysql.connector.connect(**self.db_config)
        except Exception as e:
            logger.error(f"Database connection failed: {e}")
            raise ProvisioningError(f"Database connection failed: {e}")
    
    def update_customer_status(self, customer_id, status, error_message=None):
        """Update customer provisioning status in database"""
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            
            if error_message:
                cursor.execute(
                    "UPDATE customers SET status = %s, error_message = %s, updated_at = %s WHERE id = %s",
                    (status, error_message, datetime.now(), customer_id)
                )
            else:
                cursor.execute(
                    "UPDATE customers SET status = %s, updated_at = %s WHERE id = %s",
                    (status, datetime.now(), customer_id)
                )
            
            conn.commit()
            cursor.close()
            conn.close()
            
            logger.info(f"Updated customer {customer_id} status to {status}")
            
        except Exception as e:
            logger.error(f"Failed to update customer status: {e}")
    
    def save_customer_credentials(self, customer_id, credentials):
        """Save customer credentials to database"""
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE customers 
                SET db_name = %s, db_user = %s, db_password = %s, 
                    admin_user = %s, admin_password = %s, web_port = %s
                WHERE id = %s
            """, (
                credentials['db_name'],
                credentials['db_user'],
                credentials['db_password'],
                credentials['admin_user'],
                credentials['admin_password'],
                credentials['web_port'],
                customer_id
            ))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            logger.info(f"Saved credentials for customer {customer_id}")
            
        except Exception as e:
            logger.error(f"Failed to save credentials: {e}")
    
    def generate_password(self, length=16):
        """Generate a secure random password"""
        alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
        return ''.join(secrets.choice(alphabet) for _ in range(length))
    
    def create_customer_directory(self, customer_id, platform='woocommerce'):
        """Create directory structure for customer"""
        customer_path = self.base_path / f"customer-{customer_id}"

        try:
            customer_path.mkdir(exist_ok=False)
            (customer_path / "volumes").mkdir()
            (customer_path / "volumes" / "db").mkdir()
            (customer_path / "volumes" / "files").mkdir()
            (customer_path / "logs").mkdir()

            # Create Varnish directory and config for Magento
            if platform == 'magento':
                varnish_dir = customer_path / "volumes" / "varnish"
                varnish_dir.mkdir()

                # Copy VCL template
                vcl_template = Path('/opt/shophosting/templates/magento-varnish.vcl.j2')
                if vcl_template.exists():
                    import shutil
                    shutil.copy(vcl_template, varnish_dir / "default.vcl")
            
            # Set appropriate permissions
            os.chmod(customer_path, 0o755)
            
            logger.info(f"Created directory structure for customer {customer_id}")
            return customer_path
            
        except FileExistsError:
            raise ProvisioningError(f"Customer {customer_id} already exists")
        except Exception as e:
            logger.error(f"Failed to create directory for {customer_id}: {e}")
            raise ProvisioningError(f"Directory creation failed: {e}")
    
    def generate_docker_compose(self, customer_path, config):
        """Generate docker-compose.yml from template"""
        
        # Select appropriate template
        if config['platform'] == 'woocommerce':
            template_file = '/opt/shophosting/templates/woocommerce-compose.yml.j2'
        elif config['platform'] == 'magento':
            template_file = '/opt/shophosting/templates/magento-compose.yml.j2'
        else:
            raise ProvisioningError(f"Unknown platform: {config['platform']}")
        
        try:
            with open(template_file, 'r') as f:
                template = Template(f.read())
            
            # Render template with customer config
            compose_content = template.render(**config)
            
            # Write to customer directory
            compose_path = customer_path / "docker-compose.yml"
            with open(compose_path, 'w') as f:
                f.write(compose_content)
            
            logger.info(f"Generated docker-compose.yml for {config['customer_id']}")
            return compose_path
            
        except Exception as e:
            logger.error(f"Failed to generate docker-compose: {e}")
            raise ProvisioningError(f"Compose generation failed: {e}")

    def is_port_in_use(self, port):
        """Check if a port is already in use"""
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.connect(('localhost', port))
                return True
            except ConnectionRefusedError:
                return False
            except Exception:
                return False

    def find_available_port(self, start_port):
        """Find next available port starting from start_port"""
        max_port = 8200  # Safety limit
        current_port = start_port

        while current_port <= max_port:
            if not self.is_port_in_use(current_port):
                if PortManager.is_port_available(current_port):
                    return current_port
            current_port += 1

        raise ProvisioningError(f"No available ports found starting from {start_port}")

    def update_docker_compose_port(self, customer_path, new_port):
        """Update the web_port in docker-compose.yml"""
        compose_path = customer_path / "docker-compose.yml"
        try:
            with open(compose_path, 'r') as f:
                content = f.read()

            # Replace ports mapping
            import re
            pattern = r'"(\d+):80"'
            replacement = f'"{new_port}:80"'
            new_content = re.sub(pattern, replacement, content)

            with open(compose_path, 'w') as f:
                f.write(new_content)

            logger.info(f"Updated docker-compose.yml to use port {new_port}")
            return True
        except Exception as e:
            logger.error(f"Failed to update docker-compose port: {e}")
            return False

    def start_containers(self, customer_path, config):
        """Start Docker containers using docker-compose"""
        try:
            result = subprocess.run(
                ['docker', 'compose', 'up', '-d'],
                cwd=customer_path,
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout
            )
            
            if result.returncode != 0:
                logger.error(f"Docker compose failed: {result.stderr}")
                raise ProvisioningError(f"Container startup failed: {result.stderr}")
            
            logger.info(f"Containers started successfully in {customer_path}")
            
            # Wait a bit for containers to fully start
            import time
            time.sleep(10)
            
            return True
            
        except subprocess.TimeoutExpired:
            raise ProvisioningError("Container startup timed out")
        except Exception as e:
            logger.error(f"Failed to start containers: {e}")
            raise ProvisioningError(f"Container startup failed: {e}")
    
    def configure_reverse_proxy(self, domain, customer_id, port):
        """Configure Nginx reverse proxy for customer domain"""
        
        # Nginx sites-available directory
        nginx_config_path = Path("/etc/nginx/sites-available")
        nginx_enabled_path = Path("/etc/nginx/sites-enabled")
        
        config_file = nginx_config_path / f"customer-{customer_id}.conf"
        enabled_link = nginx_enabled_path / f"customer-{customer_id}.conf"
        
        nginx_config = f"""server {{
    listen 80;
    listen [::]:80;
    server_name {domain};
    
    # Allow certbot to verify domain
    location /.well-known/acme-challenge/ {{
        root /var/www/html;
    }}
    
    # Redirect HTTP to HTTPS (will be enabled after SSL is set up)
    # return 301 https://$server_name$request_uri;
    
    # Temporary: proxy to container before SSL is set up
    location / {{
        proxy_pass http://localhost:{port};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Timeouts
        proxy_connect_timeout 600;
        proxy_send_timeout 600;
        proxy_read_timeout 600;
        send_timeout 600;
        
        # Large file uploads
        client_max_body_size 100M;
    }}
    
    # Logs
    access_log /var/log/nginx/customer-{customer_id}-access.log;
    error_log /var/log/nginx/customer-{customer_id}-error.log;
}}

# HTTPS configuration (will be uncommented after SSL cert is obtained)
# server {{
#     listen 443 ssl http2;
#     listen [::]:443 ssl http2;
#     server_name {domain};
#     
#     ssl_certificate /etc/letsencrypt/live/{domain}/fullchain.pem;
#     ssl_certificate_key /etc/letsencrypt/live/{domain}/privkey.pem;
#     
#     ssl_protocols TLSv1.2 TLSv1.3;
#     ssl_prefer_server_ciphers on;
#     ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256;
#     
#     location / {{
#         proxy_pass http://localhost:{port};
#         proxy_set_header Host $host;
#         proxy_set_header X-Real-IP $remote_addr;
#         proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
#         proxy_set_header X-Forwarded-Proto $scheme;
#         
#         proxy_connect_timeout 600;
#         proxy_send_timeout 600;
#         proxy_read_timeout 600;
#         send_timeout 600;
#         
#         client_max_body_size 100M;
#     }}
#     
#     access_log /var/log/nginx/customer-{customer_id}-access.log;
#     error_log /var/log/nginx/customer-{customer_id}-error.log;
# }}
"""
        
        try:
            # Write config file
            with open(config_file, 'w') as f:
                f.write(nginx_config)
            
            # Create symbolic link to enable the site
            if not enabled_link.exists():
                enabled_link.symlink_to(config_file)
            
            # Test nginx configuration
            test_result = subprocess.run(
                ['nginx', '-t'],
                capture_output=True,
                text=True
            )

            if test_result.returncode != 0:
                raise ProvisioningError(f"Nginx config test failed: {test_result.stderr}")

            # Reload nginx
            subprocess.run(
                ['systemctl', 'reload', 'nginx'],
                check=True
            )

            logger.info(f"Configured Nginx reverse proxy for {domain}")

            # Try to get SSL certificate using certbot --nginx (auto-configures nginx)
            try:
                admin_email = os.getenv('ADMIN_EMAIL', 'admin@shophosting.io')
                logger.info(f"Attempting to obtain SSL certificate for {domain}")
                certbot_result = subprocess.run(
                    ['certbot', '--nginx', '-d', domain,
                     '--non-interactive', '--agree-tos', '--email', admin_email,
                     '--redirect'],
                    capture_output=True,
                    text=True,
                    timeout=120
                )

                if certbot_result.returncode == 0:
                    logger.info(f"SSL certificate obtained and HTTPS enabled for {domain}")
                else:
                    logger.warning(f"Certbot failed for {domain}: {certbot_result.stderr}")
                    logger.warning("Site is accessible via HTTP only. SSL can be added later.")

            except subprocess.TimeoutExpired:
                logger.warning(f"Certbot timed out for {domain}. SSL can be added later.")
            except Exception as ssl_error:
                logger.warning(f"SSL setup failed for {domain}: {ssl_error}")
                logger.warning("Site is accessible via HTTP only. SSL can be added later.")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to configure Nginx: {e}")
            raise ProvisioningError(f"Nginx configuration failed: {e}")
    
    def install_application(self, customer_path, config):
        """Verify application container is running"""

        container_name = f"customer-{config['customer_id']}-web"

        try:
            import time

            # Wait for the web container to be running and responding
            logger.info(f"Waiting for container {container_name} to be ready...")
            for attempt in range(12):  # Wait up to 2 minutes
                result = subprocess.run(
                    f"docker inspect {container_name} --format '{{{{.State.Running}}}}'",
                    shell=True, capture_output=True, text=True, timeout=10
                )
                if 'true' in result.stdout.lower():
                    logger.info(f"Container {container_name} is running")
                    break
                logger.info(f"Waiting for container... attempt {attempt + 1}/12")
                time.sleep(10)

            if config['platform'] == 'woocommerce':
                # WordPress will auto-setup via web interface on first visit
                # Just verify container is accessible
                logger.info("WordPress container ready. Users can complete setup at the web interface.")
                commands = []  # No commands needed - WordPress handles setup via web
            elif config['platform'] == 'magento':
                # Magento is pre-installed in the image via environment variables
                # Just verify it's running
                commands = [
                    f"docker exec {container_name} php -v"  # Simple health check
                ]
            
            for cmd in commands:
                logger.info(f"Running: {cmd}")
                result = subprocess.run(
                    cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=600  # 10 minute timeout for installation
                )
                
                if result.returncode != 0:
                    logger.error(f"Installation command failed: {result.stderr}")
                    raise ProvisioningError(f"Application installation failed: {result.stderr}")
            
            logger.info(f"Application installed for customer {config['customer_id']}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to install application: {e}")
            raise ProvisioningError(f"Application installation failed: {e}")
    
    def send_welcome_email(self, config):
        """Send welcome email with credentials to customer"""
        
        # Email configuration from environment variables
        sender_email = os.getenv('SMTP_FROM', 'noreply@shophosting.io')
        smtp_server = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
        smtp_port = int(os.getenv('SMTP_PORT') or '587')
        smtp_user = os.getenv('SMTP_USER')
        smtp_password = os.getenv('SMTP_PASSWORD')
        
        # Skip email if not configured
        if not smtp_user or not smtp_password:
            logger.warning("SMTP not configured, skipping welcome email")
            return False
        
        message = MIMEMultipart()
        message["From"] = sender_email
        message["To"] = config['email']
        message["Subject"] = f"Your {config['platform'].title()} Store is Ready!"
        
        # Determine admin URL based on platform
        if config['platform'] == 'woocommerce':
            admin_url = f"http://{config['domain']}/wp-admin"
        else:
            admin_url = f"http://{config['domain']}/admin"
        
        body = f"""
Hello!

Your {config['platform'].title()} store has been successfully provisioned and is ready to use!

Store URL: http://{config['domain']}
Admin URL: {admin_url}

Admin Username: {config['admin_user']}
Admin Password: {config['admin_password']}

IMPORTANT: Please change your password after your first login.

Your store is currently accessible via HTTP. HTTPS will be automatically enabled once your domain's DNS is fully propagated.

If you have any questions, please contact our support team at support@shophosting.io.

Best regards,
ShopHosting.io Team
        """
        
        message.attach(MIMEText(body, "plain"))
        
        try:
            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.starttls()
                server.login(smtp_user, smtp_password)
                server.send_message(message)
            
            logger.info(f"Welcome email sent to {config['email']}")
            return True
            
        except Exception as e:
            logger.warning(f"Failed to send email: {e}")
            return False
    
    def rollback(self, customer_id, customer_path):
        """Rollback provisioning in case of failure"""
        logger.warning(f"Rolling back provisioning for customer {customer_id}")
        
        try:
            # Stop and remove containers
            if customer_path and customer_path.exists():
                subprocess.run(
                    ['docker', 'compose', 'down', '-v'],
                    cwd=customer_path,
                    capture_output=True,
                    timeout=60
                )
            
            # Remove directory
            import shutil
            if customer_path and customer_path.exists():
                shutil.rmtree(customer_path)
            
            # Remove Nginx config
            nginx_available = Path(f"/etc/nginx/sites-available/customer-{customer_id}.conf")
            nginx_enabled = Path(f"/etc/nginx/sites-enabled/customer-{customer_id}.conf")
            
            if nginx_enabled.exists():
                nginx_enabled.unlink()
            if nginx_available.exists():
                nginx_available.unlink()
            
            # Reload nginx
            try:
                subprocess.run(['systemctl', 'reload', 'nginx'], check=True)
            except:
                pass
            
            logger.info(f"Rollback completed for customer {customer_id}")
            
        except Exception as e:
            logger.error(f"Rollback failed: {e}")
    
    def provision_customer(self, job_data):
        """Main provisioning function - orchestrates all steps"""
        
        customer_id = job_data['customer_id']
        customer_path = None
        
        logger.info(f"Starting provisioning for customer {customer_id}")
        self.update_customer_status(customer_id, 'provisioning')
        
        try:
            # Step 1: Create directory structure
            customer_path = self.create_customer_directory(customer_id, job_data['platform'])

            # Step 2: Generate credentials and config
            db_password = self.generate_password()
            admin_password = job_data.get('admin_password') or self.generate_password()
            
            config = {
                'customer_id': customer_id,
                'domain': job_data['domain'],
                'platform': job_data['platform'],
                'site_title': job_data.get('site_title', 'My Store'),
                'email': job_data['email'],
                'admin_user': job_data.get('admin_user', 'admin'),
                'admin_password': admin_password,
                'db_name': f"customer_{customer_id}",
                'db_user': f"customer_{customer_id}",
                'db_password': db_password,
                'db_root_password': self.generate_password(),
                'container_prefix': f"customer-{customer_id}",
                'web_port': job_data['web_port'],
                'memory_limit': job_data.get('memory_limit', '1g'),
                'cpu_limit': job_data.get('cpu_limit', '1.0')
            }
            
            # Step 3: Generate docker-compose file
            self.generate_docker_compose(customer_path, config)

            # Step 3.5: Validate and fix port if needed
            if self.is_port_in_use(config['web_port']):
                logger.warning(f"Port {config['web_port']} is in use, finding alternative...")
                new_port = self.find_available_port(config['web_port'])
                logger.info(f"Using port {new_port} instead of {config['web_port']}")
                config['web_port'] = new_port
                self.update_docker_compose_port(customer_path, new_port)

            # Step 4: Start containers
            self.start_containers(customer_path, config)
            
            # Step 5: Configure reverse proxy
            self.configure_reverse_proxy(config['domain'], customer_id, config['web_port'])
            
            # Step 6: Install application
            self.install_application(customer_path, config)
            
            # Step 7: Save credentials to database
            self.save_customer_credentials(customer_id, config)
            
            # Step 8: Send welcome email
            self.send_welcome_email(config)
            
            # Step 9: Update status to active
            self.update_customer_status(customer_id, 'active')
            
            logger.info(f"Provisioning completed successfully for customer {customer_id}")
            
            return {
                'status': 'success',
                'customer_id': customer_id,
                'domain': config['domain'],
                'admin_user': config['admin_user'],
                'admin_password': admin_password
            }
            
        except ProvisioningError as e:
            logger.error(f"Provisioning failed for customer {customer_id}: {e}")
            
            # Rollback
            if customer_path:
                self.rollback(customer_id, customer_path)
            
            # Update status to failed
            self.update_customer_status(customer_id, 'failed', str(e))
            
            return {
                'status': 'failed',
                'customer_id': customer_id,
                'error': str(e)
            }
        
        except Exception as e:
            logger.error(f"Unexpected error provisioning customer {customer_id}: {e}")
            
            # Rollback
            if customer_path:
                self.rollback(customer_id, customer_path)
            
            # Update status to failed
            self.update_customer_status(customer_id, 'failed', f"Unexpected error: {str(e)}")
            
            return {
                'status': 'failed',
                'customer_id': customer_id,
                'error': f"Unexpected error: {str(e)}"
            }


# RQ job function
def provision_customer_job(job_data):
    """Job function called by RQ worker"""
    worker = ProvisioningWorker()
    return worker.provision_customer(job_data)


if __name__ == '__main__':
    # Start the worker with configuration from environment variables
    redis_host = os.getenv('REDIS_HOST', 'localhost')
    redis_port = int(os.getenv('REDIS_PORT', 6379))

    redis_conn = redis.Redis(host=redis_host, port=redis_port, db=0)

    # Removed Connection context manager as it's no longer supported
    worker = Worker(['provisioning'], connection=redis_conn)
    logger.info(f"ShopHosting.io Provisioning worker started (Redis: {redis_host}:{redis_port})")
    worker.work()