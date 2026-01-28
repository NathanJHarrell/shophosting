"""
Tests for health check endpoints
"""

import pytest


class TestHealthEndpoints:
    """Test health and readiness endpoints"""

    def test_health_endpoint_exists(self, client):
        """Test that /health endpoint returns a response"""
        response = client.get('/health')
        # Should return 200 (healthy) or 503 (unhealthy)
        assert response.status_code in [200, 503]

    def test_health_endpoint_returns_json(self, client):
        """Test that /health endpoint returns JSON"""
        response = client.get('/health')
        assert response.content_type == 'application/json'

    def test_health_endpoint_has_required_fields(self, client):
        """Test that /health response has required fields"""
        response = client.get('/health')
        data = response.get_json()

        assert 'status' in data
        assert 'timestamp' in data
        assert 'checks' in data
        assert data['status'] in ['healthy', 'unhealthy']

    def test_health_endpoint_checks_database(self, client):
        """Test that /health checks database connectivity"""
        response = client.get('/health')
        data = response.get_json()

        assert 'database' in data['checks']
        assert 'status' in data['checks']['database']

    def test_health_endpoint_checks_redis(self, client):
        """Test that /health checks Redis connectivity"""
        response = client.get('/health')
        data = response.get_json()

        assert 'redis' in data['checks']
        assert 'status' in data['checks']['redis']

    def test_readiness_endpoint_exists(self, client):
        """Test that /ready endpoint exists and returns 200"""
        response = client.get('/ready')
        assert response.status_code == 200

    def test_readiness_endpoint_returns_json(self, client):
        """Test that /ready endpoint returns JSON"""
        response = client.get('/ready')
        assert response.content_type == 'application/json'

    def test_readiness_endpoint_has_required_fields(self, client):
        """Test that /ready response has required fields"""
        response = client.get('/ready')
        data = response.get_json()

        assert 'status' in data
        assert data['status'] == 'ready'
        assert 'timestamp' in data
