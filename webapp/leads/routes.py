"""
Leads Module - Public Routes
Public-facing routes for the speed test lead funnel
"""

import os
import json
import logging
import re
from urllib.parse import urlparse

from flask import render_template, request, redirect, url_for, flash, jsonify, abort

from . import leads_bp
from .models import SiteScan, MigrationPreviewRequest
from .scanner import run_scan

logger = logging.getLogger(__name__)


# =============================================================================
# Helper Functions
# =============================================================================

def get_client_ip():
    """Get the real client IP address, accounting for proxies"""
    # Check for X-Forwarded-For header (set by reverse proxies)
    x_forwarded_for = request.headers.get('X-Forwarded-For')
    if x_forwarded_for:
        # Take the first IP in the chain (original client)
        return x_forwarded_for.split(',')[0].strip()
    # Check for X-Real-IP header
    x_real_ip = request.headers.get('X-Real-IP')
    if x_real_ip:
        return x_real_ip.strip()
    return request.remote_addr


def validate_url(url):
    """Validate and normalize a URL for scanning"""
    if not url:
        return None, "URL is required"

    url = url.strip()

    # Add https:// if no protocol specified
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url

    try:
        parsed = urlparse(url)
        if not parsed.netloc:
            return None, "Invalid URL format"

        # Basic domain validation
        domain = parsed.netloc.lower()
        if ':' in domain:
            domain = domain.split(':')[0]

        # Check for valid domain format
        if not re.match(r'^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$', domain):
            return None, "Invalid domain format"

        # Reconstruct clean URL with just the domain
        clean_url = f"https://{domain}"
        return clean_url, None

    except Exception as e:
        logger.warning(f"URL validation error: {e}")
        return None, "Invalid URL format"


def validate_email(email):
    """Basic email validation"""
    if not email:
        return None, "Email is required"

    email = email.strip().lower()

    # Basic email format check
    if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
        return None, "Invalid email format"

    return email, None


# =============================================================================
# Speed Test Routes
# =============================================================================

@leads_bp.route('/speed-test')
def speed_test():
    """Landing page for the speed test tool"""
    return render_template('leads/speed_test.html')


@leads_bp.route('/speed-test/scan', methods=['POST'])
def speed_test_scan():
    """
    Submit a URL for scanning.
    Creates a SiteScan record and enqueues a background job.
    Returns the scan_id for polling progress.

    Rate limited to 10 scans per hour per IP.
    """
    # Get and validate URL
    data = request.get_json() if request.is_json else request.form
    url = data.get('url', '').strip()

    clean_url, error = validate_url(url)
    if error:
        if request.is_json:
            return jsonify({'error': error}), 400
        flash(error, 'error')
        return redirect(url_for('leads.speed_test'))

    client_ip = get_client_ip()

    try:
        # Create scan record
        scan = SiteScan.create(clean_url, client_ip)

        if not scan:
            error_msg = "Failed to create scan record"
            if request.is_json:
                return jsonify({'error': error_msg}), 500
            flash(error_msg, 'error')
            return redirect(url_for('leads.speed_test'))

        # Enqueue background job
        try:
            from redis import Redis
            from rq import Queue

            redis_host = os.getenv('REDIS_HOST', 'localhost')
            redis_port = int(os.getenv('REDIS_PORT', 6379))
            redis_conn = Redis(host=redis_host, port=redis_port)
            queue = Queue('scans', connection=redis_conn)

            # Import the background job function
            from leads.jobs import process_site_scan

            job = queue.enqueue(
                process_site_scan,
                scan.id,
                job_timeout=120,  # 2 minute timeout
                result_ttl=3600   # Keep result for 1 hour
            )

            logger.info(f"Scan job {job.id} enqueued for scan {scan.id}, URL: {clean_url}")

        except Exception as e:
            logger.error(f"Failed to enqueue scan job: {e}")
            # Continue anyway - we can run synchronously as fallback or retry later

        if request.is_json:
            return jsonify({
                'success': True,
                'scan_id': scan.id,
                'status_url': url_for('leads.speed_test_status', scan_id=scan.id)
            })

        # For non-AJAX, redirect to status page
        return redirect(url_for('leads.speed_test_status', scan_id=scan.id))

    except Exception as e:
        logger.error(f"Error creating scan: {e}")
        error_msg = "An error occurred. Please try again."
        if request.is_json:
            return jsonify({'error': error_msg}), 500
        flash(error_msg, 'error')
        return redirect(url_for('leads.speed_test'))


@leads_bp.route('/speed-test/scan/<int:scan_id>/status')
def speed_test_status(scan_id):
    """
    Poll for scan progress.
    Returns JSON with scan status and progress information.
    """
    scan = SiteScan.get_by_id(scan_id)

    if not scan:
        if request.is_json or request.headers.get('Accept') == 'application/json':
            return jsonify({'error': 'Scan not found'}), 404
        abort(404)

    # Determine scan status based on whether results are populated
    if scan.performance_score is not None:
        status = 'completed'
        progress = 100
    elif scan.pagespeed_data:
        # Partial results
        status = 'processing'
        progress = 75
    else:
        status = 'pending'
        progress = 10

    response_data = {
        'scan_id': scan.id,
        'url': scan.url,
        'status': status,
        'progress': progress,
        'performance_score': scan.performance_score,
        'load_time_ms': scan.load_time_ms,
        'ttfb_ms': scan.ttfb_ms,
        'has_email': scan.email is not None,
        'created_at': scan.created_at.isoformat() if scan.created_at else None
    }

    if status == 'completed' and not scan.email:
        # Scan complete but email not provided - show unlock prompt
        response_data['unlock_url'] = url_for('leads.speed_test_unlock', scan_id=scan.id)
        response_data['message'] = 'Enter your email to see the full report'

    if request.is_json or request.headers.get('Accept') == 'application/json':
        return jsonify(response_data)

    # For non-AJAX requests, render a status page
    return render_template('leads/speed_test.html', scan=scan, status=response_data)


@leads_bp.route('/speed-test/scan/<int:scan_id>/unlock', methods=['POST'])
def speed_test_unlock(scan_id):
    """
    Submit email to unlock full report.
    Updates the scan with email and marks conversion time.
    """
    scan = SiteScan.get_by_id(scan_id)

    if not scan:
        if request.is_json:
            return jsonify({'error': 'Scan not found'}), 404
        abort(404)

    # Get and validate email
    data = request.get_json() if request.is_json else request.form
    email = data.get('email', '').strip()

    clean_email, error = validate_email(email)
    if error:
        if request.is_json:
            return jsonify({'error': error}), 400
        flash(error, 'error')
        return redirect(url_for('leads.speed_test_status', scan_id=scan_id))

    try:
        # Update scan with email
        scan.set_email(clean_email)

        logger.info(f"Scan {scan.id} unlocked with email: {clean_email}")

        report_url = url_for('leads.report', scan_id=scan.id)

        if request.is_json:
            return jsonify({
                'success': True,
                'report_url': report_url
            })

        return redirect(report_url)

    except Exception as e:
        logger.error(f"Error unlocking scan: {e}")
        error_msg = "An error occurred. Please try again."
        if request.is_json:
            return jsonify({'error': error_msg}), 500
        flash(error_msg, 'error')
        return redirect(url_for('leads.speed_test_status', scan_id=scan_id))


# =============================================================================
# Report Routes
# =============================================================================

@leads_bp.route('/report/<int:scan_id>')
def report(scan_id):
    """
    Full report page.
    Requires email to be set on the scan (gated content).
    """
    scan = SiteScan.get_by_id(scan_id)

    if not scan:
        abort(404)

    # Require email to view full report
    if not scan.email:
        flash('Please enter your email to view the full report.', 'info')
        return redirect(url_for('leads.speed_test_status', scan_id=scan_id))

    # Check if scan has completed
    if scan.performance_score is None:
        flash('Your scan is still processing. Please check back shortly.', 'info')
        return redirect(url_for('leads.speed_test_status', scan_id=scan_id))

    # Parse JSON data for display
    pagespeed_data = None
    custom_probe_data = None

    if scan.pagespeed_data:
        try:
            pagespeed_data = json.loads(scan.pagespeed_data)
        except (json.JSONDecodeError, TypeError):
            pass

    if scan.custom_probe_data:
        try:
            custom_probe_data = json.loads(scan.custom_probe_data)
        except (json.JSONDecodeError, TypeError):
            pass

    # Check for existing migration preview request
    migration_request = MigrationPreviewRequest.get_by_scan_id(scan_id)

    return render_template(
        'leads/report.html',
        scan=scan,
        pagespeed_data=pagespeed_data,
        custom_probe_data=custom_probe_data,
        migration_request=migration_request
    )


@leads_bp.route('/report/<int:scan_id>/request-preview', methods=['POST'])
def request_preview(scan_id):
    """
    Create a migration preview request.
    This is the CTA for leads to request a hands-on migration preview.
    """
    scan = SiteScan.get_by_id(scan_id)

    if not scan:
        if request.is_json:
            return jsonify({'error': 'Scan not found'}), 404
        abort(404)

    if not scan.email:
        error_msg = "Email required to request a migration preview"
        if request.is_json:
            return jsonify({'error': error_msg}), 400
        flash(error_msg, 'error')
        return redirect(url_for('leads.speed_test_status', scan_id=scan_id))

    # Check if request already exists
    existing = MigrationPreviewRequest.get_by_scan_id(scan_id)
    if existing:
        if request.is_json:
            return jsonify({
                'success': True,
                'message': 'Migration preview request already submitted',
                'request_id': existing.id
            })
        flash('You have already requested a migration preview. Our team will contact you soon!', 'info')
        return redirect(url_for('leads.report', scan_id=scan_id))

    # Get form data
    data = request.get_json() if request.is_json else request.form

    monthly_revenue = data.get('monthly_revenue')
    if monthly_revenue:
        try:
            monthly_revenue = float(monthly_revenue)
        except (ValueError, TypeError):
            monthly_revenue = None

    current_host = data.get('current_host', '').strip() or None

    # Detect platform from scan data
    store_platform = 'unknown'
    if scan.custom_probe_data:
        try:
            probe_data = json.loads(scan.custom_probe_data)
            store_platform = probe_data.get('technology', {}).get('platform', 'unknown')
        except (json.JSONDecodeError, TypeError):
            pass

    try:
        migration_request = MigrationPreviewRequest.create(
            site_scan_id=scan.id,
            email=scan.email,
            store_url=scan.url,
            store_platform=store_platform,
            monthly_revenue=monthly_revenue,
            current_host=current_host
        )

        if not migration_request:
            raise Exception("Failed to create migration request")

        logger.info(f"Migration preview request {migration_request.id} created for scan {scan.id}")

        if request.is_json:
            return jsonify({
                'success': True,
                'message': 'Migration preview request submitted successfully',
                'request_id': migration_request.id
            })

        flash('Thank you! Our team will contact you within 24 hours to discuss your migration.', 'success')
        return redirect(url_for('leads.report', scan_id=scan_id))

    except Exception as e:
        logger.error(f"Error creating migration request: {e}")
        error_msg = "An error occurred. Please try again."
        if request.is_json:
            return jsonify({'error': error_msg}), 500
        flash(error_msg, 'error')
        return redirect(url_for('leads.report', scan_id=scan_id))
