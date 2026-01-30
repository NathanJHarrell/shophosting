# ShopHosting.io Vault Policy
# Grants read access to application secrets

# Read access to ShopHosting secrets
path "secret/data/shophosting/*" {
  capabilities = ["read", "list"]
}

# List available secrets (for debugging/development)
path "secret/metadata/shophosting/*" {
  capabilities = ["list"]
}

# Allow token lookup (for health checks)
path "auth/token/lookup-self" {
  capabilities = ["read"]
}

# Allow token renewal
path "auth/token/renew-self" {
  capabilities = ["update"]
}
