#!/usr/bin/env python3
"""
Test script for the ShopHosting.io provisioning system
"""

import sys
import tempfile
import shutil
from pathlib import Path

# Add provisioning module to path
sys.path.insert(0, '/opt/shophosting/provisioning')

from jinja2 import Template


def test_magento_template_renders():
    """Test that the Magento template renders correctly with Varnish"""
    print("Testing Magento template rendering...")

    template_path = '/opt/shophosting/templates/magento-compose.yml.j2'

    with open(template_path, 'r') as f:
        template = Template(f.read())

    # Test config matching what provisioning would use
    test_config = {
        'customer_id': 'test-12345',
        'domain': 'testshop.example.com',
        'platform': 'magento',
        'site_title': 'Test Magento Store',
        'email': 'test@example.com',
        'admin_user': 'admin',
        'admin_password': 'TestPassword123!',
        'db_name': 'customer_test12345',
        'db_user': 'customer_test12345',
        'db_password': 'DbPassword123!',
        'db_root_password': 'RootPassword123!',
        'container_prefix': 'customer-test-12345',
        'web_port': 8099,
        'memory_limit': '2g',
        'cpu_limit': '2.0'
    }

    try:
        rendered = template.render(**test_config)

        # Verify key elements are in the rendered output
        checks = [
            ('varnish service', 'varnish:' in rendered),
            ('varnish image', 'varnish:7.4' in rendered),
            ('varnish port mapping', f"{test_config['web_port']}:80" in rendered),
            ('web service expose', 'expose:' in rendered and '"8080"' in rendered),
            ('elasticsearch service', 'elasticsearch:' in rendered),
            ('redis service', 'redis:' in rendered),
            ('VARNISH_ENABLED env', 'VARNISH_ENABLED' in rendered),
            ('varnish volume mount', 'varnish/default.vcl' in rendered),
        ]

        all_passed = True
        for check_name, passed in checks:
            status = "PASS" if passed else "FAIL"
            print(f"  [{status}] {check_name}")
            if not passed:
                all_passed = False

        if all_passed:
            print("\nTemplate rendering: SUCCESS")
            print("\n--- Rendered docker-compose.yml preview (first 80 lines) ---")
            lines = rendered.split('\n')[:80]
            for i, line in enumerate(lines, 1):
                print(f"{i:3}: {line}")
            return True
        else:
            print("\nTemplate rendering: FAILED - some checks did not pass")
            return False

    except Exception as e:
        print(f"\nTemplate rendering: FAILED - {e}")
        return False


def test_varnish_vcl_exists():
    """Test that Varnish VCL template exists"""
    print("\nTesting Varnish VCL template...")

    vcl_path = Path('/opt/shophosting/templates/magento-varnish.vcl.j2')

    if vcl_path.exists():
        print(f"  [PASS] VCL template exists at {vcl_path}")

        with open(vcl_path, 'r') as f:
            content = f.read()

        checks = [
            ('backend definition', 'backend default' in content),
            ('purge ACL', 'acl purge' in content),
            ('vcl_recv', 'sub vcl_recv' in content),
            ('vcl_backend_response', 'sub vcl_backend_response' in content),
            ('Magento tags handling', 'X-Magento-Tags' in content),
        ]

        all_passed = True
        for check_name, passed in checks:
            status = "PASS" if passed else "FAIL"
            print(f"  [{status}] VCL contains {check_name}")
            if not passed:
                all_passed = False

        return all_passed
    else:
        print(f"  [FAIL] VCL template not found at {vcl_path}")
        return False


def test_directory_creation():
    """Test that provisioning creates correct directory structure for Magento"""
    print("\nTesting directory creation for Magento...")

    from provisioning_worker import ProvisioningWorker

    # Use a temp directory for testing
    with tempfile.TemporaryDirectory() as tmpdir:
        worker = ProvisioningWorker(base_path=tmpdir)

        try:
            customer_path = worker.create_customer_directory('test-magento', platform='magento')

            checks = [
                ('customer directory', customer_path.exists()),
                ('volumes directory', (customer_path / 'volumes').exists()),
                ('db directory', (customer_path / 'volumes' / 'db').exists()),
                ('files directory', (customer_path / 'volumes' / 'files').exists()),
                ('varnish directory', (customer_path / 'volumes' / 'varnish').exists()),
                ('varnish VCL file', (customer_path / 'volumes' / 'varnish' / 'default.vcl').exists()),
                ('logs directory', (customer_path / 'logs').exists()),
            ]

            all_passed = True
            for check_name, passed in checks:
                status = "PASS" if passed else "FAIL"
                print(f"  [{status}] {check_name}")
                if not passed:
                    all_passed = False

            return all_passed

        except Exception as e:
            print(f"  [FAIL] Directory creation failed: {e}")
            return False


def main():
    print("=" * 60)
    print("ShopHosting.io Provisioning System Tests")
    print("=" * 60)

    results = []

    results.append(("Magento Template Rendering", test_magento_template_renders()))
    results.append(("Varnish VCL Template", test_varnish_vcl_exists()))
    results.append(("Directory Creation (Magento)", test_directory_creation()))

    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)

    all_passed = True
    for test_name, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {test_name}")
        if not passed:
            all_passed = False

    print("\n" + "=" * 60)
    if all_passed:
        print("All tests PASSED!")
        return 0
    else:
        print("Some tests FAILED!")
        return 1


if __name__ == '__main__':
    sys.exit(main())
