"""
Health Check Logic for Status Page
Provides real-time health status for all services
"""

import socket
import requests
from datetime import datetime, timedelta

from models import Server
from .models import StatusIncident, StatusMaintenance, StatusOverride


# Configuration
BACKUP_SERVER = {
    'name': 'Backup Server',
    'hostname': '15.204.249.219',
    'check_port': 22
}

SERVICE_ENDPOINTS = {
    'api': {'name': 'API', 'url': 'https://shophosting.io/api/health', 'timeout': 5},
    'dashboard': {'name': 'Customer Dashboard', 'url': 'https://shophosting.io/dashboard', 'timeout': 5}
}

# Status severity order (lower index = better status)
STATUS_ORDER = ['operational', 'maintenance', 'degraded', 'partial_outage', 'major_outage', 'unknown']

# Status display configuration
STATUS_DISPLAY = {
    'operational': {
        'text': 'Operational',
        'color': 'green',
        'icon': 'check-circle'
    },
    'degraded': {
        'text': 'Degraded Performance',
        'color': 'yellow',
        'icon': 'exclamation-triangle'
    },
    'partial_outage': {
        'text': 'Partial Outage',
        'color': 'orange',
        'icon': 'exclamation-circle'
    },
    'major_outage': {
        'text': 'Major Outage',
        'color': 'red',
        'icon': 'times-circle'
    },
    'maintenance': {
        'text': 'Under Maintenance',
        'color': 'blue',
        'icon': 'wrench'
    },
    'unknown': {
        'text': 'Unknown',
        'color': 'gray',
        'icon': 'question-circle'
    }
}

# Overall status messages
OVERALL_MESSAGES = {
    'operational': 'All systems operational',
    'degraded': 'Some systems experiencing degraded performance',
    'partial_outage': 'Partial system outage',
    'major_outage': 'Major system outage',
    'maintenance': 'Scheduled maintenance in progress',
    'unknown': 'Unable to determine system status'
}


def is_recent(timestamp, minutes=5):
    """
    Check if a timestamp is within the last N minutes.

    Args:
        timestamp: datetime object to check
        minutes: number of minutes to consider as recent (default 5)

    Returns:
        bool: True if timestamp is within last N minutes, False otherwise
    """
    if timestamp is None:
        return False

    cutoff = datetime.now() - timedelta(minutes=minutes)
    return timestamp > cutoff


def get_worse_status(current, new):
    """
    Compare two statuses and return the worse one.

    Order: operational < maintenance < degraded < partial_outage < major_outage < unknown

    Args:
        current: current status string
        new: new status string to compare

    Returns:
        str: the worse of the two statuses
    """
    current_index = STATUS_ORDER.index(current) if current in STATUS_ORDER else len(STATUS_ORDER) - 1
    new_index = STATUS_ORDER.index(new) if new in STATUS_ORDER else len(STATUS_ORDER) - 1

    if new_index > current_index:
        return new
    return current


def get_status_display(status):
    """
    Get display information for a status.

    Args:
        status: status string

    Returns:
        dict: containing text, color, and icon
    """
    if status in STATUS_DISPLAY:
        return STATUS_DISPLAY[status].copy()
    return STATUS_DISPLAY['unknown'].copy()


def get_overall_message(status):
    """
    Get the banner message for an overall status.

    Args:
        status: overall status string

    Returns:
        str: banner message
    """
    return OVERALL_MESSAGES.get(status, OVERALL_MESSAGES['unknown'])


def check_server_health(server):
    """
    Check the health status of a server.

    Priority order:
    1. Check for StatusOverride
    2. Check for active maintenance
    3. Check for active incidents (return worst severity-based status)
    4. Use server.last_heartbeat if recent (< 5 min)
    5. Fallback: HTTP GET to server.hostname/health

    Args:
        server: Server model instance

    Returns:
        str: status string ('operational', 'degraded', 'partial_outage',
             'major_outage', 'maintenance', or 'unknown')
    """
    service_name = f'server_{server.id}'

    # 1. Check for status override
    overrides = StatusOverride.get_active()
    if service_name in overrides:
        return overrides[service_name].display_status

    # 2. Check for active maintenance
    active_maintenance = StatusMaintenance.get_active()
    for maintenance in active_maintenance:
        if maintenance.server_id == server.id or maintenance.server_id is None:
            return 'maintenance'

    # 3. Check for active incidents
    active_incidents = StatusIncident.get_active_for_server(server.id)
    if active_incidents:
        # Map severity to status
        severity_to_status = {
            'critical': 'major_outage',
            'major': 'partial_outage',
            'minor': 'degraded'
        }
        worst_status = 'operational'
        for incident in active_incidents:
            incident_status = severity_to_status.get(incident.severity, 'degraded')
            worst_status = get_worse_status(worst_status, incident_status)
        return worst_status

    # 4. Check last heartbeat if recent
    if is_recent(server.last_heartbeat, minutes=5):
        return 'operational'

    # 5. Fallback: HTTP health check
    try:
        url = f'https://{server.hostname}/health'
        response = requests.get(url, timeout=5, verify=False)
        if response.status_code == 200:
            return 'operational'
        elif 400 <= response.status_code < 500:
            return 'degraded'
        else:
            return 'major_outage'
    except requests.exceptions.Timeout:
        return 'degraded'
    except requests.exceptions.RequestException:
        return 'major_outage'
    except Exception:
        return 'unknown'


def check_backup_server():
    """
    Check the health status of the backup server.

    Process:
    1. Check for StatusOverride for 'backup_server'
    2. Check for global maintenance
    3. TCP socket connect to backup server port

    Returns:
        str: 'operational' if port open, 'major_outage' if not
    """
    # 1. Check for status override
    overrides = StatusOverride.get_active()
    if 'backup_server' in overrides:
        return overrides['backup_server'].display_status

    # 2. Check for global maintenance (server_id is NULL)
    active_maintenance = StatusMaintenance.get_active()
    for maintenance in active_maintenance:
        if maintenance.server_id is None:
            return 'maintenance'

    # 3. TCP socket check
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex((BACKUP_SERVER['hostname'], BACKUP_SERVER['check_port']))
        sock.close()

        if result == 0:
            return 'operational'
        else:
            return 'major_outage'
    except socket.error:
        return 'major_outage'
    except Exception:
        return 'unknown'


def check_service_endpoint(service_key):
    """
    Check the health status of a service endpoint.

    Args:
        service_key: key from SERVICE_ENDPOINTS dict

    Returns:
        str: status string based on HTTP response:
             - 200 = operational
             - 4xx = degraded
             - 5xx = major_outage
             - timeout = degraded
             - error = major_outage
    """
    if service_key not in SERVICE_ENDPOINTS:
        return 'unknown'

    service = SERVICE_ENDPOINTS[service_key]
    service_name = f'service_{service_key}'

    # Check for status override
    overrides = StatusOverride.get_active()
    if service_name in overrides:
        return overrides[service_name].display_status

    # HTTP health check
    try:
        response = requests.get(service['url'], timeout=service['timeout'])
        if response.status_code == 200:
            return 'operational'
        elif 400 <= response.status_code < 500:
            return 'degraded'
        else:
            return 'major_outage'
    except requests.exceptions.Timeout:
        return 'degraded'
    except requests.exceptions.RequestException:
        return 'major_outage'
    except Exception:
        return 'unknown'


def get_all_statuses():
    """
    Get the health status of all monitored components.

    Returns:
        dict: containing:
            - servers: dict of server statuses keyed by server id
            - backup_server: backup server status
            - services: dict of service statuses keyed by service key
            - overall: worst overall status
            - overall_message: banner message
            - last_updated: timestamp
    """
    result = {
        'servers': {},
        'backup_server': {},
        'services': {},
        'overall': 'operational',
        'overall_message': '',
        'last_updated': datetime.now().isoformat()
    }

    # Check all active servers
    try:
        # Try get_all_active first (may exist), fallback to get_active
        if hasattr(Server, 'get_all_active'):
            servers = Server.get_all_active()
        else:
            servers = Server.get_active()
    except Exception:
        servers = []

    for server in servers:
        status = check_server_health(server)
        result['servers'][server.id] = {
            'name': server.name,
            'hostname': server.hostname,
            'status': status,
            'display': get_status_display(status)
        }
        result['overall'] = get_worse_status(result['overall'], status)

    # Check backup server
    backup_status = check_backup_server()
    result['backup_server'] = {
        'name': BACKUP_SERVER['name'],
        'hostname': BACKUP_SERVER['hostname'],
        'status': backup_status,
        'display': get_status_display(backup_status)
    }
    result['overall'] = get_worse_status(result['overall'], backup_status)

    # Check service endpoints
    for service_key, service_config in SERVICE_ENDPOINTS.items():
        status = check_service_endpoint(service_key)
        result['services'][service_key] = {
            'name': service_config['name'],
            'url': service_config['url'],
            'status': status,
            'display': get_status_display(status)
        }
        result['overall'] = get_worse_status(result['overall'], status)

    # Set overall message
    result['overall_message'] = get_overall_message(result['overall'])
    result['overall_display'] = get_status_display(result['overall'])

    return result
