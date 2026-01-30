"""
Container metrics exporter for customer containers.
Exposes CPU, memory, and network metrics with customer_id labels.
"""
import docker
from flask import Blueprint, Response

container_metrics_bp = Blueprint('container_metrics', __name__)

def get_container_stats():
    """Get stats for all customer containers."""
    try:
        client = docker.from_env()
        containers = client.containers.list()
        
        metrics = []
        
        for container in containers:
            name = container.name
            # Only process customer containers
            if not name.startswith('customer-'):
                continue
            
            # Extract customer ID from name (customer-X-...)
            parts = name.split('-')
            if len(parts) < 2:
                continue
            customer_id = parts[1]
            
            # Get container type (web, db, redis, etc.)
            container_type = parts[2] if len(parts) > 2 else 'unknown'
            
            try:
                stats = container.stats(stream=False)
                
                # CPU calculation
                cpu_delta = stats['cpu_stats']['cpu_usage']['total_usage'] - \
                           stats['precpu_stats']['cpu_usage']['total_usage']
                system_delta = stats['cpu_stats']['system_cpu_usage'] - \
                              stats['precpu_stats']['system_cpu_usage']
                cpu_count = stats['cpu_stats'].get('online_cpus', 1)
                
                if system_delta > 0:
                    cpu_percent = (cpu_delta / system_delta) * cpu_count * 100.0
                else:
                    cpu_percent = 0.0
                
                # Memory calculation
                memory_usage = stats['memory_stats'].get('usage', 0)
                memory_limit = stats['memory_stats'].get('limit', 1)
                memory_percent = (memory_usage / memory_limit) * 100.0 if memory_limit > 0 else 0
                
                # Network I/O
                networks = stats.get('networks', {})
                rx_bytes = sum(n.get('rx_bytes', 0) for n in networks.values())
                tx_bytes = sum(n.get('tx_bytes', 0) for n in networks.values())
                
                labels = f'customer_id="{customer_id}",container_name="{name}",container_type="{container_type}"'
                
                metrics.append(f'container_cpu_usage_percent{{{labels}}} {cpu_percent:.2f}')
                metrics.append(f'container_memory_usage_bytes{{{labels}}} {memory_usage}')
                metrics.append(f'container_memory_limit_bytes{{{labels}}} {memory_limit}')
                metrics.append(f'container_memory_usage_percent{{{labels}}} {memory_percent:.2f}')
                metrics.append(f'container_network_rx_bytes{{{labels}}} {rx_bytes}')
                metrics.append(f'container_network_tx_bytes{{{labels}}} {tx_bytes}')
                
            except Exception as e:
                # Container might have stopped
                continue
                
        return metrics
    except Exception as e:
        return [f'# Error: {e}']


@container_metrics_bp.route('/metrics/containers')
def container_metrics():
    """Prometheus metrics endpoint for container stats."""
    metrics = [
        '# HELP container_cpu_usage_percent CPU usage percentage',
        '# TYPE container_cpu_usage_percent gauge',
        '# HELP container_memory_usage_bytes Memory usage in bytes',
        '# TYPE container_memory_usage_bytes gauge',
        '# HELP container_memory_limit_bytes Memory limit in bytes',
        '# TYPE container_memory_limit_bytes gauge',
        '# HELP container_memory_usage_percent Memory usage percentage',
        '# TYPE container_memory_usage_percent gauge',
        '# HELP container_network_rx_bytes Network received bytes',
        '# TYPE container_network_rx_bytes counter',
        '# HELP container_network_tx_bytes Network transmitted bytes',
        '# TYPE container_network_tx_bytes counter',
    ]
    metrics.extend(get_container_stats())
    
    return Response('\n'.join(metrics) + '\n', mimetype='text/plain')
