# HashiCorp Vault Configuration for ShopHosting.io
# Production configuration with file storage backend

# API listener
listener "tcp" {
  address     = "0.0.0.0:8200"
  tls_disable = "true"  # TLS handled by nginx reverse proxy
}

# File storage backend (suitable for single-server deployment)
storage "file" {
  path = "/vault/data"
}

# Disable memory locking (required for some Docker configurations)
disable_mlock = true

# Enable the UI for administration
ui = true

# API address for redirection
api_addr = "http://127.0.0.1:8200"

# Cluster address (for HA configurations - not used currently)
cluster_addr = "http://127.0.0.1:8201"

# Log level
log_level = "info"

# Telemetry for Prometheus metrics
telemetry {
  prometheus_retention_time = "30s"
  disable_hostname = true
}
