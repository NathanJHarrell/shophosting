"""
ShopHosting.io - Prometheus Metrics Endpoint
Exposes monitoring data in Prometheus format for Grafana visualization
"""

import time
from flask import Blueprint, Response
from models import (
    Customer, CustomerMonitoringStatus, MonitoringCheck, MonitoringAlert,
    get_db_connection
)

metrics_bp = Blueprint('metrics', __name__)


def generate_metrics():
    """Generate Prometheus-format metrics"""
    lines = []

    # Help and type declarations
    lines.append('# HELP shophosting_customers_total Total number of customers by status')
    lines.append('# TYPE shophosting_customers_total gauge')

    lines.append('# HELP shophosting_monitoring_status Current monitoring status (1=up, 0=down)')
    lines.append('# TYPE shophosting_monitoring_status gauge')

    lines.append('# HELP shophosting_http_response_time_ms HTTP response time in milliseconds')
    lines.append('# TYPE shophosting_http_response_time_ms gauge')

    lines.append('# HELP shophosting_uptime_percent 24-hour uptime percentage')
    lines.append('# TYPE shophosting_uptime_percent gauge')

    lines.append('# HELP shophosting_cpu_percent CPU usage percentage')
    lines.append('# TYPE shophosting_cpu_percent gauge')

    lines.append('# HELP shophosting_memory_usage_mb Memory usage in megabytes')
    lines.append('# TYPE shophosting_memory_usage_mb gauge')

    lines.append('# HELP shophosting_consecutive_failures Number of consecutive check failures')
    lines.append('# TYPE shophosting_consecutive_failures gauge')

    lines.append('# HELP shophosting_alerts_total Total alerts by type')
    lines.append('# TYPE shophosting_alerts_total counter')

    lines.append('# HELP shophosting_alerts_unacknowledged Number of unacknowledged alerts')
    lines.append('# TYPE shophosting_alerts_unacknowledged gauge')

    # Get customer counts by status
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT status, COUNT(*) as count
            FROM customers
            GROUP BY status
        """)
        for row in cursor.fetchall():
            lines.append(f'shophosting_customers_total{{status="{row["status"]}"}} {row["count"]}')

        cursor.close()
        conn.close()
    except Exception as e:
        lines.append(f'# Error getting customer counts: {e}')

    # Get monitoring status for all customers
    try:
        statuses = CustomerMonitoringStatus.get_all_statuses()

        for s in statuses:
            labels = f'customer_id="{s["customer_id"]}",domain="{s["domain"]}"'

            # HTTP status (1=up, 0=down, 0.5=degraded)
            http_val = 1 if s['http_status'] == 'up' else (0.5 if s['http_status'] == 'degraded' else 0)
            lines.append(f'shophosting_monitoring_status{{type="http",{labels}}} {http_val}')

            # Container status
            container_val = 1 if s['container_status'] == 'up' else (0.5 if s['container_status'] == 'degraded' else 0)
            lines.append(f'shophosting_monitoring_status{{type="container",{labels}}} {container_val}')

            # Response time
            if s['last_http_response_ms'] is not None:
                lines.append(f'shophosting_http_response_time_ms{{{labels}}} {s["last_http_response_ms"]}')

            # Uptime
            if s['uptime_24h'] is not None:
                lines.append(f'shophosting_uptime_percent{{{labels}}} {s["uptime_24h"]}')

            # CPU
            if s['cpu_percent'] is not None:
                lines.append(f'shophosting_cpu_percent{{{labels}}} {s["cpu_percent"]}')

            # Memory
            if s['memory_usage_mb'] is not None:
                lines.append(f'shophosting_memory_usage_mb{{{labels}}} {s["memory_usage_mb"]}')

            # Consecutive failures
            lines.append(f'shophosting_consecutive_failures{{{labels}}} {s["consecutive_failures"]}')

    except Exception as e:
        lines.append(f'# Error getting monitoring status: {e}')

    # Get alert counts
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Total alerts by type
        cursor.execute("""
            SELECT alert_type, COUNT(*) as count
            FROM monitoring_alerts
            GROUP BY alert_type
        """)
        for row in cursor.fetchall():
            lines.append(f'shophosting_alerts_total{{type="{row["alert_type"]}"}} {row["count"]}')

        # Unacknowledged alerts
        cursor.execute("SELECT COUNT(*) as count FROM monitoring_alerts WHERE acknowledged = FALSE")
        unacked = cursor.fetchone()['count']
        lines.append(f'shophosting_alerts_unacknowledged {unacked}')

        cursor.close()
        conn.close()
    except Exception as e:
        lines.append(f'# Error getting alert counts: {e}')

    # Summary stats
    try:
        stats = CustomerMonitoringStatus.get_summary_stats()
        lines.append(f'shophosting_monitored_total {stats["total"]}')
        lines.append(f'shophosting_monitored_up {stats["up"]}')
        lines.append(f'shophosting_monitored_down {stats["down"]}')
        lines.append(f'shophosting_monitored_degraded {stats["degraded"]}')
        lines.append(f'shophosting_avg_uptime_percent {stats["avg_uptime"]}')
        lines.append(f'shophosting_avg_response_time_ms {stats["avg_response_time"]}')
    except Exception as e:
        lines.append(f'# Error getting summary stats: {e}')

    return '\n'.join(lines) + '\n'


@metrics_bp.route('/metrics')
def prometheus_metrics():
    """Prometheus metrics endpoint"""
    metrics = generate_metrics()
    return Response(metrics, mimetype='text/plain; version=0.0.4; charset=utf-8')


@metrics_bp.route('/health')
def health_check():
    """Simple health check endpoint for monitoring the monitoring system"""
    return Response('OK', mimetype='text/plain')
