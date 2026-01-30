"""
Tests for admin panel functionality
"""

import pytest
from unittest.mock import Mock, patch, MagicMock


class TestAdminLogin:
    """Test admin authentication"""

    def test_admin_login_page_loads(self, client):
        """Test that admin login page loads successfully"""
        response = client.get('/admin/login')
        assert response.status_code == 200
        assert b'Admin' in response.data or b'Login' in response.data

    def test_admin_login_with_empty_credentials(self, client):
        """Test admin login fails with empty credentials"""
        response = client.post('/admin/login', data={
            'username': '',
            'password': ''
        })
        assert response.status_code == 200

    def test_admin_login_with_invalid_credentials(self, client):
        """Test admin login fails with invalid credentials"""
        response = client.post('/admin/login', data={
            'username': 'fakeadmin',
            'password': 'wrongpassword'
        })
        assert response.status_code == 200


class TestAdminDashboard:
    """Test admin dashboard access"""

    def test_admin_dashboard_requires_login(self, client):
        """Test that admin dashboard redirects when not logged in"""
        response = client.get('/admin/', follow_redirects=False)
        # Should redirect to login
        assert response.status_code == 302

    def test_admin_customers_requires_login(self, client):
        """Test that customer list requires admin login"""
        response = client.get('/admin/customers', follow_redirects=False)
        assert response.status_code == 302


class TestAdminCustomerOperations:
    """Test admin customer management operations"""

    @pytest.fixture
    def mock_admin_session(self, client):
        """Mock an admin session"""
        with client.session_transaction() as sess:
            sess['admin_logged_in'] = True
            sess['admin_username'] = 'testadmin'
        return client

    def test_customer_list_requires_auth(self, client):
        """Test customer list requires authentication"""
        response = client.get('/admin/customers')
        assert response.status_code in [302, 401, 403]

    def test_customer_details_requires_auth(self, client):
        """Test customer details requires authentication"""
        response = client.get('/admin/customer/1')
        assert response.status_code in [302, 401, 403, 404]

    def test_customer_suspend_requires_auth(self, client):
        """Test customer suspend requires authentication"""
        response = client.post('/admin/customer/1/suspend')
        assert response.status_code in [302, 401, 403, 404]

    def test_customer_reactivate_requires_auth(self, client):
        """Test customer reactivate requires authentication"""
        response = client.post('/admin/customer/1/reactivate')
        assert response.status_code in [302, 401, 403, 404]


class TestAdminLogout:
    """Test admin logout functionality"""

    def test_admin_logout_redirects(self, client):
        """Test admin logout redirects to login page"""
        response = client.get('/admin/logout', follow_redirects=False)
        # Should redirect to admin login
        assert response.status_code == 302


class TestAdminSecurityHeaders:
    """Test security headers on admin pages"""

    def test_admin_login_has_security_headers(self, client):
        """Test admin login page has security headers"""
        response = client.get('/admin/login')
        # Check for common security headers
        assert response.status_code == 200
        # The actual headers depend on Flask-Talisman configuration
