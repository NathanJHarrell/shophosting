# webapp/cloudflare/routes.py
"""
Cloudflare DNS Management Routes

Provides OAuth flow for connecting Cloudflare accounts and DNS record management.
"""

import os
import secrets
import logging
from datetime import datetime, timedelta

from flask import render_template, request, redirect, url_for, flash, session, jsonify
from flask_login import login_required, current_user

from . import cloudflare_bp
from .models import CloudflareConnection, DNSRecordCache
from .api import (
    CloudflareAPI, CloudflareAPIError,
    get_oauth_authorize_url, exchange_code_for_tokens, refresh_access_token
)

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models import Customer

logger = logging.getLogger(__name__)


# =============================================================================
# Helper Functions
# =============================================================================

def get_cloudflare_api(connection):
    """
    Get an authenticated Cloudflare API instance.

    If the access token is expired and a refresh token is available,
    automatically refreshes the token and updates the connection.

    Args:
        connection: CloudflareConnection instance

    Returns:
        CloudflareAPI: Authenticated API client

    Raises:
        CloudflareAPIError: If token refresh fails
    """
    if connection.is_token_expired() and connection.refresh_token:
        logger.info(f"Refreshing expired token for customer {connection.customer_id}")
        try:
            token_data = refresh_access_token(connection.refresh_token)

            # Update connection with new tokens
            connection.access_token = token_data['access_token']
            if 'refresh_token' in token_data:
                connection.refresh_token = token_data['refresh_token']

            # Calculate new expiry time
            expires_in = token_data.get('expires_in', 3600)
            connection.token_expires_at = datetime.now() + timedelta(seconds=expires_in)
            connection.save()

            logger.info(f"Successfully refreshed token for customer {connection.customer_id}")
        except CloudflareAPIError as e:
            logger.error(f"Failed to refresh token for customer {connection.customer_id}: {e}")
            raise

    return CloudflareAPI(connection.access_token)


def sync_dns_records(customer_id, api, zone_id):
    """
    Sync DNS records from Cloudflare to the local cache.

    Fetches all DNS records for the zone and updates the local cache.
    Only caches records of types: A, CNAME, MX, TXT.

    Args:
        customer_id: The customer ID to sync records for
        api: Authenticated CloudflareAPI instance
        zone_id: The Cloudflare zone ID

    Returns:
        list: List of DNSRecordCache objects that were synced
    """
    # Fetch records from Cloudflare (only types we support)
    supported_types = ['A', 'CNAME', 'MX', 'TXT']
    records = api.get_dns_records(zone_id, record_types=supported_types)

    # Clear existing cache for this customer
    DNSRecordCache.clear_customer_cache(customer_id)

    # Cache the new records
    cached_records = []
    sync_time = datetime.now()

    for record in records:
        cache_entry = DNSRecordCache(
            customer_id=customer_id,
            cloudflare_record_id=record['id'],
            record_type=record['type'],
            name=record['name'],
            content=record['content'],
            priority=record.get('priority'),
            proxied=record.get('proxied', False),
            ttl=record.get('ttl', 1),
            synced_at=sync_time
        )
        cache_entry.save()
        cached_records.append(cache_entry)

    # Update connection sync time
    connection = CloudflareConnection.get_by_customer_id(customer_id)
    if connection:
        connection.last_sync_at = sync_time
        connection.save()

    logger.info(f"Synced {len(cached_records)} DNS records for customer {customer_id}")
    return cached_records


# =============================================================================
# OAuth Routes
# =============================================================================

@cloudflare_bp.route('/connect')
@login_required
def connect():
    """
    Initiate OAuth flow to connect Cloudflare account.

    Generates a random state parameter for CSRF protection, stores it in
    the session, and redirects to Cloudflare's OAuth authorization page.
    """
    # Check if already connected
    existing = CloudflareConnection.get_by_customer_id(current_user.id)
    if existing:
        flash('Your Cloudflare account is already connected.', 'info')
        return redirect(url_for('cloudflare.confirm'))

    # Generate state for CSRF protection
    state = secrets.token_urlsafe(32)
    session['cloudflare_oauth_state'] = state

    try:
        auth_url = get_oauth_authorize_url(state)
        logger.info(f"Initiating Cloudflare OAuth for customer {current_user.id}")
        return redirect(auth_url)
    except ValueError as e:
        logger.error(f"Cloudflare OAuth configuration error: {e}")
        flash('Cloudflare integration is not properly configured. Please contact support.', 'error')
        return redirect(url_for('dashboard_overview'))


@cloudflare_bp.route('/callback')
@login_required
def callback():
    """
    Handle OAuth callback from Cloudflare.

    Verifies the state parameter, exchanges the authorization code for tokens,
    saves the connection, and redirects to the confirmation page.
    """
    # Check for errors from Cloudflare
    error = request.args.get('error')
    if error:
        error_description = request.args.get('error_description', error)
        logger.warning(f"Cloudflare OAuth error for customer {current_user.id}: {error_description}")
        flash(f'Cloudflare authorization failed: {error_description}', 'error')
        return redirect(url_for('dashboard_overview'))

    # Verify state parameter
    state = request.args.get('state')
    expected_state = session.pop('cloudflare_oauth_state', None)

    if not state or state != expected_state:
        logger.warning(f"Invalid OAuth state for customer {current_user.id}")
        flash('Invalid authorization state. Please try again.', 'error')
        return redirect(url_for('dashboard_overview'))

    # Get authorization code
    code = request.args.get('code')
    if not code:
        flash('No authorization code received. Please try again.', 'error')
        return redirect(url_for('dashboard_overview'))

    try:
        # Exchange code for tokens
        token_data = exchange_code_for_tokens(code)

        # Calculate token expiry
        expires_in = token_data.get('expires_in', 3600)
        token_expires_at = datetime.now() + timedelta(seconds=expires_in)

        # Create new connection (or update existing)
        existing = CloudflareConnection.get_by_customer_id(current_user.id)
        if existing:
            connection = existing
        else:
            connection = CloudflareConnection(customer_id=current_user.id)

        connection.access_token = token_data['access_token']
        connection.refresh_token = token_data.get('refresh_token')
        connection.token_expires_at = token_expires_at
        connection.connected_at = datetime.now()

        # Try to find the zone for the customer's domain
        api = CloudflareAPI(token_data['access_token'])
        customer = Customer.get_by_id(current_user.id)

        if customer and customer.domain:
            zone = api.get_zone_by_name(customer.domain)
            if zone:
                connection.cloudflare_zone_id = zone['id']
                logger.info(f"Found zone {zone['id']} for domain {customer.domain}")

        connection.save()
        logger.info(f"Cloudflare connection saved for customer {current_user.id}")

        flash('Cloudflare account connected successfully!', 'success')
        return redirect(url_for('cloudflare.confirm'))

    except CloudflareAPIError as e:
        logger.error(f"Cloudflare OAuth token exchange failed for customer {current_user.id}: {e}")
        flash(f'Failed to connect Cloudflare account: {e.message}', 'error')
        return redirect(url_for('dashboard_overview'))
    except ValueError as e:
        logger.error(f"Cloudflare configuration error: {e}")
        flash('Cloudflare integration is not properly configured. Please contact support.', 'error')
        return redirect(url_for('dashboard_overview'))


@cloudflare_bp.route('/confirm')
@login_required
def confirm():
    """
    Show confirmation screen with existing DNS records.

    Fetches current DNS records from Cloudflare and displays them for review.
    """
    connection = CloudflareConnection.get_by_customer_id(current_user.id)
    if not connection:
        flash('Please connect your Cloudflare account first.', 'info')
        return redirect(url_for('cloudflare.connect'))

    customer = Customer.get_by_id(current_user.id)
    records = []
    zones = []
    selected_zone = None

    try:
        api = get_cloudflare_api(connection)

        # Get available zones
        zones = api.get_zones()

        # If zone is selected, get records
        if connection.cloudflare_zone_id:
            records = api.get_dns_records(
                connection.cloudflare_zone_id,
                record_types=['A', 'CNAME', 'MX', 'TXT']
            )
            # Find selected zone info
            for zone in zones:
                if zone['id'] == connection.cloudflare_zone_id:
                    selected_zone = zone
                    break

            # Sync to cache
            sync_dns_records(current_user.id, api, connection.cloudflare_zone_id)

    except CloudflareAPIError as e:
        logger.error(f"Failed to fetch Cloudflare data for customer {current_user.id}: {e}")
        flash(f'Failed to fetch DNS records: {e.message}', 'error')

    return render_template(
        'cloudflare/confirm.html',
        connection=connection,
        customer=customer,
        records=records,
        zones=zones,
        selected_zone=selected_zone
    )


@cloudflare_bp.route('/confirm', methods=['POST'])
@login_required
def confirm_submit():
    """
    Apply DNS changes based on form data.

    Processes the form submission to create/update/delete DNS records
    as specified by the user.
    """
    connection = CloudflareConnection.get_by_customer_id(current_user.id)
    if not connection:
        flash('Please connect your Cloudflare account first.', 'error')
        return redirect(url_for('cloudflare.connect'))

    # Handle zone selection
    zone_id = request.form.get('zone_id')
    if zone_id and zone_id != connection.cloudflare_zone_id:
        connection.cloudflare_zone_id = zone_id
        connection.save()
        flash('Zone selection updated.', 'success')
        return redirect(url_for('cloudflare.confirm'))

    if not connection.cloudflare_zone_id:
        flash('Please select a zone first.', 'warning')
        return redirect(url_for('cloudflare.confirm'))

    try:
        api = get_cloudflare_api(connection)

        # Process any DNS record changes from form
        # This can be extended to handle bulk updates from the confirmation page
        action = request.form.get('action')

        if action == 'sync':
            # Force sync records from Cloudflare
            sync_dns_records(current_user.id, api, connection.cloudflare_zone_id)
            flash('DNS records synchronized successfully.', 'success')

        return redirect(url_for('cloudflare.confirm'))

    except CloudflareAPIError as e:
        logger.error(f"Failed to apply DNS changes for customer {current_user.id}: {e}")
        flash(f'Failed to apply changes: {e.message}', 'error')
        return redirect(url_for('cloudflare.confirm'))


@cloudflare_bp.route('/disconnect', methods=['POST'])
@login_required
def disconnect():
    """
    Remove Cloudflare connection.

    Deletes the stored OAuth tokens and clears the DNS record cache.
    """
    connection = CloudflareConnection.get_by_customer_id(current_user.id)
    if not connection:
        flash('No Cloudflare connection found.', 'info')
        return redirect(url_for('dashboard_overview'))

    try:
        # Clear DNS cache
        DNSRecordCache.clear_customer_cache(current_user.id)

        # Delete connection
        connection.delete()

        logger.info(f"Cloudflare connection removed for customer {current_user.id}")
        flash('Cloudflare account disconnected successfully.', 'success')

    except Exception as e:
        logger.error(f"Failed to disconnect Cloudflare for customer {current_user.id}: {e}")
        flash('Failed to disconnect Cloudflare account. Please try again.', 'error')

    return redirect(url_for('dashboard_overview'))


# =============================================================================
# DNS API Routes
# =============================================================================

@cloudflare_bp.route('/api/records')
@login_required
def api_records():
    """
    List DNS records from cache.

    Returns cached DNS records as JSON. Use POST /api/sync to refresh the cache.

    Returns:
        JSON response with list of DNS records
    """
    connection = CloudflareConnection.get_by_customer_id(current_user.id)
    if not connection:
        return jsonify({'error': 'Cloudflare not connected'}), 400

    records = DNSRecordCache.get_by_customer_id(current_user.id)

    return jsonify({
        'success': True,
        'records': [
            {
                'id': r.id,
                'cloudflare_id': r.cloudflare_record_id,
                'type': r.record_type,
                'name': r.name,
                'content': r.content,
                'priority': r.priority,
                'proxied': r.proxied,
                'ttl': r.ttl,
                'synced_at': r.synced_at.isoformat() if r.synced_at else None
            }
            for r in records
        ],
        'zone_id': connection.cloudflare_zone_id,
        'last_sync': connection.last_sync_at.isoformat() if connection.last_sync_at else None
    })


@cloudflare_bp.route('/api/records', methods=['POST'])
@login_required
def api_create_record():
    """
    Create a new DNS record.

    Request JSON body:
        - type: Record type (A, CNAME, MX, TXT)
        - name: Record name (e.g., subdomain or @ for root)
        - content: Record content (IP, hostname, or text)
        - priority: Priority for MX records (optional)
        - proxied: Whether to proxy through Cloudflare (optional, default false)
        - ttl: Time to live (optional, default 1 = auto)

    Returns:
        JSON response with created record
    """
    connection = CloudflareConnection.get_by_customer_id(current_user.id)
    if not connection:
        return jsonify({'error': 'Cloudflare not connected'}), 400

    if not connection.cloudflare_zone_id:
        return jsonify({'error': 'No zone selected'}), 400

    data = request.get_json()
    if not data:
        return jsonify({'error': 'Request body required'}), 400

    # Validate required fields
    required = ['type', 'name', 'content']
    for field in required:
        if field not in data:
            return jsonify({'error': f'Missing required field: {field}'}), 400

    # Validate record type
    valid_types = ['A', 'CNAME', 'MX', 'TXT']
    if data['type'] not in valid_types:
        return jsonify({'error': f'Invalid record type. Must be one of: {", ".join(valid_types)}'}), 400

    try:
        api = get_cloudflare_api(connection)

        # Create record in Cloudflare
        result = api.create_dns_record(
            zone_id=connection.cloudflare_zone_id,
            record_type=data['type'],
            name=data['name'],
            content=data['content'],
            ttl=data.get('ttl', 1),
            priority=data.get('priority'),
            proxied=data.get('proxied', False)
        )

        # Add to cache
        cache_entry = DNSRecordCache(
            customer_id=current_user.id,
            cloudflare_record_id=result['id'],
            record_type=result['type'],
            name=result['name'],
            content=result['content'],
            priority=result.get('priority'),
            proxied=result.get('proxied', False),
            ttl=result.get('ttl', 1),
            synced_at=datetime.now()
        )
        cache_entry.save()

        logger.info(f"Created DNS record {result['id']} for customer {current_user.id}")

        return jsonify({
            'success': True,
            'record': {
                'id': cache_entry.id,
                'cloudflare_id': result['id'],
                'type': result['type'],
                'name': result['name'],
                'content': result['content'],
                'priority': result.get('priority'),
                'proxied': result.get('proxied', False),
                'ttl': result.get('ttl', 1)
            }
        })

    except CloudflareAPIError as e:
        logger.error(f"Failed to create DNS record for customer {current_user.id}: {e}")
        return jsonify({'error': e.message}), 400


@cloudflare_bp.route('/api/records/<record_id>', methods=['PUT'])
@login_required
def api_update_record(record_id):
    """
    Update an existing DNS record.

    Args:
        record_id: The Cloudflare record ID to update

    Request JSON body:
        - type: Record type (A, CNAME, MX, TXT)
        - name: Record name
        - content: Record content
        - priority: Priority for MX records (optional)
        - proxied: Whether to proxy through Cloudflare (optional)
        - ttl: Time to live (optional)

    Returns:
        JSON response with updated record
    """
    connection = CloudflareConnection.get_by_customer_id(current_user.id)
    if not connection:
        return jsonify({'error': 'Cloudflare not connected'}), 400

    if not connection.cloudflare_zone_id:
        return jsonify({'error': 'No zone selected'}), 400

    data = request.get_json()
    if not data:
        return jsonify({'error': 'Request body required'}), 400

    # Validate required fields
    required = ['type', 'name', 'content']
    for field in required:
        if field not in data:
            return jsonify({'error': f'Missing required field: {field}'}), 400

    # Validate record type
    valid_types = ['A', 'CNAME', 'MX', 'TXT']
    if data['type'] not in valid_types:
        return jsonify({'error': f'Invalid record type. Must be one of: {", ".join(valid_types)}'}), 400

    try:
        api = get_cloudflare_api(connection)

        # Update record in Cloudflare
        result = api.update_dns_record(
            zone_id=connection.cloudflare_zone_id,
            record_id=record_id,
            record_type=data['type'],
            name=data['name'],
            content=data['content'],
            ttl=data.get('ttl', 1),
            priority=data.get('priority'),
            proxied=data.get('proxied', False)
        )

        # Update cache
        cache_entry = DNSRecordCache(
            customer_id=current_user.id,
            cloudflare_record_id=result['id'],
            record_type=result['type'],
            name=result['name'],
            content=result['content'],
            priority=result.get('priority'),
            proxied=result.get('proxied', False),
            ttl=result.get('ttl', 1),
            synced_at=datetime.now()
        )
        cache_entry.save()

        logger.info(f"Updated DNS record {record_id} for customer {current_user.id}")

        return jsonify({
            'success': True,
            'record': {
                'id': cache_entry.id,
                'cloudflare_id': result['id'],
                'type': result['type'],
                'name': result['name'],
                'content': result['content'],
                'priority': result.get('priority'),
                'proxied': result.get('proxied', False),
                'ttl': result.get('ttl', 1)
            }
        })

    except CloudflareAPIError as e:
        logger.error(f"Failed to update DNS record {record_id} for customer {current_user.id}: {e}")
        return jsonify({'error': e.message}), 400


@cloudflare_bp.route('/api/records/<record_id>', methods=['DELETE'])
@login_required
def api_delete_record(record_id):
    """
    Delete a DNS record.

    Args:
        record_id: The Cloudflare record ID to delete

    Returns:
        JSON response confirming deletion
    """
    connection = CloudflareConnection.get_by_customer_id(current_user.id)
    if not connection:
        return jsonify({'error': 'Cloudflare not connected'}), 400

    if not connection.cloudflare_zone_id:
        return jsonify({'error': 'No zone selected'}), 400

    try:
        api = get_cloudflare_api(connection)

        # Delete record from Cloudflare
        api.delete_dns_record(
            zone_id=connection.cloudflare_zone_id,
            record_id=record_id
        )

        # Remove from cache
        DNSRecordCache.delete_by_cloudflare_id(record_id)

        logger.info(f"Deleted DNS record {record_id} for customer {current_user.id}")

        return jsonify({
            'success': True,
            'message': 'Record deleted successfully'
        })

    except CloudflareAPIError as e:
        logger.error(f"Failed to delete DNS record {record_id} for customer {current_user.id}: {e}")
        return jsonify({'error': e.message}), 400


@cloudflare_bp.route('/api/sync', methods=['POST'])
@login_required
def api_sync():
    """
    Force sync DNS records from Cloudflare.

    Fetches all DNS records from Cloudflare and updates the local cache.

    Returns:
        JSON response with synced records count
    """
    connection = CloudflareConnection.get_by_customer_id(current_user.id)
    if not connection:
        return jsonify({'error': 'Cloudflare not connected'}), 400

    if not connection.cloudflare_zone_id:
        return jsonify({'error': 'No zone selected'}), 400

    try:
        api = get_cloudflare_api(connection)
        records = sync_dns_records(current_user.id, api, connection.cloudflare_zone_id)

        return jsonify({
            'success': True,
            'message': f'Synced {len(records)} records',
            'count': len(records),
            'synced_at': datetime.now().isoformat()
        })

    except CloudflareAPIError as e:
        logger.error(f"Failed to sync DNS records for customer {current_user.id}: {e}")
        return jsonify({'error': e.message}), 400
