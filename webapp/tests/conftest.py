"""
Pytest configuration and fixtures for ShopHosting.io tests
"""

import os
import sys
import pytest

# Ensure the webapp module is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set test environment before importing app
os.environ['FLASK_ENV'] = 'testing'
os.environ['FLASK_DEBUG'] = 'true'
os.environ['SECRET_KEY'] = 'test-secret-key-for-testing-only'
# Don't set DB_PASSWORD in CI - allows graceful test mode without database
# For local testing with a database, set these env vars before running pytest
if 'DB_PASSWORD' not in os.environ:
    # Clear any existing DB config to ensure test mode behavior
    for key in ['DB_PASSWORD', 'DB_HOST', 'DB_USER', 'DB_NAME']:
        os.environ.pop(key, None)
os.environ['REDIS_URL'] = os.environ.get('REDIS_URL', 'redis://localhost:6379/1')


@pytest.fixture(scope='session')
def app():
    """Create application for testing"""
    from app import app as flask_app

    flask_app.config.update({
        'TESTING': True,
        'WTF_CSRF_ENABLED': False,  # Disable CSRF for testing
        'LOGIN_DISABLED': False,
    })

    yield flask_app


@pytest.fixture
def client(app):
    """Create test client"""
    return app.test_client()


@pytest.fixture
def runner(app):
    """Create CLI test runner"""
    return app.test_cli_runner()


@pytest.fixture
def auth_headers():
    """Return headers for authenticated requests"""
    return {'Content-Type': 'application/json'}


@pytest.fixture
def admin_session(client):
    """
    Create a mock admin session for testing admin routes.
    Note: This simulates an admin session without hitting the database.
    """
    with client.session_transaction() as sess:
        sess['admin_logged_in'] = True
        sess['admin_username'] = 'testadmin'
    return client


@pytest.fixture
def mock_customer():
    """
    Create a mock customer object for testing.
    Returns a dictionary that mimics the Customer model.
    """
    return {
        'id': 1,
        'email': 'test@example.com',
        'username': 'testuser',
        'subdomain': 'testcust',
        'status': 'active',
        'plan_slug': 'woo-starter',
        'created_at': '2024-01-01 00:00:00',
        'last_login': None,
        'stripe_customer_id': 'cus_test123',
        'stripe_subscription_id': 'sub_test123',
        'disk_limit_gb': 10,
        'bandwidth_limit_gb': 100,
        'disk_usage_bytes': 5 * 1024 * 1024 * 1024,  # 5 GB
        'bandwidth_usage_bytes': 50 * 1024 * 1024 * 1024,  # 50 GB
        'auto_suspended': False,
        'suspension_reason': None,
        'suspended_at': None,
        'reactivated_at': None,
    }


@pytest.fixture
def mock_customer_at_limit(mock_customer):
    """
    Create a mock customer at 100% disk usage for enforcement testing.
    """
    customer = mock_customer.copy()
    customer['disk_usage_bytes'] = 10 * 1024 * 1024 * 1024  # 10 GB (100%)
    return customer


@pytest.fixture
def mock_customer_suspended(mock_customer):
    """
    Create a mock suspended customer for testing.
    """
    from datetime import datetime
    customer = mock_customer.copy()
    customer['status'] = 'suspended'
    customer['auto_suspended'] = True
    customer['suspension_reason'] = 'Disk usage exceeded 100% of allocated quota'
    customer['suspended_at'] = datetime.now().isoformat()
    return customer


@pytest.fixture
def stripe_webhook_payload():
    """
    Create a mock Stripe webhook payload.
    """
    return {
        'id': 'evt_test123',
        'type': 'checkout.session.completed',
        'data': {
            'object': {
                'id': 'cs_test123',
                'customer': 'cus_test123',
                'subscription': 'sub_test123',
                'metadata': {
                    'customer_id': '1',
                    'plan_slug': 'woo-starter'
                }
            }
        }
    }
