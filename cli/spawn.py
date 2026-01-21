#!/usr/bin/env python3
"""
ShopHosting.io Container Spawner CLI
Interactive tool to spawn WooCommerce or Magento containers
"""

import os
import sys
import re
import secrets
import string

# Add parent directories to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'provisioning'))

from dotenv import load_dotenv

# Load environment variables
env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(env_path)


def clear_screen():
    """Clear the terminal screen"""
    os.system('clear' if os.name == 'posix' else 'cls')


def print_banner():
    """Print the ShopHosting.io banner"""
    banner = """
    ╔═══════════════════════════════════════════════════════════════╗
    ║                                                               ║
    ║   ███████╗██╗  ██╗ ██████╗ ██████╗ ██╗  ██╗ ██████╗ ███████╗████████╗ ║
    ║   ██╔════╝██║  ██║██╔═══██╗██╔══██╗██║  ██║██╔═══██╗██╔════╝╚══██╔══╝ ║
    ║   ███████╗███████║██║   ██║██████╔╝███████║██║   ██║███████╗   ██║    ║
    ║   ╚════██║██╔══██║██║   ██║██╔═══╝ ██╔══██║██║   ██║╚════██║   ██║    ║
    ║   ███████║██║  ██║╚██████╔╝██║     ██║  ██║╚██████╔╝███████║   ██║    ║
    ║   ╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚═╝     ╚═╝  ╚═╝ ╚═════╝ ╚══════╝   ╚═╝    ║
    ║                                                               ║
    ║         ShopHosting.io - E-Commerce Container Spawner         ║
    ╚═══════════════════════════════════════════════════════════════╝
    """
    print(banner)


def print_menu():
    """Print the platform selection menu"""
    print("\n    Select a platform to spawn:\n")
    print("    ┌─────────────────────────────────────────────────────┐")
    print("    │                                                     │")
    print("    │   [1]  WooCommerce (WordPress + WooCommerce)        │")
    print("    │        - Best for: Small to medium stores           │")
    print("    │        - Includes: WordPress, MySQL, Redis          │")
    print("    │                                                     │")
    print("    │   [2]  Magento Open Source                          │")
    print("    │        - Best for: Large catalogs, complex needs    │")
    print("    │        - Includes: Magento, MySQL, Elasticsearch,   │")
    print("    │                    Redis                            │")
    print("    │                                                     │")
    print("    │   [q]  Quit                                         │")
    print("    │                                                     │")
    print("    └─────────────────────────────────────────────────────┘")


def validate_domain(domain):
    """Validate domain format"""
    pattern = r'^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z]{2,})+$'
    return bool(re.match(pattern, domain))


def validate_email(email):
    """Validate email format"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))


def generate_password(length=16):
    """Generate a secure random password"""
    alphabet = string.ascii_letters + string.digits + "!@#^*"
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def get_next_port():
    """Get the next available port from the port range"""
    port_start = int(os.getenv('PORT_RANGE_START', 8001))
    port_end = int(os.getenv('PORT_RANGE_END', 8100))

    # Check which ports are in use by checking docker containers
    import subprocess
    result = subprocess.run(
        ['docker', 'ps', '--format', '{{.Ports}}'],
        capture_output=True, text=True
    )

    used_ports = set()
    if result.returncode == 0:
        for line in result.stdout.strip().split('\n'):
            # Parse port mappings like "0.0.0.0:8001->80/tcp"
            matches = re.findall(r':(\d+)->', line)
            for match in matches:
                port = int(match)
                if port_start <= port <= port_end:
                    used_ports.add(port)

    # Find first available port
    for port in range(port_start, port_end + 1):
        if port not in used_ports:
            return port

    return None


def get_next_customer_id():
    """Generate a simple customer ID based on timestamp"""
    import time
    return f"cust-{int(time.time())}"


def prompt_with_default(prompt, default=None, validator=None, error_msg="Invalid input"):
    """Prompt user for input with optional default and validation"""
    while True:
        if default:
            user_input = input(f"    {prompt} [{default}]: ").strip()
            if not user_input:
                user_input = default
        else:
            user_input = input(f"    {prompt}: ").strip()

        if not user_input:
            print(f"    Error: This field is required.")
            continue

        if validator and not validator(user_input):
            print(f"    Error: {error_msg}")
            continue

        return user_input


def collect_store_info(platform):
    """Collect store information from user"""
    platform_name = "WooCommerce" if platform == "woocommerce" else "Magento"

    print(f"\n    ═══ {platform_name} Store Configuration ═══\n")

    # Get domain
    base_domain = os.getenv('BASE_DOMAIN', 'localhost')
    domain = prompt_with_default(
        "Enter store domain",
        default=None,
        validator=validate_domain,
        error_msg="Invalid domain format (e.g., mystore.com)"
    )

    # Get email
    email = prompt_with_default(
        "Enter admin email",
        default=None,
        validator=validate_email,
        error_msg="Invalid email format"
    )

    # Get site title
    site_title = prompt_with_default(
        "Enter site/store name",
        default="My Store"
    )

    # Admin username
    admin_user = prompt_with_default(
        "Enter admin username",
        default="admin"
    )

    # Admin password (auto-generate or custom)
    print("\n    Password options:")
    print("    [1] Auto-generate secure password (recommended)")
    print("    [2] Enter custom password")
    pwd_choice = input("    Select option [1]: ").strip() or "1"

    if pwd_choice == "2":
        admin_password = prompt_with_default(
            "Enter admin password (min 8 characters)",
            validator=lambda x: len(x) >= 8,
            error_msg="Password must be at least 8 characters"
        )
    else:
        admin_password = generate_password()
        print(f"    Generated password: {admin_password}")

    # Get port
    next_port = get_next_port()
    if next_port is None:
        print("\n    Error: No available ports in the configured range.")
        return None

    # Resource limits
    memory_limit = os.getenv('DEFAULT_MEMORY_LIMIT', '1g')
    cpu_limit = os.getenv('DEFAULT_CPU_LIMIT', '1.0')

    # Generate customer ID
    customer_id = get_next_customer_id()

    return {
        'customer_id': customer_id,
        'domain': domain,
        'platform': platform,
        'email': email,
        'site_title': site_title,
        'admin_user': admin_user,
        'admin_password': admin_password,
        'web_port': next_port,
        'memory_limit': memory_limit,
        'cpu_limit': cpu_limit
    }


def confirm_and_spawn(store_info):
    """Display configuration summary and confirm spawn"""
    platform_name = "WooCommerce" if store_info['platform'] == "woocommerce" else "Magento"

    print("\n    ═══ Configuration Summary ═══\n")
    print("    ┌─────────────────────────────────────────────────────┐")
    print(f"    │  Platform:      {platform_name:<36} │")
    print(f"    │  Domain:        {store_info['domain']:<36} │")
    print(f"    │  Email:         {store_info['email']:<36} │")
    print(f"    │  Site Title:    {store_info['site_title']:<36} │")
    print(f"    │  Admin User:    {store_info['admin_user']:<36} │")
    print(f"    │  Admin Pass:    {store_info['admin_password']:<36} │")
    print(f"    │  Port:          {store_info['web_port']:<36} │")
    print(f"    │  Memory:        {store_info['memory_limit']:<36} │")
    print(f"    │  CPU:           {store_info['cpu_limit']:<36} │")
    print("    └─────────────────────────────────────────────────────┘")

    print("\n    Do you want to spawn this container?")
    confirm = input("    [Y/n]: ").strip().lower()

    if confirm in ['', 'y', 'yes']:
        return spawn_container(store_info)
    else:
        print("\n    Spawn cancelled.")
        return False


def spawn_container(store_info):
    """Spawn the container using the provisioning system"""
    print("\n    Spawning container...")

    try:
        from enqueue_provisioning import ProvisioningQueue

        redis_host = os.getenv('REDIS_HOST', 'localhost')
        redis_port = int(os.getenv('REDIS_PORT', 6379))

        queue = ProvisioningQueue(redis_host=redis_host, redis_port=redis_port)
        job = queue.enqueue_customer(store_info)

        print("\n    ┌─────────────────────────────────────────────────────┐")
        print("    │                                                     │")
        print("    │           Container Spawn Initiated!                │")
        print("    │                                                     │")
        print(f"    │  Job ID: {job.id:<42} │")
        print(f"    │  Status: {job.get_status():<42} │")
        print("    │                                                     │")
        print("    │  The container is being provisioned in the         │")
        print("    │  background. This may take several minutes.        │")
        print("    │                                                     │")
        print("    │  Monitor progress with:                            │")
        print("    │  tail -f /opt/shophosting.io/logs/provisioning_worker.log│")
        print("    │                                                     │")
        print("    └─────────────────────────────────────────────────────┘")

        # Save credentials to a file for reference
        creds_dir = os.path.join(os.path.dirname(__file__), '..', 'logs')
        creds_file = os.path.join(creds_dir, f"credentials-{store_info['customer_id']}.txt")

        with open(creds_file, 'w') as f:
            f.write(f"ShopHosting.io Store Credentials\n")
            f.write(f"================================\n\n")
            f.write(f"Customer ID: {store_info['customer_id']}\n")
            f.write(f"Platform: {store_info['platform']}\n")
            f.write(f"Domain: {store_info['domain']}\n")
            f.write(f"Port: {store_info['web_port']}\n")
            f.write(f"URL: http://{store_info['domain']}:{store_info['web_port']}\n\n")
            f.write(f"Admin Credentials:\n")
            f.write(f"  Username: {store_info['admin_user']}\n")
            f.write(f"  Password: {store_info['admin_password']}\n")
            f.write(f"  Email: {store_info['email']}\n")

        print(f"\n    Credentials saved to: {creds_file}")

        return True

    except ImportError as e:
        print(f"\n    Error: Could not import provisioning module: {e}")
        print("    Make sure you're running from the correct directory")
        print("    and all dependencies are installed.")
        return False
    except Exception as e:
        print(f"\n    Error spawning container: {e}")
        return False


def main():
    """Main entry point"""
    clear_screen()
    print_banner()

    while True:
        print_menu()
        choice = input("\n    Enter your choice: ").strip().lower()

        if choice == '1':
            store_info = collect_store_info('woocommerce')
            if store_info:
                confirm_and_spawn(store_info)
            input("\n    Press Enter to continue...")
            clear_screen()
            print_banner()

        elif choice == '2':
            store_info = collect_store_info('magento')
            if store_info:
                confirm_and_spawn(store_info)
            input("\n    Press Enter to continue...")
            clear_screen()
            print_banner()

        elif choice in ['q', 'quit', 'exit']:
            print("\n    Goodbye!\n")
            sys.exit(0)

        else:
            print("\n    Invalid choice. Please try again.")
            input("    Press Enter to continue...")
            clear_screen()
            print_banner()


if __name__ == '__main__':
    main()
