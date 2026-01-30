"""
Cloudflare API Wrapper for ShopHosting.io

Provides a client for interacting with Cloudflare's v4 API and OAuth helpers
for the authorization flow.
"""

import os
import requests
from urllib.parse import urlencode


# API Configuration
CLOUDFLARE_API_BASE_URL = 'https://api.cloudflare.com/client/v4'
CLOUDFLARE_OAUTH_AUTHORIZE_URL = 'https://dash.cloudflare.com/oauth2/authorize'
CLOUDFLARE_OAUTH_TOKEN_URL = 'https://api.cloudflare.com/client/v4/user/tokens/oauth'

# OAuth scopes needed for DNS management
CLOUDFLARE_OAUTH_SCOPES = 'zone:read dns:read dns:edit'


class CloudflareAPIError(Exception):
    """Exception raised when a Cloudflare API request fails."""

    def __init__(self, message, status_code=None, errors=None):
        """
        Initialize the CloudflareAPIError.

        Args:
            message: Human-readable error message
            status_code: HTTP status code from the response (optional)
            errors: List of error details from Cloudflare API (optional)
        """
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.errors = errors or []

    def __str__(self):
        if self.status_code:
            return f"CloudflareAPIError ({self.status_code}): {self.message}"
        return f"CloudflareAPIError: {self.message}"


class CloudflareAPI:
    """Client for interacting with the Cloudflare v4 API."""

    def __init__(self, access_token):
        """
        Initialize the Cloudflare API client.

        Args:
            access_token: OAuth access token for authentication
        """
        self.access_token = access_token
        self.base_url = CLOUDFLARE_API_BASE_URL

    def _request(self, method, endpoint, data=None):
        """
        Make an authenticated request to the Cloudflare API.

        Args:
            method: HTTP method (GET, POST, PUT, PATCH, DELETE)
            endpoint: API endpoint path (e.g., '/zones')
            data: Request body data for POST/PUT/PATCH requests (optional)

        Returns:
            dict: The 'result' field from the Cloudflare API response

        Raises:
            CloudflareAPIError: If the request fails or returns an error
        """
        url = f"{self.base_url}{endpoint}"
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json'
        }

        try:
            response = requests.request(
                method=method,
                url=url,
                headers=headers,
                json=data,
                timeout=30
            )
        except requests.exceptions.Timeout:
            raise CloudflareAPIError("Request to Cloudflare API timed out")
        except requests.exceptions.RequestException as e:
            raise CloudflareAPIError(f"Request to Cloudflare API failed: {str(e)}")

        try:
            response_data = response.json()
        except ValueError:
            raise CloudflareAPIError(
                f"Invalid JSON response from Cloudflare API",
                status_code=response.status_code
            )

        # Check for API errors
        if not response_data.get('success', False):
            errors = response_data.get('errors', [])
            error_messages = [e.get('message', 'Unknown error') for e in errors]
            message = '; '.join(error_messages) if error_messages else 'Unknown API error'
            raise CloudflareAPIError(
                message,
                status_code=response.status_code,
                errors=errors
            )

        return response_data.get('result')

    def get_zones(self):
        """
        List all zones accessible with the current token.

        Returns:
            list: List of zone objects

        Raises:
            CloudflareAPIError: If the request fails
        """
        return self._request('GET', '/zones')

    def get_zone_by_name(self, domain):
        """
        Find a zone by domain name.

        Args:
            domain: The domain name to search for (e.g., 'example.com')

        Returns:
            dict: Zone object if found, None if not found

        Raises:
            CloudflareAPIError: If the request fails
        """
        params = urlencode({'name': domain})
        result = self._request('GET', f'/zones?{params}')

        if result and len(result) > 0:
            return result[0]
        return None

    def get_dns_records(self, zone_id, record_types=None):
        """
        List DNS records for a zone.

        Args:
            zone_id: The zone identifier
            record_types: Optional list of record types to filter by
                         (e.g., ['A', 'AAAA', 'CNAME'])

        Returns:
            list: List of DNS record objects

        Raises:
            CloudflareAPIError: If the request fails
        """
        endpoint = f'/zones/{zone_id}/dns_records'

        if record_types:
            # Cloudflare API accepts multiple type parameters
            params = '&'.join([f'type={t}' for t in record_types])
            endpoint = f'{endpoint}?{params}'

        return self._request('GET', endpoint)

    def create_dns_record(self, zone_id, record_type, name, content, ttl=1,
                          priority=None, proxied=False):
        """
        Create a new DNS record.

        Args:
            zone_id: The zone identifier
            record_type: DNS record type (A, AAAA, CNAME, MX, TXT, etc.)
            name: DNS record name (e.g., 'subdomain.example.com')
            content: DNS record content (e.g., IP address, target hostname)
            ttl: Time to live for DNS record (1 = automatic, otherwise seconds)
            priority: Priority for MX/SRV records (optional)
            proxied: Whether the record receives Cloudflare's protection (default False)

        Returns:
            dict: The created DNS record object

        Raises:
            CloudflareAPIError: If the request fails
        """
        data = {
            'type': record_type,
            'name': name,
            'content': content,
            'ttl': ttl,
            'proxied': proxied
        }

        # Priority is only valid for MX and SRV records
        if priority is not None and record_type in ('MX', 'SRV'):
            data['priority'] = priority

        return self._request('POST', f'/zones/{zone_id}/dns_records', data)

    def update_dns_record(self, zone_id, record_id, record_type, name, content,
                          ttl=1, priority=None, proxied=False):
        """
        Update an existing DNS record.

        Args:
            zone_id: The zone identifier
            record_id: The DNS record identifier
            record_type: DNS record type (A, AAAA, CNAME, MX, TXT, etc.)
            name: DNS record name (e.g., 'subdomain.example.com')
            content: DNS record content (e.g., IP address, target hostname)
            ttl: Time to live for DNS record (1 = automatic, otherwise seconds)
            priority: Priority for MX/SRV records (optional)
            proxied: Whether the record receives Cloudflare's protection (default False)

        Returns:
            dict: The updated DNS record object

        Raises:
            CloudflareAPIError: If the request fails
        """
        data = {
            'type': record_type,
            'name': name,
            'content': content,
            'ttl': ttl,
            'proxied': proxied
        }

        # Priority is only valid for MX and SRV records
        if priority is not None and record_type in ('MX', 'SRV'):
            data['priority'] = priority

        return self._request('PUT', f'/zones/{zone_id}/dns_records/{record_id}', data)

    def delete_dns_record(self, zone_id, record_id):
        """
        Delete a DNS record.

        Args:
            zone_id: The zone identifier
            record_id: The DNS record identifier

        Returns:
            dict: Deletion confirmation (contains 'id' of deleted record)

        Raises:
            CloudflareAPIError: If the request fails
        """
        return self._request('DELETE', f'/zones/{zone_id}/dns_records/{record_id}')


def get_oauth_authorize_url(state):
    """
    Build the OAuth authorization URL for Cloudflare.

    Args:
        state: Random state parameter for CSRF protection

    Returns:
        str: The complete OAuth authorization URL

    Raises:
        ValueError: If required environment variables are not set
    """
    client_id = os.environ.get('CLOUDFLARE_CLIENT_ID')
    redirect_uri = os.environ.get('CLOUDFLARE_REDIRECT_URI')

    if not client_id:
        raise ValueError("CLOUDFLARE_CLIENT_ID environment variable is not set")
    if not redirect_uri:
        raise ValueError("CLOUDFLARE_REDIRECT_URI environment variable is not set")

    params = {
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': CLOUDFLARE_OAUTH_SCOPES,
        'state': state
    }

    return f"{CLOUDFLARE_OAUTH_AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code_for_tokens(code):
    """
    Exchange an authorization code for access and refresh tokens.

    Args:
        code: The authorization code received from Cloudflare OAuth callback

    Returns:
        dict: Token response containing 'access_token', 'refresh_token',
              'expires_in', and 'token_type'

    Raises:
        CloudflareAPIError: If the token exchange fails
        ValueError: If required environment variables are not set
    """
    client_id = os.environ.get('CLOUDFLARE_CLIENT_ID')
    client_secret = os.environ.get('CLOUDFLARE_CLIENT_SECRET')
    redirect_uri = os.environ.get('CLOUDFLARE_REDIRECT_URI')

    if not client_id:
        raise ValueError("CLOUDFLARE_CLIENT_ID environment variable is not set")
    if not client_secret:
        raise ValueError("CLOUDFLARE_CLIENT_SECRET environment variable is not set")
    if not redirect_uri:
        raise ValueError("CLOUDFLARE_REDIRECT_URI environment variable is not set")

    data = {
        'grant_type': 'authorization_code',
        'code': code,
        'client_id': client_id,
        'client_secret': client_secret,
        'redirect_uri': redirect_uri
    }

    try:
        response = requests.post(
            CLOUDFLARE_OAUTH_TOKEN_URL,
            data=data,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            timeout=30
        )
    except requests.exceptions.Timeout:
        raise CloudflareAPIError("Token exchange request timed out")
    except requests.exceptions.RequestException as e:
        raise CloudflareAPIError(f"Token exchange request failed: {str(e)}")

    try:
        response_data = response.json()
    except ValueError:
        raise CloudflareAPIError(
            "Invalid JSON response from token endpoint",
            status_code=response.status_code
        )

    # Check for OAuth error response
    if 'error' in response_data:
        error_description = response_data.get('error_description', response_data['error'])
        raise CloudflareAPIError(
            f"OAuth error: {error_description}",
            status_code=response.status_code
        )

    # Ensure we have the required tokens
    if 'access_token' not in response_data:
        raise CloudflareAPIError(
            "Token response missing access_token",
            status_code=response.status_code
        )

    return response_data


def refresh_access_token(refresh_token):
    """
    Refresh an expired access token using a refresh token.

    Args:
        refresh_token: The refresh token to use

    Returns:
        dict: Token response containing new 'access_token', 'refresh_token',
              'expires_in', and 'token_type'

    Raises:
        CloudflareAPIError: If the token refresh fails
        ValueError: If required environment variables are not set
    """
    client_id = os.environ.get('CLOUDFLARE_CLIENT_ID')
    client_secret = os.environ.get('CLOUDFLARE_CLIENT_SECRET')

    if not client_id:
        raise ValueError("CLOUDFLARE_CLIENT_ID environment variable is not set")
    if not client_secret:
        raise ValueError("CLOUDFLARE_CLIENT_SECRET environment variable is not set")

    data = {
        'grant_type': 'refresh_token',
        'refresh_token': refresh_token,
        'client_id': client_id,
        'client_secret': client_secret
    }

    try:
        response = requests.post(
            CLOUDFLARE_OAUTH_TOKEN_URL,
            data=data,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            timeout=30
        )
    except requests.exceptions.Timeout:
        raise CloudflareAPIError("Token refresh request timed out")
    except requests.exceptions.RequestException as e:
        raise CloudflareAPIError(f"Token refresh request failed: {str(e)}")

    try:
        response_data = response.json()
    except ValueError:
        raise CloudflareAPIError(
            "Invalid JSON response from token endpoint",
            status_code=response.status_code
        )

    # Check for OAuth error response
    if 'error' in response_data:
        error_description = response_data.get('error_description', response_data['error'])
        raise CloudflareAPIError(
            f"OAuth error: {error_description}",
            status_code=response.status_code
        )

    # Ensure we have the required tokens
    if 'access_token' not in response_data:
        raise CloudflareAPIError(
            "Token response missing access_token",
            status_code=response.status_code
        )

    return response_data
