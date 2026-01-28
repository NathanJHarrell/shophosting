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


class ProvisioningLogHandler(logging.Handler):
    """Custom log handler that saves logs to database"""

    def __init__(self, customer_id=None, job_id=None):
        super().__init__()
        self.customer_id = customer_id
        self.job_id = job_id

    def emit(self, record):
        try:
            log_level = record.levelname
            message = self.format(record)

            step_name = None
            if 'directory structure' in message.lower():
                step_name = 'create_directory'
            elif 'docker-compose' in message.lower():
                step_name = 'generate_config'
            elif 'port' in message.lower() and ('in use' in message.lower() or 'alternative' in message.lower()):
                step_name = 'port_allocation'
            elif 'containers started' in message.lower() or 'docker compose' in message.lower():
                step_name = 'start_containers'
            elif 'nginx' in message.lower() or 'reverse proxy' in message.lower():
                step_name = 'configure_proxy'
            elif 'ssl' in message.lower() or 'certbot' in message.lower():
                step_name = 'ssl_cert'
            elif 'container' in message.lower() and ('waiting' in message.lower() or 'ready' in message.lower()):
                step_name = 'wait_container'
            elif 'wordpress' in message.lower() or 'magento' in message.lower():
                step_name = 'install_app'
            elif 'credentials' in message.lower():
                step_name = 'save_credentials'
            elif 'welcome email' in message.lower():
                step_name = 'send_email'
            elif 'completed successfully' in message.lower():
                step_name = 'complete'
            elif 'failed' in message.lower() or 'error' in message.lower():
                step_name = 'error'
            elif 'rollback' in message.lower():
                step_name = 'rollback'

            self._save_log(log_level, message, step_name)
        except Exception as e:
            pass

    def _save_log(self, log_level, message, step_name=None):
        if not self.customer_id:
            return

        try:
            import mysql.connector
            from datetime import datetime

            db_config = {
                'host': os.getenv('DB_HOST', 'localhost'),
                'user': os.getenv('DB_USER', 'shophosting_app'),
                'password': os.getenv('DB_PASSWORD', ''),
                'database': os.getenv('DB_NAME', 'shophosting_db')
            }

            conn = mysql.connector.connect(**db_config)
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO provisioning_logs
                (job_id, customer_id, log_level, message, step_name, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                self.job_id,
                self.customer_id,
                log_level,
                message,
                step_name,
                datetime.now()
            ))

            conn.commit()
            cursor.close()
            conn.close()
        except Exception:
            pass


class ProvisioningWorker:
    """Handles provisioning of new customer containers with Nginx reverse proxy"""

    def __init__(self, base_path=None, server_id=None):
        # Use environment variable for base path, with fallback
        if base_path is None:
            base_path = os.getenv('CUSTOMERS_BASE_PATH', '/var/customers')
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

        # Server ID for multi-server support
        self.server_id = server_id or os.getenv('SERVER_ID')
        if self.server_id:
            self.server_id = int(self.server_id)

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

        self.current_job_id = None
        self.current_customer_id = None

        # Send initial heartbeat if server_id is set
        if self.server_id:
            self.update_server_heartbeat()

    def update_server_heartbeat(self):
        """Update server heartbeat timestamp in database"""
        if not self.server_id:
            return

        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE servers SET last_heartbeat = NOW() WHERE id = %s",
                (self.server_id,)
            )
            conn.commit()
            cursor.close()
            conn.close()
            logger.debug(f"Updated heartbeat for server {self.server_id}")
        except Exception as e:
            logger.warning(f"Failed to update server heartbeat: {e}")
    
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

    def update_job_status(self, job_id, status, error_message=None):
        """Update provisioning job status in database"""
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()

            now = datetime.now()

            if status == 'started':
                cursor.execute(
                    "UPDATE provisioning_jobs SET status = %s, started_at = %s WHERE job_id = %s",
                    (status, now, job_id)
                )
            elif status in ('finished', 'failed'):
                if error_message:
                    cursor.execute(
                        "UPDATE provisioning_jobs SET status = %s, finished_at = %s, error_message = %s WHERE job_id = %s",
                        (status, now, error_message, job_id)
                    )
                else:
                    cursor.execute(
                        "UPDATE provisioning_jobs SET status = %s, finished_at = %s WHERE job_id = %s",
                        (status, now, job_id)
                    )

            conn.commit()
            cursor.close()
            conn.close()

            logger.info(f"Updated job {job_id} status to {status}")

        except Exception as e:
            logger.error(f"Failed to update job status: {e}")
    
    def save_customer_credentials(self, customer_id, credentials):
        """Save customer credentials to database"""
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()

            # Include server_id if present in credentials
            server_id = credentials.get('server_id')
            if server_id:
                cursor.execute("""
                    UPDATE customers
                    SET db_name = %s, db_user = %s, db_password = %s,
                        admin_user = %s, admin_password = %s, web_port = %s, server_id = %s
                    WHERE id = %s
                """, (
                    credentials['db_name'],
                    credentials['db_user'],
                    credentials['db_password'],
                    credentials['admin_user'],
                    credentials['admin_password'],
                    credentials['web_port'],
                    server_id,
                    customer_id
                ))
            else:
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
            # Check if directory already exists - clean up if so (idempotent provisioning)
            if customer_path.exists():
                logger.info(f"Customer directory {customer_path} already exists, cleaning up first")
                try:
                    subprocess.run(
                        ['docker', 'compose', 'down', '-v', '--remove-orphans'],
                        cwd=str(customer_path),
                        capture_output=True,
                        timeout=60
                    )
                except Exception as e:
                    logger.warning(f"Failed to stop existing containers: {e}")
                
                check_result = subprocess.run(
                    ['docker', 'ps', '-a', '--format', '{{.Names}}'],
                    capture_output=True, text=True
                )
                if f"customer-{customer_id}" in check_result.stdout:
                    logger.warning(f"Containers for customer {customer_id} still exist, will be removed during retry")
            
            # Create directory structure (exist_ok=True for idempotency)
            customer_path.mkdir(parents=True, exist_ok=True)
            (customer_path / "volumes").mkdir(exist_ok=True)
            (customer_path / "volumes" / "db").mkdir(exist_ok=True)
            (customer_path / "volumes" / "files").mkdir(exist_ok=True)
            (customer_path / "logs").mkdir(exist_ok=True)

            # Create Varnish directory and config for Magento
            if platform == 'magento':
                varnish_dir = customer_path / "volumes" / "varnish"
                varnish_dir.mkdir(exist_ok=True)

                vcl_template = Path('/opt/shophosting/templates/magento-varnish.vcl.j2')
                if vcl_template.exists():
                    import shutil
                    dest = varnish_dir / "default.vcl"
                    if dest.exists() and dest.is_dir():
                        shutil.rmtree(dest)
                    shutil.copy(vcl_template, dest)
            
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
"""

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
#    }}
#   
#    access_log /var/log/nginx/customer-{customer_id}-access.log;
#    error_log /var/log/nginx/customer-{customer_id}-error.log;

#}}
#"""
        
        try:
            import tempfile

            # Write config to temp file first, then use sudo to move it
            with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
                f.write(nginx_config)
                temp_file = f.name

            # Use sudo to copy config to sites-available
            result = subprocess.run(
                ['sudo', 'cp', temp_file, str(config_file)],
                capture_output=True,
                text=True
            )
            if result.returncode != 0:
                raise ProvisioningError(f"Failed to copy nginx config: {result.stderr}")

            # Clean up temp file
            os.unlink(temp_file)

            # Use sudo to create symbolic link to enable the site
            if not enabled_link.exists():
                result = subprocess.run(
                    ['sudo', 'ln', '-s', str(config_file), str(enabled_link)],
                    capture_output=True,
                    text=True
                )
                if result.returncode != 0:
                    raise ProvisioningError(f"Failed to enable nginx site: {result.stderr}")

            # Test nginx configuration
            test_result = subprocess.run(
                ['sudo', 'nginx', '-t'],
                capture_output=True,
                text=True
            )

            if test_result.returncode != 0:
                raise ProvisioningError(f"Nginx config test failed: {test_result.stderr}")

            # Reload nginx
            subprocess.run(
                ['sudo', 'systemctl', 'reload', 'nginx'],
                check=True
            )

            logger.info(f"Configured Nginx reverse proxy for {domain}")

            # Try to get SSL certificate using certbot --nginx (auto-configures nginx)
            try:
                admin_email = os.getenv('ADMIN_EMAIL', 'admin@shophosting.io')
                logger.info(f"Attempting to obtain SSL certificate for {domain}")
                certbot_result = subprocess.run(
                    ['sudo', 'certbot', '--nginx', '-d', domain,
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

            # Magento has more dependencies and takes longer to start
            max_attempts = 60 if config['platform'] == 'magento' else 30  # 10 min for Magento, 5 min for WooCommerce

            # Wait for the web container to be running and responding
            logger.info(f"Waiting for container {container_name} to be ready...")
            container_running = False
            for attempt in range(max_attempts):
                result = subprocess.run(
                    f"docker inspect {container_name} --format '{{{{.State.Status}}}}'",
                    shell=True, capture_output=True, text=True, timeout=10
                )
                if 'running' in result.stdout.lower():
                    logger.info(f"Container {container_name} is running")
                    container_running = True
                    break

                # Check if container exited with error
                exit_check = subprocess.run(
                    f"docker inspect {container_name} --format '{{{{.State.Status}}}} {{{{.State.ExitCode}}}}'",
                    shell=True, capture_output=True, text=True, timeout=10
                )
                if 'exited' in exit_check.stdout.lower():
                    logs = subprocess.run(
                        f"docker logs {container_name} --tail 20",
                        shell=True, capture_output=True, text=True, timeout=10
                    )
                    raise ProvisioningError(f"Container exited unexpectedly: {logs.stderr or logs.stdout}")

                logger.info(f"Waiting for container... attempt {attempt + 1}/{max_attempts}")
                time.sleep(10)

            if not container_running:
                raise ProvisioningError(f"Container {container_name} failed to start within timeout")

            if config['platform'] == 'woocommerce':
                # WordPress was installed via WP-CLI in entrypoint-wrapper.sh
                # Verify installation was successful
                logger.info("Verifying WordPress installation...")
                container_name = f"customer-{config['customer_id']}-web"
                
                for attempt in range(30):  # Wait up to 5 minutes for WP install
                    wp_check = subprocess.run(
                        f"docker exec {container_name} wp core is-installed --allow-root 2>/dev/null",
                        shell=True, capture_output=True, text=True, timeout=30
                    )
                    if wp_check.returncode == 0:
                        logger.info("WordPress installation verified successfully")
                        
                        # Get WordPress version for logging
                        wp_version = subprocess.run(
                            f"docker exec {container_name} wp core version --allow-root",
                            shell=True, capture_output=True, text=True, timeout=30
                        )
                        if wp_version.returncode == 0:
                            logger.info(f"WordPress version: {wp_version.stdout.strip()}")
                        
                        break
                    
                    logger.info(f"Waiting for WordPress installation... attempt {attempt + 1}/30")
                    time.sleep(10)
                else:
                    logger.warning("WordPress installation verification timed out, but container is running")
                
                commands = []
            elif config['platform'] == 'magento':
                # Magento is pre-installed in the image via environment variables
                # Wait a bit more for PHP-FPM to be ready, then verify
                logger.info("Waiting for Magento to initialize...")
                time.sleep(15)
                commands = [
                    f"docker exec {container_name} php -v"  # Simple health check
                ]

            # Final check before running commands
            final_check = subprocess.run(
                f"docker inspect {container_name} --format '{{{{.State.Status}}}}'",
                shell=True, capture_output=True, text=True, timeout=10
            )
            if 'running' not in final_check.stdout.lower():
                raise ProvisioningError(f"Container {container_name} is not running (status: {final_check.stdout.strip()})")   

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
                    error_detail = result.stderr.strip() or result.stdout.strip() or f"Exit code {result.returncode}"
                    logger.error(f"Installation command failed: {error_detail}")
                    raise ProvisioningError(f"Application installation failed: {error_detail}")
            
            logger.info(f"Application installed for customer {config['customer_id']}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to install application: {e}")
            raise ProvisioningError(f"Application installation failed: {e}")
    
    def send_welcome_email(self, config):
        """Send welcome email with credentials to customer"""
        try:
            # Use the shared email service for styled emails
            sys.path.insert(0, '/opt/shophosting/webapp')
            from email_service import email_service
            
            # Prevent duplicate emails by checking if customer is already active
            try:
                conn = self.get_db_connection()
                cursor = conn.cursor()
                cursor.execute("SELECT status FROM customers WHERE id = %s", (config['customer_id'],))
                row = cursor.fetchone()
                cursor.close()
                conn.close()
                if row and row[0] == 'active':
                    logger.info(f"Customer {config['customer_id']} already active, skipping welcome email")
                    return True
            except Exception as check_err:
                logger.warning(f"Could not check customer status: {check_err}")

            result = email_service.send_welcome_email(
                to_email=config['email'],
                domain=config['domain'],
                platform=config['platform'],
                admin_user=config['admin_user'],
                admin_password=config['admin_password']
            )

            if result:
                logger.info(f"Welcome email sent to {config['email']}")
            else:
                logger.warning(f"Welcome email not sent to {config['email']}")

            return result

        except Exception as e:
            logger.warning(f"Failed to send welcome email: {e}")
            return False
    
    def setup_backup_cron(self, customer_id, customer_path):
        """Step 10: Configure automated backups for customer data"""
        logger.info(f"Setting up automated backups for customer {customer_id}")
        
        try:
            BACKUP_SCRIPT = "/opt/shophosting/scripts/customer-backup.sh"
            
            # Verify backup script exists
            if not os.path.exists(BACKUP_SCRIPT):
                logger.warning(f"Backup script not found at {BACKUP_SCRIPT}")
                return False
            
            # Create crontab entry for this customer
            # Run backup every 6 hours
            cron_entry = f"0 */6 * * * {BACKUP_SCRIPT} {customer_id} >> /var/log/shophosting-customer-backup.log 2>&1"
            
            # Get existing crontab
            result = subprocess.run(
                ['crontab', '-l'],
                capture_output=True,
                text=True
            )
            existing_cron = result.stdout if result.returncode == 0 else ""
            
            # Check if entry already exists
            if f"customer-backup.sh {customer_id}" in existing_cron:
                logger.info(f"Backup cron already exists for customer {customer_id}")
                return True
            
            # Add new crontab entry
            new_cron = existing_cron.strip() + "\n" + cron_entry + "\n"
            
            # Write new crontab
            process = subprocess.run(
                ['crontab', '-'],
                input=new_cron,
                text=True,
                capture_output=True
            )
            
            if process.returncode == 0:
                logger.info(f"Backup cron configured for customer {customer_id} (every 6 hours)")
                return True
            else:
                logger.warning(f"Failed to configure backup cron: {process.stderr}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to setup backup cron for customer {customer_id}: {e}")
            return False
    
    def rollback(self, customer_id, customer_path):
        """Rollback provisioning in case of failure"""
        logger.warning(f"Rolling back provisioning for customer {customer_id}")
        
        try:
            # Stop and remove containers with volume removal
            if customer_path and customer_path.exists():
                subprocess.run(
                    ['docker', 'compose', 'down', '-v', '--remove-orphans'],
                    cwd=str(customer_path),
                    capture_output=True,
                    timeout=120
                )
                
                # Short delay to ensure volumes are unmounted
                import time
                time.sleep(2)
                
                # Change ownership of all files to ensure deletion works
                subprocess.run(
                    ['sudo', 'chmod', '-R', '777', str(customer_path)],
                    capture_output=True,
                    timeout=30
                )
                
                # Remove directory with sudo (Docker creates files as root)
                import shutil
                result = subprocess.run(
                    ['sudo', 'rm', '-rf', str(customer_path)],
                    capture_output=True,
                    timeout=60
                )
                if result.returncode != 0:
                    logger.warning(f"Rollback rm failed: {result.stderr.decode()}")
            
            # Remove Nginx config (using sudo)
            nginx_available = Path(f"/etc/nginx/sites-available/customer-{customer_id}.conf")
            nginx_enabled = Path(f"/etc/nginx/sites-enabled/customer-{customer_id}.conf")

            if nginx_enabled.exists():
                subprocess.run(['sudo', 'rm', '-f', str(nginx_enabled)], capture_output=True)
            if nginx_available.exists():
                subprocess.run(['sudo', 'rm', '-f', str(nginx_available)], capture_output=True)

            # Reload nginx
            try:
                subprocess.run(['sudo', 'systemctl', 'reload', 'nginx'], check=True)
            except:
                pass
            
            logger.info(f"Rollback completed for customer {customer_id}")
            
        except Exception as e:
            logger.error(f"Rollback failed: {e}")
    
    def provision_customer(self, job_data, rq_job_id=None):
        """Main provisioning function - orchestrates all steps"""

        customer_id = job_data['customer_id']
        customer_path = None

        self.current_job_id = rq_job_id
        self.current_customer_id = customer_id

        log_handler = ProvisioningLogHandler(customer_id=customer_id, job_id=rq_job_id)
        log_handler.setLevel(logging.INFO)
        logger.addHandler(log_handler)

        logger.info(f"Starting provisioning for customer {customer_id}")

        if rq_job_id:
            self.update_job_status(rq_job_id, 'started')

        self.update_customer_status(customer_id, 'provisioning')

        existing_path = self.base_path / f"customer-{customer_id}"
        if existing_path.exists():
            logger.info(f"Cleaning up existing resources for customer {customer_id}")
            try:
                subprocess.run(
                    ['docker', 'compose', 'down', '-v', '--remove-orphans'],
                    cwd=str(existing_path),
                    capture_output=True,
                    timeout=60
                )
                subprocess.run(
                    ['sudo', 'rm', '-rf', str(existing_path)],
                    capture_output=True,
                    timeout=30
                )
            except Exception as e:
                logger.warning(f"Cleanup of existing resources failed: {e}")

        try:
            customer_path = self.create_customer_directory(customer_id, job_data['platform'])

            db_password = self.generate_password()
            admin_password = job_data.get('admin_password') or self.generate_password()
            
            email_username = job_data['email'].split('@')[0]
            admin_user = ''.join(c for c in email_username if c.isalnum())[:60] or 'admin'
            
            config = {
                'customer_id': customer_id,
                'domain': job_data['domain'],
                'platform': job_data['platform'],
                'site_title': job_data.get('site_title', job_data['domain']),
                'email': job_data['email'],
                'admin_user': job_data.get('admin_user', admin_user),
                'admin_password': admin_password,
                'db_name': f"customer_{customer_id}",
                'db_user': f"customer_{customer_id}",
                'db_password': db_password,
                'db_root_password': self.generate_password(),
                'container_prefix': f"customer-{customer_id}",
                'web_port': job_data['web_port'],
                'server_id': job_data.get('server_id') or self.server_id,
                'memory_limit': job_data.get('memory_limit', '1g'),
                'cpu_limit': job_data.get('cpu_limit', '1.0')
            }

            logger.info(f"Generated configuration for customer {customer_id}")
            self.generate_docker_compose(customer_path, config)

            if self.is_port_in_use(config['web_port']):
                logger.warning(f"Port {config['web_port']} is in use, finding alternative...")
                new_port = self.find_available_port(config['web_port'])
                logger.info(f"Using port {new_port} instead of {config['web_port']}")
                config['web_port'] = new_port
                self.update_docker_compose_port(customer_path, new_port)

            logger.info(f"Starting containers for customer {customer_id}")
            self.start_containers(customer_path, config)
            
            logger.info(f"Configuring Nginx reverse proxy for {config['domain']}")
            self.configure_reverse_proxy(config['domain'], customer_id, config['web_port'])
            
            logger.info(f"Installing {job_data['platform']} application")
            self.install_application(customer_path, config)
            
            logger.info(f"Saving customer credentials to database")
            self.save_customer_credentials(customer_id, config)
            
            logger.info(f"Sending welcome email to {config['email']}")
            self.send_welcome_email(config)
            
            # Step 10: Configure automated backups for this customer
            logger.info(f"Setting up automated backups for customer {customer_id}")
            self.setup_backup_cron(customer_id, customer_path)
            
            logger.info(f"Provisioning completed successfully for customer {customer_id}")
            self.update_customer_status(customer_id, 'active')

            if rq_job_id:
                self.update_job_status(rq_job_id, 'finished')

            return {
                'status': 'success',
                'customer_id': customer_id,
                'domain': config['domain'],
                'admin_user': config['admin_user'],
                'admin_password': admin_password
            }

        except ProvisioningError as e:
            logger.error(f"Provisioning failed for customer {customer_id}: {e}")

            if customer_path:
                self.rollback(customer_id, customer_path)

            self.update_customer_status(customer_id, 'failed', str(e))

            if rq_job_id:
                self.update_job_status(rq_job_id, 'failed', str(e))

            return {
                'status': 'failed',
                'customer_id': customer_id,
                'error': str(e)
            }

        except Exception as e:
            logger.error(f"Unexpected error provisioning customer {customer_id}: {e}")

            if customer_path:
                self.rollback(customer_id, customer_path)

            self.update_customer_status(customer_id, 'failed', f"Unexpected error: {str(e)}")

            if rq_job_id:
                self.update_job_status(rq_job_id, 'failed', f"Unexpected error: {str(e)}")

            return {
                'status': 'failed',
                'customer_id': customer_id,
                'error': f"Unexpected error: {str(e)}"
            }


# RQ job function
def provision_customer_job(job_data):
    """Job function called by RQ worker"""
    from rq import get_current_job

    # Get the current RQ job ID
    current_job = get_current_job()
    rq_job_id = current_job.id if current_job else None

    # Get server_id from job data or environment
    server_id = job_data.get('server_id') or os.getenv('SERVER_ID')

    worker = ProvisioningWorker(server_id=server_id)

    # Update heartbeat at start of job
    worker.update_server_heartbeat()

    return worker.provision_customer(job_data, rq_job_id=rq_job_id)


def start_heartbeat_thread(server_id, interval=30):
    """Start a background thread that sends periodic heartbeats"""
    import threading

    def heartbeat_loop():
        import mysql.connector
        db_config = {
            'host': os.getenv('DB_HOST', 'localhost'),
            'user': os.getenv('DB_USER', 'shophosting_app'),
            'password': os.getenv('DB_PASSWORD', ''),
            'database': os.getenv('DB_NAME', 'shophosting_db')
        }

        while True:
            try:
                conn = mysql.connector.connect(**db_config)
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE servers SET last_heartbeat = NOW() WHERE id = %s",
                    (server_id,)
                )
                conn.commit()
                cursor.close()
                conn.close()
                logger.debug(f"Heartbeat sent for server {server_id}")
            except Exception as e:
                logger.warning(f"Heartbeat failed: {e}")

            import time
            time.sleep(interval)

    thread = threading.Thread(target=heartbeat_loop, daemon=True)
    thread.start()
    logger.info(f"Started heartbeat thread for server {server_id} (interval: {interval}s)")
    return thread


if __name__ == '__main__':
    # Start the worker with configuration from environment variables
    redis_host = os.getenv('REDIS_HOST', 'localhost')
    redis_port = int(os.getenv('REDIS_PORT', 6379))
    server_id = os.getenv('SERVER_ID')

    redis_conn = redis.Redis(host=redis_host, port=redis_port, db=0)

    # Build queue list based on server configuration
    queues = ['staging']  # Always listen to staging queue

    if server_id:
        # Multi-server mode: listen to server-specific queue
        server_queue = f"provisioning:server-{server_id}"
        queues.insert(0, server_queue)
        logger.info(f"Multi-server mode: Server ID {server_id}")

        # Start heartbeat thread
        start_heartbeat_thread(int(server_id))
    else:
        # Single-server mode: listen to default provisioning queue
        queues.insert(0, 'provisioning')
        logger.info("Single-server mode (no SERVER_ID set)")

    worker = Worker(queues, connection=redis_conn)
    logger.info(f"ShopHosting.io Provisioning worker started (Redis: {redis_host}:{redis_port}, queues: {', '.join(queues)})")
    worker.work()
