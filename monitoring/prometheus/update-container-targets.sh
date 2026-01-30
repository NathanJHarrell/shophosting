#!/bin/bash
# Generate file_sd targets mapping container IDs to names

OUTPUT_FILE="/opt/shophosting/monitoring/prometheus/targets/containers.json"

# Get all running containers with their IDs and names
containers=$(docker ps --format '{{.ID}} {{.Names}}' 2>/dev/null)

# Start JSON array
echo "[" > "$OUTPUT_FILE.tmp"

first=true
while read -r id name; do
    [ -z "$id" ] && continue
    
    # Extract customer ID from container name (customer-X-...)
    customer_id=$(echo "$name" | grep -oP 'customer-\K[0-9]+' || echo "")
    
    if [ -n "$customer_id" ]; then
        if [ "$first" = true ]; then
            first=false
        else
            echo "," >> "$OUTPUT_FILE.tmp"
        fi
        
        cat >> "$OUTPUT_FILE.tmp" << ENTRY
  {
    "targets": ["cadvisor:8080"],
    "labels": {
      "container_id": "$id",
      "container_name": "$name",
      "customer_id": "$customer_id"
    }
  }
ENTRY
    fi
done <<< "$containers"

echo "]" >> "$OUTPUT_FILE.tmp"
mv "$OUTPUT_FILE.tmp" "$OUTPUT_FILE"
