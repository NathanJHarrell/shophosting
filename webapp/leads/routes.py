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
from .models import SiteScan, MigrationPreviewRequest, SpeedBattle
from .scanner import run_scan
from .battle_scorer import calculate_battle_score, get_round_breakdown, get_score_tier, get_weakest_category
from .jobs import run_speed_battle, send_battle_report_email

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
        wants_json = request.is_json or request.headers.get('Accept') == 'application/json'
        if wants_json:
            return jsonify({'error': error}), 400
        flash(error, 'error')
        return redirect(url_for('leads.speed_test'))

    client_ip = get_client_ip()

    try:
        # Create scan record
        scan = SiteScan.create(clean_url, client_ip)

        if not scan:
            error_msg = "Failed to create scan record"
            wants_json = request.is_json or request.headers.get('Accept') == 'application/json'
            if wants_json:
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

        # Check if client wants JSON response (AJAX request)
        wants_json = request.is_json or request.headers.get('Accept') == 'application/json'
        if wants_json:
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
        wants_json = request.is_json or request.headers.get('Accept') == 'application/json'
        if wants_json:
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
    # The scan is complete if we have ANY results (score, pagespeed data, or custom probe data)
    # This handles cases where PageSpeed API fails but custom probes succeed
    if scan.performance_score is not None:
        status = 'completed'
        progress = 100
    elif scan.pagespeed_data or scan.custom_probe_data:
        # Scan job finished but may have partial results (e.g., PageSpeed API failed)
        # Still mark as completed so user can see whatever results we have
        status = 'completed'
        progress = 100
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

        # Enqueue email notification job
        try:
            from redis import Redis
            from rq import Queue

            redis_host = os.getenv('REDIS_HOST', 'localhost')
            redis_port = int(os.getenv('REDIS_PORT', 6379))
            redis_conn = Redis(host=redis_host, port=redis_port)
            queue = Queue('emails', connection=redis_conn)

            from leads.jobs import send_report_ready_email
            queue.enqueue(send_report_ready_email, scan.id, job_timeout=60)
            logger.info(f"Report ready email job enqueued for scan {scan.id}")

        except Exception as email_error:
            # Don't fail the unlock if email fails to enqueue
            logger.warning(f"Failed to enqueue report email: {email_error}")

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

        # Enqueue confirmation email job
        try:
            from redis import Redis
            from rq import Queue

            redis_host = os.getenv('REDIS_HOST', 'localhost')
            redis_port = int(os.getenv('REDIS_PORT', 6379))
            redis_conn = Redis(host=redis_host, port=redis_port)
            queue = Queue('emails', connection=redis_conn)

            from leads.jobs import send_migration_preview_confirmation
            queue.enqueue(send_migration_preview_confirmation, migration_request.id, job_timeout=60)
            logger.info(f"Migration preview confirmation email job enqueued for request {migration_request.id}")

        except Exception as email_error:
            # Don't fail the request if email fails to enqueue
            logger.warning(f"Failed to enqueue migration confirmation email: {email_error}")

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


# =============================================================================
# Speed Battle Routes
# =============================================================================

def get_domain(url):
    """Extract domain from URL for comparison"""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if ':' in domain:
            domain = domain.split(':')[0]
        return domain
    except Exception:
        return None


@leads_bp.route('/speed-battle')
def speed_battle():
    """Landing page for the speed battle tool"""
    referrer_battle = None

    # Check for referral parameter
    ref = request.args.get('ref')
    if ref:
        referrer_battle = SpeedBattle.get_by_uid(ref)

    return render_template('leads/speed_battle.html', referrer_battle=referrer_battle)


@leads_bp.route('/speed-battle', methods=['POST'])
def speed_battle_start():
    """
    Start a new speed battle.
    Creates a SpeedBattle record and enqueues a background job.
    Returns the battle_uid for polling progress.
    """
    data = request.get_json() if request.is_json else request.form

    # Get and validate URLs
    challenger_url = data.get('challenger_url', '').strip()
    opponent_url = data.get('opponent_url', '').strip()

    # Validate challenger URL
    clean_challenger_url, error = validate_url(challenger_url)
    if error:
        return jsonify({'error': f'Challenger URL: {error}'}), 400

    # Validate opponent URL
    clean_opponent_url, error = validate_url(opponent_url)
    if error:
        return jsonify({'error': f'Opponent URL: {error}'}), 400

    # Check URLs are from different domains
    challenger_domain = get_domain(clean_challenger_url)
    opponent_domain = get_domain(clean_opponent_url)

    if challenger_domain == opponent_domain:
        return jsonify({'error': 'URLs must be from different domains'}), 400

    # Check for referrer battle
    referrer_battle_id = None
    ref = request.args.get('ref')
    if ref:
        referrer_battle = SpeedBattle.get_by_uid(ref)
        if referrer_battle:
            referrer_battle_id = referrer_battle.id

    client_ip = get_client_ip()

    try:
        # Create battle record
        battle = SpeedBattle.create(
            challenger_url=clean_challenger_url,
            opponent_url=clean_opponent_url,
            ip_address=client_ip,
            referrer_battle_id=referrer_battle_id
        )

        if not battle:
            return jsonify({'error': 'Failed to create battle record'}), 500

        # Enqueue background job
        try:
            from redis import Redis
            from rq import Queue

            redis_host = os.getenv('REDIS_HOST', 'localhost')
            redis_port = int(os.getenv('REDIS_PORT', 6379))
            redis_conn = Redis(host=redis_host, port=redis_port)
            queue = Queue('battles', connection=redis_conn)

            job = queue.enqueue(
                run_speed_battle,
                battle.id,
                job_timeout=300,  # 5 minute timeout (2 scans)
                result_ttl=3600
            )

            logger.info(f"Battle job {job.id} enqueued for battle {battle.id}")

        except Exception as e:
            logger.error(f"Failed to enqueue battle job: {e}")
            # Continue anyway - job can be retried

        return jsonify({
            'battle_uid': battle.battle_uid,
            'redirect_url': url_for('leads.speed_battle_results', battle_uid=battle.battle_uid)
        })

    except Exception as e:
        logger.error(f"Error creating battle: {e}")
        return jsonify({'error': 'An error occurred. Please try again.'}), 500


@leads_bp.route('/speed-battle/<battle_uid>')
def speed_battle_results(battle_uid):
    """
    Results page for a speed battle.
    Shows progress if still scanning, results if completed.
    """
    battle = SpeedBattle.get_by_uid(battle_uid)

    if not battle:
        abort(404)

    # Build context for template
    context = {
        'battle': battle,
        'rounds': None,
        'challenger_scan': None,
        'opponent_scan': None,
        'challenger_tier': None,
        'opponent_tier': None
    }

    # If completed, get scan records and build rounds breakdown
    if battle.status == 'completed':
        # Get scan records
        if battle.challenger_scan_id:
            challenger_scan = SiteScan.get_by_id(battle.challenger_scan_id)
            context['challenger_scan'] = challenger_scan

            # Parse pagespeed data for scoring
            challenger_data = {
                'performance_score': battle.challenger_score,
                'url': battle.challenger_url
            }
            if challenger_scan and challenger_scan.pagespeed_data:
                try:
                    challenger_data['pagespeed_data'] = json.loads(challenger_scan.pagespeed_data)
                except (json.JSONDecodeError, TypeError):
                    pass

        if battle.opponent_scan_id:
            opponent_scan = SiteScan.get_by_id(battle.opponent_scan_id)
            context['opponent_scan'] = opponent_scan

            opponent_data = {
                'performance_score': battle.opponent_score,
                'url': battle.opponent_url
            }
            if opponent_scan and opponent_scan.pagespeed_data:
                try:
                    opponent_data['pagespeed_data'] = json.loads(opponent_scan.pagespeed_data)
                except (json.JSONDecodeError, TypeError):
                    pass

        # Get rounds breakdown if we have both scans
        if context.get('challenger_scan') and context.get('opponent_scan'):
            try:
                context['rounds'] = get_round_breakdown(challenger_data, opponent_data)
            except Exception as e:
                logger.warning(f"Failed to get round breakdown: {e}")

        # Get score tiers
        if battle.challenger_score is not None:
            context['challenger_tier'] = get_score_tier(battle.challenger_score)
        if battle.opponent_score is not None:
            context['opponent_tier'] = get_score_tier(battle.opponent_score)

    return render_template('leads/speed_battle_results.html', **context)


@leads_bp.route('/speed-battle/<battle_uid>/status')
def speed_battle_status(battle_uid):
    """
    Polling endpoint for battle progress.
    Returns JSON with battle status.
    """
    battle = SpeedBattle.get_by_uid(battle_uid)

    if not battle:
        return jsonify({'error': 'Battle not found'}), 404

    return jsonify(battle.to_dict())


@leads_bp.route('/speed-battle/<battle_uid>/unlock', methods=['POST'])
def speed_battle_unlock(battle_uid):
    """
    Email capture endpoint.
    Sets email on battle and queues report email.
    """
    battle = SpeedBattle.get_by_uid(battle_uid)

    if not battle:
        return jsonify({'error': 'Battle not found'}), 404

    # Get and validate email
    data = request.get_json() if request.is_json else request.form
    email = data.get('email', '').strip()

    clean_email, error = validate_email(email)
    if error:
        return jsonify({'error': error}), 400

    try:
        # Set email on battle
        battle.set_email(clean_email)

        logger.info(f"Battle {battle.id} unlocked with email: {clean_email}")

        # Enqueue report email job
        try:
            from redis import Redis
            from rq import Queue

            redis_host = os.getenv('REDIS_HOST', 'localhost')
            redis_port = int(os.getenv('REDIS_PORT', 6379))
            redis_conn = Redis(host=redis_host, port=redis_port)
            queue = Queue('emails', connection=redis_conn)

            queue.enqueue(send_battle_report_email, battle.id, job_timeout=60)
            logger.info(f"Battle report email job enqueued for battle {battle.id}")

        except Exception as email_error:
            logger.warning(f"Failed to enqueue battle report email: {email_error}")

        return jsonify({
            'success': True,
            'segment': battle.get_email_segment()
        })

    except Exception as e:
        logger.error(f"Error unlocking battle: {e}")
        return jsonify({'error': 'An error occurred. Please try again.'}), 500


@leads_bp.route('/speed-battle/<battle_uid>/share', methods=['POST'])
def speed_battle_share(battle_uid):
    """
    Track share button clicks.
    Increments counter for the specified platform.
    """
    battle = SpeedBattle.get_by_uid(battle_uid)

    if not battle:
        return jsonify({'error': 'Battle not found'}), 404

    data = request.get_json() if request.is_json else request.form
    platform = data.get('platform', '').strip().lower()

    try:
        battle.increment_share_click(platform)
        return jsonify({'success': True})

    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Error tracking share click: {e}")
        return jsonify({'error': 'An error occurred'}), 500
