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
os.environ['DB_PASSWORD'] = os.environ.get('DB_PASSWORD', 'test_password')
os.environ['DB_HOST'] = os.environ.get('DB_HOST', 'localhost')
os.environ['DB_USER'] = os.environ.get('DB_USER', 'test_user')
os.environ['DB_NAME'] = os.environ.get('DB_NAME', 'shophosting_test')
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
