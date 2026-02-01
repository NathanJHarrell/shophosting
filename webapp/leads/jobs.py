"""
Leads Module - Background Jobs
RQ worker jobs for processing site scans and sending lead emails
"""

import os
import json
import logging
from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger(__name__)

# Email template directory
TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), 'email_templates')


def get_email_template(template_name):
    """Load and return a Jinja2 template for emails"""
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))
    return env.get_template(template_name)


def process_site_scan(scan_id):
    """
    Background job to process a site scan.
    Runs the scanner and updates the SiteScan record with results.

    Args:
        scan_id: The ID of the SiteScan record to process
    """
    from .models import SiteScan
    from .scanner import run_scan

    logger.info(f"Processing scan {scan_id}")

    # Get the scan record
    scan = SiteScan.get_by_id(scan_id)
    if not scan:
        logger.error(f"Scan {scan_id} not found")
        return {'error': 'Scan not found'}

    try:
        # Run the scan
        results = run_scan(scan.url)

        # Update the scan record with results
        scan.update_results(
            performance_score=results.get('performance_score'),
            load_time_ms=results.get('load_time_ms'),
            ttfb_ms=results.get('ttfb_ms'),
            pagespeed_data=results.get('pagespeed_data_json'),
            custom_probe_data=results.get('custom_probe_data_json'),
            estimated_revenue_loss=results.get('estimated_revenue_loss')
        )

        logger.info(
            f"Scan {scan_id} completed: score={results.get('performance_score')}, "
            f"load_time={results.get('load_time_ms')}ms"
        )

        return {
            'success': True,
            'scan_id': scan_id,
            'performance_score': results.get('performance_score'),
            'status': results.get('status')
        }

    except Exception as e:
        logger.error(f"Error processing scan {scan_id}: {e}")
        return {
            'error': str(e),
            'scan_id': scan_id
        }


def send_report_ready_email(scan_id):
    """
    Send the "report ready" email when a user provides their email.

    Args:
        scan_id: The ID of the SiteScan record
    """
    from .models import SiteScan
    from email_utils import send_email
    from flask import url_for

    logger.info(f"Sending report ready email for scan {scan_id}")

    scan = SiteScan.get_by_id(scan_id)
    if not scan:
        logger.error(f"Scan {scan_id} not found")
        return {'error': 'Scan not found'}

    if not scan.email:
        logger.error(f"Scan {scan_id} has no email")
        return {'error': 'No email on scan'}

    try:
        # Parse PageSpeed data for issues count
        issues_count = 0
        if scan.pagespeed_data:
            try:
                pagespeed = json.loads(scan.pagespeed_data)
                issues_count = len(pagespeed.get('recommendations', []))
            except (json.JSONDecodeError, TypeError):
                pass

        # Determine score class
        score = scan.performance_score or 0
        if score >= 90:
            score_class = 'good'
        elif score >= 50:
            score_class = 'medium'
        else:
            score_class = 'poor'

        # Format display values
        load_time_display = f"{scan.load_time_ms}ms" if scan.load_time_ms else "N/A"
        ttfb_display = f"{scan.ttfb_ms}ms" if scan.ttfb_ms else "N/A"

        # Build report URL (needs app context for url_for)
        # For now, construct manually
        base_url = os.getenv('BASE_URL', 'https://shophosting.io')
        report_url = f"{base_url}/report/{scan_id}"

        # Render the email template
        template = get_email_template('report_ready.html')
        html_body = template.render(
            url=scan.url,
            performance_score=scan.performance_score or 0,
            score_class=score_class,
            load_time_display=load_time_display,
            ttfb_display=ttfb_display,
            issues_count=issues_count,
            estimated_revenue_loss=scan.estimated_revenue_loss or 0,
            report_url=report_url
        )

        # Send the email
        success, message = send_email(
            to_email=scan.email,
            subject=f"Your Performance Report is Ready - Score: {scan.performance_score}/100",
            html_body=html_body
        )

        if success:
            logger.info(f"Report ready email sent to {scan.email} for scan {scan_id}")
        else:
            logger.error(f"Failed to send report ready email: {message}")

        return {
            'success': success,
            'message': message,
            'scan_id': scan_id
        }

    except Exception as e:
        logger.error(f"Error sending report ready email for scan {scan_id}: {e}")
        return {
            'error': str(e),
            'scan_id': scan_id
        }


def send_migration_preview_confirmation(request_id):
    """
    Send confirmation email when user requests a migration preview.

    Args:
        request_id: The ID of the MigrationPreviewRequest
    """
    from .models import MigrationPreviewRequest, SiteScan
    from email_utils import send_email

    logger.info(f"Sending migration preview confirmation for request {request_id}")

    migration_request = MigrationPreviewRequest.get_by_id(request_id)
    if not migration_request:
        logger.error(f"Migration request {request_id} not found")
        return {'error': 'Request not found'}

    try:
        # Get the related scan for additional data
        scan = SiteScan.get_by_id(migration_request.site_scan_id)

        # Build report URL
        base_url = os.getenv('BASE_URL', 'https://shophosting.io')
        report_url = f"{base_url}/report/{migration_request.site_scan_id}"

        # Render the email template
        template = get_email_template('migration_preview_confirmation.html')
        html_body = template.render(
            store_url=migration_request.store_url,
            store_platform=migration_request.store_platform,
            current_host=migration_request.current_host,
            report_url=report_url
        )

        # Send the email
        success, message = send_email(
            to_email=migration_request.email,
            subject="We're Setting Up Your Migration Preview - ShopHosting",
            html_body=html_body
        )

        if success:
            logger.info(f"Migration preview confirmation sent to {migration_request.email}")
        else:
            logger.error(f"Failed to send migration preview confirmation: {message}")

        return {
            'success': success,
            'message': message,
            'request_id': request_id
        }

    except Exception as e:
        logger.error(f"Error sending migration preview confirmation for request {request_id}: {e}")
        return {
            'error': str(e),
            'request_id': request_id
        }


def send_follow_up_email(scan_id, template_name, subject, days_since=None):
    """
    Send a follow-up nurture email to a lead.

    Args:
        scan_id: The ID of the SiteScan record
        template_name: Name of the email template to use
        subject: Email subject line
        days_since: Optional - for logging/tracking purposes
    """
    from .models import SiteScan
    from email_utils import send_email

    logger.info(f"Sending follow-up email ({template_name}) for scan {scan_id}")

    scan = SiteScan.get_by_id(scan_id)
    if not scan or not scan.email:
        logger.error(f"Scan {scan_id} not found or has no email")
        return {'error': 'Scan not found or no email'}

    try:
        # Parse data for template
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

        # Build URLs
        base_url = os.getenv('BASE_URL', 'https://shophosting.io')
        report_url = f"{base_url}/report/{scan_id}"
        speed_test_url = f"{base_url}/speed-test"

        # Render the email template
        template = get_email_template(template_name)
        html_body = template.render(
            url=scan.url,
            email=scan.email,
            performance_score=scan.performance_score,
            load_time_ms=scan.load_time_ms,
            ttfb_ms=scan.ttfb_ms,
            estimated_revenue_loss=scan.estimated_revenue_loss,
            pagespeed_data=pagespeed_data,
            custom_probe_data=custom_probe_data,
            report_url=report_url,
            speed_test_url=speed_test_url,
            base_url=base_url
        )

        # Send the email
        success, message = send_email(
            to_email=scan.email,
            subject=subject,
            html_body=html_body
        )

        if success:
            logger.info(f"Follow-up email ({template_name}) sent to {scan.email}")
        else:
            logger.error(f"Failed to send follow-up email: {message}")

        return {
            'success': success,
            'message': message,
            'scan_id': scan_id,
            'template': template_name
        }

    except Exception as e:
        logger.error(f"Error sending follow-up email for scan {scan_id}: {e}")
        return {
            'error': str(e),
            'scan_id': scan_id
        }


def run_speed_battle(battle_id):
    """
    Background job to run a speed battle.
    Runs scans on both URLs and updates the SpeedBattle record.

    Args:
        battle_id: The ID of the SpeedBattle record to process
    """
    from .models import SpeedBattle, SiteScan
    from .scanner import run_scan
    from .battle_scorer import calculate_battle_score

    logger.info(f"Processing speed battle {battle_id}")

    battle = SpeedBattle.get_by_id(battle_id)
    if not battle:
        logger.error(f"Battle {battle_id} not found")
        return {'error': 'Battle not found'}

    try:
        # Update status to scanning
        battle.update_status('scanning')

        # Run challenger scan
        logger.info(f"Scanning challenger URL: {battle.challenger_url}")
        challenger_results = run_scan(battle.challenger_url)

        # Create challenger SiteScan record
        challenger_scan = SiteScan.create(battle.challenger_url, battle.ip_address)
        if challenger_scan:
            challenger_scan.update_results(
                performance_score=challenger_results.get('performance_score'),
                load_time_ms=challenger_results.get('load_time_ms'),
                ttfb_ms=challenger_results.get('ttfb_ms'),
                pagespeed_data=challenger_results.get('pagespeed_data_json'),
                custom_probe_data=challenger_results.get('custom_probe_data_json'),
                estimated_revenue_loss=challenger_results.get('estimated_revenue_loss')
            )

        # Run opponent scan
        logger.info(f"Scanning opponent URL: {battle.opponent_url}")
        opponent_results = run_scan(battle.opponent_url)

        # Create opponent SiteScan record
        opponent_scan = SiteScan.create(battle.opponent_url, battle.ip_address)
        if opponent_scan:
            opponent_scan.update_results(
                performance_score=opponent_results.get('performance_score'),
                load_time_ms=opponent_results.get('load_time_ms'),
                ttfb_ms=opponent_results.get('ttfb_ms'),
                pagespeed_data=opponent_results.get('pagespeed_data_json'),
                custom_probe_data=opponent_results.get('custom_probe_data_json'),
                estimated_revenue_loss=opponent_results.get('estimated_revenue_loss')
            )

        # Calculate battle scores
        challenger_score = calculate_battle_score({
            'performance_score': challenger_results.get('performance_score'),
            'pagespeed_data': challenger_results.get('pagespeed_data'),
            'ttfb_ms': challenger_results.get('ttfb_ms'),
            'url': battle.challenger_url
        })

        opponent_score = calculate_battle_score({
            'performance_score': opponent_results.get('performance_score'),
            'pagespeed_data': opponent_results.get('pagespeed_data'),
            'ttfb_ms': opponent_results.get('ttfb_ms'),
            'url': battle.opponent_url
        })

        # Update battle with scores
        battle.update_scores(
            challenger_scan_id=challenger_scan.id if challenger_scan else None,
            challenger_score=challenger_score,
            opponent_scan_id=opponent_scan.id if opponent_scan else None,
            opponent_score=opponent_score
        )

        logger.info(
            f"Battle {battle_id} completed: challenger={challenger_score}, "
            f"opponent={opponent_score}, winner={battle.winner}"
        )

        return {
            'success': True,
            'battle_id': battle_id,
            'winner': battle.winner,
            'margin': battle.margin
        }

    except Exception as e:
        logger.error(f"Error processing battle {battle_id}: {e}")
        battle.update_status('failed', error_message=str(e))
        return {
            'error': str(e),
            'battle_id': battle_id
        }


def send_battle_report_email(battle_id):
    """
    Send battle report email when user provides their email.

    Args:
        battle_id: The ID of the SpeedBattle record
    """
    from .models import SpeedBattle
    from email_utils import send_email

    logger.info(f"Sending battle report email for battle {battle_id}")

    battle = SpeedBattle.get_by_id(battle_id)
    if not battle:
        logger.error(f"Battle {battle_id} not found")
        return {'error': 'Battle not found'}

    if not battle.email:
        logger.error(f"Battle {battle_id} has no email")
        return {'error': 'No email on battle'}

    try:
        # Determine which email template to use based on segment
        segment = battle.email_segment or 'won_close'
        template_name = f"battle_report_{segment}.html"

        # Build battle URL
        base_url = os.getenv('BASE_URL', 'https://shophosting.io')
        battle_url = f"{base_url}/speed-battle/{battle.battle_uid}"
        share_url = f"{base_url}/speed-battle?ref={battle.battle_uid}"

        # Render the email template
        try:
            template = get_email_template(template_name)
        except Exception:
            # Fallback to generic template
            template = get_email_template('battle_report.html')

        html_body = template.render(
            challenger_url=battle.challenger_url,
            opponent_url=battle.opponent_url,
            challenger_score=battle.challenger_score,
            opponent_score=battle.opponent_score,
            winner=battle.winner,
            margin=battle.margin,
            battle_url=battle_url,
            share_url=share_url,
            segment=segment
        )

        # Determine subject based on outcome
        if battle.winner == 'challenger':
            subject = f"You won! {battle.challenger_score} vs {battle.opponent_score} - Speed Battle Results"
        elif battle.winner == 'opponent':
            subject = f"Close battle! See how to improve your score - Speed Battle Results"
        else:
            subject = f"It's a tie! {battle.challenger_score} vs {battle.opponent_score} - Speed Battle Results"

        # Send the email
        success, message = send_email(
            to_email=battle.email,
            subject=subject,
            html_body=html_body
        )

        if success:
            logger.info(f"Battle report email sent to {battle.email} for battle {battle_id}")
        else:
            logger.error(f"Failed to send battle report email: {message}")

        return {
            'success': success,
            'message': message,
            'battle_id': battle_id
        }

    except Exception as e:
        logger.error(f"Error sending battle report email for battle {battle_id}: {e}")
        return {
            'error': str(e),
            'battle_id': battle_id
        }
