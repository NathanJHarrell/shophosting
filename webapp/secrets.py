"""
ShopHosting.io - Secrets Management
Provides a unified interface for accessing secrets from HashiCorp Vault
with graceful fallback to environment variables.
"""

import os
import logging
import hvac
from functools import lru_cache
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class SecretsManager:
    """
    Manages application secrets with Vault integration.

    Usage:
        secrets = SecretsManager()

        # Get a single secret
        db_password = secrets.get('shophosting/database', 'password')

        # Get all secrets at a path
        db_config = secrets.get_all('shophosting/database')

        # Fallback to environment variable
        api_key = secrets.get('shophosting/api', 'key', env_fallback='API_KEY')
    """

    _instance: Optional['SecretsManager'] = None

    def __new__(cls):
        """Singleton pattern to reuse Vault connection."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._initialized = True
        self._client: Optional[hvac.Client] = None
        self._vault_available = False
        self._cache: Dict[str, Dict[str, Any]] = {}

        # Vault configuration from environment
        self._vault_addr = os.getenv('VAULT_ADDR', 'http://127.0.0.1:8200')
        self._vault_token = os.getenv('VAULT_TOKEN')
        self._vault_role_id = os.getenv('VAULT_ROLE_ID')
        self._vault_secret_id = os.getenv('VAULT_SECRET_ID')

        # Attempt to connect to Vault
        self._connect()

    def _connect(self) -> bool:
        """Establish connection to Vault."""
        try:
            self._client = hvac.Client(url=self._vault_addr)

            # Try AppRole authentication first (preferred for applications)
            if self._vault_role_id and self._vault_secret_id:
                auth_result = self._client.auth.approle.login(
                    role_id=self._vault_role_id,
                    secret_id=self._vault_secret_id
                )
                self._client.token = auth_result['auth']['client_token']
                logger.info("Connected to Vault using AppRole authentication")

            # Fall back to token authentication
            elif self._vault_token:
                self._client.token = self._vault_token
                logger.info("Connected to Vault using token authentication")
            else:
                logger.warning("No Vault credentials configured, using environment variables only")
                return False

            # Verify connection
            if self._client.is_authenticated():
                self._vault_available = True
                logger.info("Vault connection verified")
                return True
            else:
                logger.warning("Vault authentication failed")
                return False

        except Exception as e:
            logger.warning(f"Could not connect to Vault: {e}")
            logger.warning("Falling back to environment variables for secrets")
            self._vault_available = False
            return False

    @property
    def vault_available(self) -> bool:
        """Check if Vault is currently available."""
        return self._vault_available

    def get(self, path: str, key: str, env_fallback: Optional[str] = None,
            default: Optional[str] = None) -> Optional[str]:
        """
        Get a single secret value.

        Args:
            path: Vault path (e.g., 'shophosting/database')
            key: Key within the secret (e.g., 'password')
            env_fallback: Environment variable to check if Vault unavailable
            default: Default value if not found anywhere

        Returns:
            The secret value, or default if not found
        """
        # Try Vault first
        if self._vault_available:
            try:
                secrets = self._get_from_vault(path)
                if secrets and key in secrets:
                    return secrets[key]
            except Exception as e:
                logger.warning(f"Error fetching secret from Vault: {e}")

        # Fall back to environment variable
        if env_fallback:
            value = os.getenv(env_fallback)
            if value is not None:
                return value

        return default

    def get_all(self, path: str, env_prefix: Optional[str] = None) -> Dict[str, Any]:
        """
        Get all secrets at a path.

        Args:
            path: Vault path (e.g., 'shophosting/database')
            env_prefix: Prefix for environment variable fallback
                       (e.g., 'DB_' would look for DB_HOST, DB_USER, etc.)

        Returns:
            Dictionary of secret key-value pairs
        """
        # Try Vault first
        if self._vault_available:
            try:
                secrets = self._get_from_vault(path)
                if secrets:
                    return secrets
            except Exception as e:
                logger.warning(f"Error fetching secrets from Vault: {e}")

        # Fall back to environment variables with prefix
        if env_prefix:
            result = {}
            for key, value in os.environ.items():
                if key.startswith(env_prefix):
                    # Convert DB_HOST to host
                    secret_key = key[len(env_prefix):].lower()
                    result[secret_key] = value
            return result

        return {}

    def _get_from_vault(self, path: str) -> Optional[Dict[str, Any]]:
        """Fetch secrets from Vault with caching."""
        if path in self._cache:
            return self._cache[path]

        if not self._client:
            return None

        try:
            # KV v2 secrets engine
            response = self._client.secrets.kv.v2.read_secret_version(
                path=path,
                mount_point='secret'
            )

            if response and 'data' in response and 'data' in response['data']:
                secrets = response['data']['data']
                self._cache[path] = secrets
                return secrets

        except hvac.exceptions.InvalidPath:
            logger.warning(f"Secret path not found: secret/{path}")
        except Exception as e:
            logger.error(f"Error reading from Vault: {e}")

        return None

    def clear_cache(self):
        """Clear the secrets cache (useful after secret rotation)."""
        self._cache.clear()
        logger.info("Secrets cache cleared")

    def refresh_connection(self):
        """Attempt to reconnect to Vault."""
        self._vault_available = False
        self._cache.clear()
        self._connect()


# Singleton instance for easy access
_secrets_manager: Optional[SecretsManager] = None


def get_secrets_manager() -> SecretsManager:
    """Get the singleton SecretsManager instance."""
    global _secrets_manager
    if _secrets_manager is None:
        _secrets_manager = SecretsManager()
    return _secrets_manager


# Convenience functions for common secrets
def get_secret(path: str, key: str, env_fallback: Optional[str] = None,
               default: Optional[str] = None) -> Optional[str]:
    """Get a single secret value."""
    return get_secrets_manager().get(path, key, env_fallback, default)


def get_db_password() -> Optional[str]:
    """Get database password from Vault or environment."""
    return get_secret('shophosting/database', 'password', 'DB_PASSWORD')


def get_stripe_secret_key() -> Optional[str]:
    """Get Stripe secret key from Vault or environment."""
    return get_secret('shophosting/stripe', 'secret_key', 'STRIPE_SECRET_KEY')


def get_stripe_publishable_key() -> Optional[str]:
    """Get Stripe publishable key from Vault or environment."""
    return get_secret('shophosting/stripe', 'publishable_key', 'STRIPE_PUBLISHABLE_KEY')


def get_stripe_webhook_secret() -> Optional[str]:
    """Get Stripe webhook secret from Vault or environment."""
    return get_secret('shophosting/stripe', 'webhook_secret', 'STRIPE_WEBHOOK_SECRET')


def get_flask_secret_key() -> Optional[str]:
    """Get Flask secret key from Vault or environment."""
    return get_secret('shophosting/app', 'secret_key', 'SECRET_KEY')


def get_redis_password() -> Optional[str]:
    """Get Redis password from Vault or environment."""
    return get_secret('shophosting/redis', 'password', 'REDIS_PASSWORD')
