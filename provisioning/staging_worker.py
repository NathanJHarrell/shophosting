"""
ShopHosting.io Staging Worker - Handles staging environment provisioning and sync
"""

import os
import subprocess
import secrets
import string
import logging
import shutil
from pathlib import Path
import sys
sys.path.insert(0, '/opt/shophosting/webapp')
from models import Customer, StagingEnvironment, StagingPortManager
from jinja2 import Template
import mysql.connector
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(env_path)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/opt/shophosting/logs/staging_worker.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class StagingError(Exception):
    """Custom exception for staging operations"""
    pass


class StagingWorker:
    """Handles staging environment provisioning and management"""

    def __init__(self, base_path=None):
        if base_path is None:
            base_path = os.getenv('CUSTOMERS_BASE_PATH', '/var/customers')
        self.base_path = Path(base_path)

        self.db_config = {
            'host': os.getenv('DB_HOST', 'localhost'),
            'user': os.getenv('DB_USER', 'shophosting_app'),
            'password': os.getenv('DB_PASSWORD', ''),
            'database': os.getenv('DB_NAME', 'shophosting_db')
        }

    def get_db_connection(self):
        """Get database connection"""
        return mysql.connector.connect(**self.db_config)

    def generate_password(self, length=16):
        """Generate a secure random password"""
        alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
        return ''.join(secrets.choice(alphabet) for _ in range(length))

    # =========================================================================
    # Create Staging Environment
    # =========================================================================

    def create_staging_environment(self, customer_id, staging_name=None):
        """
        Create a new staging environment for a customer.
        Clones production database and files to staging.
        """
        logger.info(f"Creating staging environment for customer {customer_id}")

        # Get customer info
        customer = Customer.get_by_id(customer_id)
        if not customer:
            raise StagingError(f"Customer {customer_id} not found")

        if customer.status != 'active':
            raise StagingError(f"Customer site must be active to create staging")

        # Check staging limit
        if not StagingEnvironment.can_create_staging(customer_id):
            raise StagingError(f"Maximum staging environments ({StagingEnvironment.MAX_STAGING_PER_CUSTOMER}) reached")

        # Determine staging number
        existing_staging = StagingEnvironment.get_by_customer(customer_id)
        staging_number = len(existing_staging) + 1

        # Generate staging name if not provided
        if not staging_name:
            staging_name = f"Staging {staging_number}"

        # Allocate port
        web_port = StagingPortManager.get_next_available_port()
        if not web_port:
            raise StagingError("No staging ports available")

        # Generate staging domain
        staging_domain = StagingEnvironment.generate_staging_domain(customer_id, staging_number)

        # Generate credentials
        db_name = f"staging_{customer_id}_{staging_number}"
        db_user = f"stg_{customer_id}_{staging_number}"
        db_password = self.generate_password()

        # Create staging record
        staging = StagingEnvironment(
            customer_id=customer_id,
            name=staging_name,
            staging_domain=staging_domain,
            status='creating',
            web_port=web_port,
            db_name=db_name,
            db_user=db_user,
            db_password=db_password,
            source_snapshot_date=datetime.now()
        )
        staging.save()

        # Log sync operation
        sync_id = staging.log_sync('create', 'running')

        try:
            # Create staging directory structure
            staging_path = self.create_staging_directory(customer_id, staging_number, customer.platform)

            # Clone production files to staging
            self.clone_files(customer_id, staging_number, customer.platform)

            # Generate docker-compose for staging
            self.generate_staging_compose(staging_path, {
                'customer_id': customer_id,
                'staging_number': staging_number,
                'staging_domain': staging_domain,
                'web_port': web_port,
                'db_name': db_name,
                'db_user': db_user,
                'db_password': db_password,
                'db_root_password': db_password,
                'platform': customer.platform
            })

            # Start staging containers
            self.start_staging_containers(staging_path)

            # Clone production database to staging
            self.clone_database(customer, staging)

            # Update URLs in staging database
            self.update_staging_urls(staging, customer)

            # Configure nginx for staging
            self.configure_staging_nginx(staging)

            # Update staging status
            staging.update_status('active')
            StagingEnvironment.update_sync_status(sync_id, 'completed')

            logger.info(f"Staging environment created successfully: {staging_domain}")
            return staging

        except Exception as e:
            logger.error(f"Failed to create staging: {e}")
            staging.update_status('failed')
            StagingEnvironment.update_sync_status(sync_id, 'failed', str(e))
            raise StagingError(f"Staging creation failed: {e}")

    def create_staging_directory(self, customer_id, staging_number, platform):
        """Create directory structure for staging environment"""
        staging_path = self.base_path / f"customer-{customer_id}" / f"staging-{staging_number}"

        try:
            staging_path.mkdir(parents=True, exist_ok=False)
            (staging_path / "volumes").mkdir()
            (staging_path / "volumes" / "db").mkdir()
            (staging_path / "volumes" / "files").mkdir()
            (staging_path / "logs").mkdir()

            if platform == 'magento':
                varnish_dir = staging_path / "volumes" / "varnish"
                varnish_dir.mkdir()
                vcl_template = Path('/opt/shophosting/templates/magento-varnish.vcl.j2')
                if vcl_template.exists():
                    shutil.copy(vcl_template, varnish_dir / "default.vcl")

            os.chmod(staging_path, 0o755)
            logger.info(f"Created staging directory: {staging_path}")
            return staging_path

        except FileExistsError:
            raise StagingError(f"Staging directory already exists")
        except Exception as e:
            raise StagingError(f"Failed to create staging directory: {e}")

    def clone_files(self, customer_id, staging_number, platform):
        """Clone production files to staging directory"""
        prod_path = self.base_path / f"customer-{customer_id}"
        staging_path = prod_path / f"staging-{staging_number}"

        if platform == 'woocommerce':
            src = prod_path / "wordpress"
            dst = staging_path / "wordpress"
        else:  # magento
            src = prod_path / "volumes" / "files"
            dst = staging_path / "volumes" / "files"

        if src.exists():
            logger.info(f"Cloning files from {src} to {dst}")
            result = subprocess.run(
                ['rsync', '-a', '--delete', f"{src}/", f"{dst}/"],
                capture_output=True, text=True, timeout=600
            )
            if result.returncode != 0:
                raise StagingError(f"File clone failed: {result.stderr}")
            logger.info("Files cloned successfully")
        else:
            logger.warning(f"Production files not found at {src}")

    def generate_staging_compose(self, staging_path, config):
        """Generate docker-compose.yml for staging"""
        if config['platform'] == 'woocommerce':
            template_file = '/opt/shophosting/templates/woocommerce-staging-compose.yml.j2'
        else:
            template_file = '/opt/shophosting/templates/magento-staging-compose.yml.j2'

        try:
            with open(template_file, 'r') as f:
                template = Template(f.read())

            compose_content = template.render(**config)
            compose_path = staging_path / "docker-compose.yml"

            with open(compose_path, 'w') as f:
                f.write(compose_content)

            logger.info(f"Generated staging docker-compose.yml")
            return compose_path

        except Exception as e:
            raise StagingError(f"Failed to generate compose file: {e}")

    def start_staging_containers(self, staging_path):
        """Start staging Docker containers"""
        try:
            result = subprocess.run(
                ['docker', 'compose', 'up', '-d'],
                cwd=staging_path,
                capture_output=True,
                text=True,
                timeout=300
            )

            if result.returncode != 0:
                raise StagingError(f"Container startup failed: {result.stderr}")

            logger.info("Staging containers started")
            import time
            time.sleep(15)  # Wait for containers to fully start

        except subprocess.TimeoutExpired:
            raise StagingError("Container startup timed out")

    def clone_database(self, customer, staging):
        """Clone production database to staging database"""
        logger.info(f"Cloning database for staging {staging.id}")

        # Get production container name
        prod_container = f"customer-{customer.id}-db"
        staging_container = f"customer-{customer.id}-staging-{staging.staging_domain.split('-')[-1].split('.')[0]}-db"

        # Wait for staging DB to be ready
        import time
        for _ in range(30):
            result = subprocess.run(
                ['docker', 'exec', staging_container, 'mysqladmin', 'ping',
                 '-h', 'localhost', '-u', 'root', f'-p{staging.db_password}', '--silent'],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                break
            time.sleep(2)
        else:
            raise StagingError("Staging database not ready")

        # Dump production database
        dump_result = subprocess.run(
            ['docker', 'exec', prod_container, 'mysqldump',
             '-u', customer.db_user, f'-p{customer.db_password}',
             '--single-transaction', '--quick', customer.db_name],
            capture_output=True, text=True, timeout=300
        )

        if dump_result.returncode != 0:
            raise StagingError(f"Database dump failed: {dump_result.stderr}")

        # Import to staging database
        import_process = subprocess.Popen(
            ['docker', 'exec', '-i', staging_container, 'mysql',
             '-u', staging.db_user, f'-p{staging.db_password}', staging.db_name],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        stdout, stderr = import_process.communicate(input=dump_result.stdout, timeout=300)

        if import_process.returncode != 0:
            raise StagingError(f"Database import failed: {stderr}")

        logger.info("Database cloned successfully")

    def update_staging_urls(self, staging, customer):
        """Update URLs in staging database to use staging domain"""
        logger.info(f"Updating URLs for staging {staging.staging_domain}")

        staging_number = staging.staging_domain.split('-')[-1].split('.')[0]
        staging_container = f"customer-{customer.id}-staging-{staging_number}-db"
        prod_domain = customer.domain
        staging_domain = staging.staging_domain

        if customer.platform == 'woocommerce':
            # WordPress URL updates
            sql_commands = f"""
                UPDATE wp_options SET option_value = 'https://{staging_domain}' WHERE option_name = 'siteurl';
                UPDATE wp_options SET option_value = 'https://{staging_domain}' WHERE option_name = 'home';
                UPDATE wp_posts SET post_content = REPLACE(post_content, '{prod_domain}', '{staging_domain}');
                UPDATE wp_posts SET guid = REPLACE(guid, '{prod_domain}', '{staging_domain}');
                UPDATE wp_postmeta SET meta_value = REPLACE(meta_value, '{prod_domain}', '{staging_domain}') WHERE meta_value LIKE '%{prod_domain}%';
            """
        else:  # Magento
            sql_commands = f"""
                UPDATE core_config_data SET value = 'https://{staging_domain}/' WHERE path = 'web/unsecure/base_url';
                UPDATE core_config_data SET value = 'https://{staging_domain}/' WHERE path = 'web/secure/base_url';
                TRUNCATE TABLE cache;
                TRUNCATE TABLE cache_tag;
            """

        result = subprocess.run(
            ['docker', 'exec', staging_container, 'mysql',
             '-u', staging.db_user, f'-p{staging.db_password}', staging.db_name, '-e', sql_commands],
            capture_output=True, text=True
        )

        if result.returncode != 0:
            logger.warning(f"URL update may have partially failed: {result.stderr}")
        else:
            logger.info("URLs updated successfully")

    def configure_staging_nginx(self, staging):
        """Configure Nginx reverse proxy for staging domain"""
        staging_number = staging.staging_domain.split('-')[-1].split('.')[0]
        config_name = f"staging-{staging.customer_id}-{staging_number}"

        nginx_config = f"""server {{
    listen 80;
    listen [::]:80;
    server_name {staging.staging_domain};

    location /.well-known/acme-challenge/ {{
        root /var/www/html;
    }}

    location / {{
        proxy_pass http://localhost:{staging.web_port};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Staging-Environment "true";

        proxy_connect_timeout 600;
        proxy_send_timeout 600;
        proxy_read_timeout 600;
        send_timeout 600;
        client_max_body_size 100M;
    }}

    access_log /var/log/nginx/{config_name}-access.log;
    error_log /var/log/nginx/{config_name}-error.log;
}}
"""

        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
            f.write(nginx_config)
            temp_file = f.name

        config_path = f"/etc/nginx/sites-available/{config_name}.conf"
        enabled_path = f"/etc/nginx/sites-enabled/{config_name}.conf"

        subprocess.run(['sudo', 'cp', temp_file, config_path], check=True)
        os.unlink(temp_file)

        if not Path(enabled_path).exists():
            subprocess.run(['sudo', 'ln', '-s', config_path, enabled_path], check=True)

        # Test and reload nginx
        test_result = subprocess.run(['sudo', 'nginx', '-t'], capture_output=True, text=True)
        if test_result.returncode != 0:
            raise StagingError(f"Nginx config test failed: {test_result.stderr}")

        subprocess.run(['sudo', 'systemctl', 'reload', 'nginx'], check=True)

        # Try to get SSL certificate
        try:
            admin_email = os.getenv('ADMIN_EMAIL', 'admin@shophosting.io')
            certbot_result = subprocess.run(
                ['sudo', 'certbot', '--nginx', '-d', staging.staging_domain,
                 '--non-interactive', '--agree-tos', '--email', admin_email, '--redirect'],
                capture_output=True, text=True, timeout=120
            )
            if certbot_result.returncode == 0:
                logger.info(f"SSL certificate obtained for {staging.staging_domain}")
            else:
                logger.warning(f"SSL setup failed, HTTP only: {certbot_result.stderr}")
        except Exception as ssl_error:
            logger.warning(f"SSL setup failed: {ssl_error}")

        logger.info(f"Nginx configured for {staging.staging_domain}")

    # =========================================================================
    # Push to Production
    # =========================================================================

    def push_to_production(self, staging_id, sync_type='all'):
        """
        Push staging changes to production.
        sync_type: 'files', 'db', or 'all'
        """
        staging = StagingEnvironment.get_by_id(staging_id)
        if not staging:
            raise StagingError(f"Staging environment {staging_id} not found")

        if staging.status != 'active':
            raise StagingError("Staging must be active to push to production")

        customer = Customer.get_by_id(staging.customer_id)
        if not customer:
            raise StagingError("Customer not found")

        sync_type_map = {
            'files': 'push_files',
            'db': 'push_db',
            'all': 'push_all'
        }
        sync_id = staging.log_sync(sync_type_map.get(sync_type, 'push_all'), 'running')
        staging.update_status('syncing')

        try:
            staging_number = staging.staging_domain.split('-')[-1].split('.')[0]

            if sync_type in ['files', 'all']:
                self.push_files_to_production(customer, staging_number)

            if sync_type in ['db', 'all']:
                self.push_database_to_production(customer, staging)

            # Update last push date
            staging.last_push_date = datetime.now()
            staging.update_status('active')
            StagingEnvironment.update_sync_status(sync_id, 'completed')

            logger.info(f"Push to production completed for staging {staging_id}")
            return True

        except Exception as e:
            logger.error(f"Push to production failed: {e}")
            staging.update_status('active')  # Revert to active status
            StagingEnvironment.update_sync_status(sync_id, 'failed', str(e))
            raise StagingError(f"Push to production failed: {e}")

    def push_files_to_production(self, customer, staging_number):
        """Push staging files to production"""
        logger.info(f"Pushing files to production for customer {customer.id}")

        prod_path = self.base_path / f"customer-{customer.id}"
        staging_path = prod_path / f"staging-{staging_number}"

        if customer.platform == 'woocommerce':
            src = staging_path / "wordpress"
            dst = prod_path / "wordpress"
        else:
            src = staging_path / "volumes" / "files"
            dst = prod_path / "volumes" / "files"

        if not src.exists():
            raise StagingError("Staging files not found")

        # Create backup of production files
        backup_path = prod_path / f"backup-pre-push-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        if dst.exists():
            shutil.copytree(dst, backup_path)
            logger.info(f"Production backup created at {backup_path}")

        # Sync files
        result = subprocess.run(
            ['rsync', '-a', '--delete',
             '--exclude', 'wp-config.php',  # Preserve config files
             '--exclude', 'env.php',
             '--exclude', '.htaccess',
             f"{src}/", f"{dst}/"],
            capture_output=True, text=True, timeout=600
        )

        if result.returncode != 0:
            raise StagingError(f"File sync failed: {result.stderr}")

        logger.info("Files pushed to production successfully")

    def push_database_to_production(self, customer, staging):
        """Push staging database to production"""
        logger.info(f"Pushing database to production for customer {customer.id}")

        staging_number = staging.staging_domain.split('-')[-1].split('.')[0]
        staging_container = f"customer-{customer.id}-staging-{staging_number}-db"
        prod_container = f"customer-{customer.id}-db"

        # Dump staging database
        dump_result = subprocess.run(
            ['docker', 'exec', staging_container, 'mysqldump',
             '-u', staging.db_user, f'-p{staging.db_password}',
             '--single-transaction', '--quick', staging.db_name],
            capture_output=True, text=True, timeout=300
        )

        if dump_result.returncode != 0:
            raise StagingError(f"Staging database dump failed: {dump_result.stderr}")

        # Replace staging URLs with production URLs in the dump
        db_dump = dump_result.stdout
        db_dump = db_dump.replace(staging.staging_domain, customer.domain)

        # Import to production database
        import_process = subprocess.Popen(
            ['docker', 'exec', '-i', prod_container, 'mysql',
             '-u', customer.db_user, f'-p{customer.db_password}', customer.db_name],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        stdout, stderr = import_process.communicate(input=db_dump, timeout=300)

        if import_process.returncode != 0:
            raise StagingError(f"Production database import failed: {stderr}")

        logger.info("Database pushed to production successfully")

    # =========================================================================
    # Delete Staging Environment
    # =========================================================================

    def delete_staging_environment(self, staging_id):
        """Delete a staging environment"""
        staging = StagingEnvironment.get_by_id(staging_id)
        if not staging:
            raise StagingError(f"Staging environment {staging_id} not found")

        logger.info(f"Deleting staging environment {staging_id}")

        staging_number = staging.staging_domain.split('-')[-1].split('.')[0]
        customer_id = staging.customer_id

        try:
            # Stop and remove containers
            staging_path = self.base_path / f"customer-{customer_id}" / f"staging-{staging_number}"
            if staging_path.exists():
                subprocess.run(
                    ['docker', 'compose', 'down', '-v', '--remove-orphans'],
                    cwd=staging_path,
                    capture_output=True,
                    timeout=120
                )

            # Remove nginx config
            config_name = f"staging-{customer_id}-{staging_number}"
            subprocess.run(['sudo', 'rm', '-f', f"/etc/nginx/sites-enabled/{config_name}.conf"], capture_output=True)
            subprocess.run(['sudo', 'rm', '-f', f"/etc/nginx/sites-available/{config_name}.conf"], capture_output=True)
            subprocess.run(['sudo', 'systemctl', 'reload', 'nginx'], capture_output=True)

            # Remove staging directory
            if staging_path.exists():
                shutil.rmtree(staging_path)

            # Mark as deleted in database
            staging.mark_deleted()

            logger.info(f"Staging environment {staging_id} deleted successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to delete staging: {e}")
            raise StagingError(f"Staging deletion failed: {e}")


# =============================================================================
# RQ Job Functions (for async processing)
# =============================================================================

def create_staging_job(customer_id, staging_name=None):
    """RQ job wrapper for creating staging environment"""
    worker = StagingWorker()
    return worker.create_staging_environment(customer_id, staging_name)


def push_to_production_job(staging_id, sync_type='all'):
    """RQ job wrapper for pushing to production"""
    worker = StagingWorker()
    return worker.push_to_production(staging_id, sync_type)


def delete_staging_job(staging_id):
    """RQ job wrapper for deleting staging environment"""
    worker = StagingWorker()
    return worker.delete_staging_environment(staging_id)


if __name__ == '__main__':
    # Can be run directly for testing
    import argparse
    parser = argparse.ArgumentParser(description='Staging Worker')
    parser.add_argument('action', choices=['create', 'push', 'delete'])
    parser.add_argument('--customer-id', type=int)
    parser.add_argument('--staging-id', type=int)
    parser.add_argument('--sync-type', choices=['files', 'db', 'all'], default='all')
    parser.add_argument('--name', type=str)

    args = parser.parse_args()
    worker = StagingWorker()

    if args.action == 'create':
        if not args.customer_id:
            print("--customer-id required for create")
            sys.exit(1)
        result = worker.create_staging_environment(args.customer_id, args.name)
        print(f"Created staging: {result.staging_domain}")

    elif args.action == 'push':
        if not args.staging_id:
            print("--staging-id required for push")
            sys.exit(1)
        worker.push_to_production(args.staging_id, args.sync_type)
        print("Push completed")

    elif args.action == 'delete':
        if not args.staging_id:
            print("--staging-id required for delete")
            sys.exit(1)
        worker.delete_staging_environment(args.staging_id)
        print("Staging deleted")
