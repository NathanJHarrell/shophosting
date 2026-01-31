"""
Leads Module - Background Jobs
RQ worker jobs for processing site scans
"""

import logging

logger = logging.getLogger(__name__)


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
