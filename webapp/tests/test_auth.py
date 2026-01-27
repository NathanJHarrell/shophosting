"""
Tests for authentication endpoints
"""

import pytest


class TestLoginEndpoint:
    """Test login functionality"""

    def test_login_page_loads(self, client):
        """Test that login page loads successfully"""
        response = client.get('/login')
        assert response.status_code == 200
        assert b'Login' in response.data or b'login' in response.data

    def test_login_with_empty_credentials(self, client):
        """Test login fails with empty credentials"""
        response = client.post('/login', data={
            'email': '',
            'password': ''
        })
        # Should return 200 (form redisplay) with errors
        assert response.status_code == 200

    def test_login_with_invalid_email_format(self, client):
        """Test login handles invalid email format"""
        response = client.post('/login', data={
            'email': 'not-an-email',
            'password': 'somepassword'
        })
        assert response.status_code == 200

    def test_login_with_nonexistent_user(self, client):
        """Test login fails for non-existent user"""
        response = client.post('/login', data={
            'email': 'nonexistent@example.com',
            'password': 'wrongpassword'
        })
        assert response.status_code == 200
        # Should show error message
        assert b'Invalid' in response.data or b'invalid' in response.data or b'error' in response.data.lower()


class TestSignupEndpoint:
    """Test signup functionality"""

    def test_signup_page_redirects_without_plan(self, client):
        """Test that signup page redirects to pricing without a plan"""
        response = client.get('/signup', follow_redirects=False)
        assert response.status_code == 302  # Redirect
        assert b'pricing' in response.data or response.location and 'pricing' in response.location

    def test_signup_page_loads_with_valid_plan_slug(self, client):
        """Test that signup page loads with a plan parameter"""
        # This may redirect if plan doesn't exist, which is expected
        response = client.get('/signup/woo-starter')
        assert response.status_code in [200, 302]


class TestLogoutEndpoint:
    """Test logout functionality"""

    def test_logout_redirects_when_not_logged_in(self, client):
        """Test that logout redirects to login when not authenticated"""
        response = client.get('/logout', follow_redirects=False)
        assert response.status_code == 302


class TestPublicEndpoints:
    """Test public pages load correctly"""

    def test_index_page_loads(self, client):
        """Test that index page loads"""
        response = client.get('/')
        assert response.status_code == 200

    def test_pricing_page_loads(self, client):
        """Test that pricing page loads"""
        response = client.get('/pricing')
        assert response.status_code == 200

    def test_features_page_loads(self, client):
        """Test that features page loads"""
        response = client.get('/features')
        assert response.status_code == 200

    def test_about_page_loads(self, client):
        """Test that about page loads"""
        response = client.get('/about')
        assert response.status_code == 200

    def test_contact_page_loads(self, client):
        """Test that contact page loads"""
        response = client.get('/contact')
        assert response.status_code == 200
