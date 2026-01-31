"""
Site Scanner Module for Lead Generation
Provides comprehensive site analysis including PageSpeed API, custom HTTP probes,
technology detection, and revenue impact calculation.
"""

import os
import re
import ssl
import json
import socket
import logging
import time
from datetime import datetime
from urllib.parse import urlparse
from typing import Dict, Any, Optional, Tuple

import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv('/opt/shophosting/.env')

logger = logging.getLogger(__name__)

# Configuration
PAGESPEED_API_KEY = os.getenv('PAGESPEED_API_KEY', '')
PAGESPEED_API_URL = 'https://www.googleapis.com/pagespeedonline/v5/runPagespeed'

# Request timeout in seconds
DEFAULT_TIMEOUT = 30

# Known hosting provider signatures
HOSTING_SIGNATURES = {
    'cloudflare': {
        'headers': ['cf-ray', 'cf-cache-status'],
        'server': ['cloudflare'],
    },
    'aws': {
        'headers': ['x-amz-cf-id', 'x-amz-request-id', 'x-amz-cf-pop'],
        'server': ['amazons3', 'awselb'],
    },
    'google_cloud': {
        'headers': ['x-goog-generation', 'x-goog-storage-class'],
        'server': ['gws', 'gse'],
    },
    'vercel': {
        'headers': ['x-vercel-id', 'x-vercel-cache'],
        'server': ['vercel'],
    },
    'netlify': {
        'headers': ['x-nf-request-id'],
        'server': ['netlify'],
    },
    'shopify': {
        'headers': ['x-shopify-stage', 'x-shopid'],
        'server': ['shopify'],
    },
    'wpengine': {
        'headers': ['x-powered-by'],
        'server': ['wpengine'],
        'powered_by': ['wpengine'],
    },
    'siteground': {
        'headers': ['x-siteground-'],
        'server': ['siteground'],
    },
    'godaddy': {
        'headers': ['x-godaddy'],
        'server': ['godaddy'],
    },
    'bluehost': {
        'server': ['apache'],
        'powered_by': ['bluehost'],
    },
    'kinsta': {
        'headers': ['x-kinsta-cache'],
        'server': ['nginx'],
    },
    'flywheel': {
        'headers': ['x-fw-hash'],
        'server': ['flywheel'],
    },
}

# Technology detection patterns
TECH_PATTERNS = {
    'woocommerce': {
        'paths': ['/wp-content/plugins/woocommerce/', '/wc-api/', '/wp-json/wc/'],
        'cookies': ['woocommerce_cart_hash', 'woocommerce_items_in_cart', 'wp_woocommerce_session'],
        'meta': ['woocommerce', 'generator.*woocommerce'],
        'scripts': ['woocommerce', 'wc-add-to-cart'],
    },
    'magento': {
        'paths': ['/static/frontend/', '/pub/static/', '/media/catalog/', '/skin/frontend/'],
        'cookies': ['mage-cache-storage', 'PHPSESSID', 'form_key', 'mage-cache-sessid'],
        'meta': ['magento', 'generator.*magento'],
        'scripts': ['mage/', 'requirejs-config.js', 'Magento_'],
        'headers': ['x-magento-'],
    },
    'wordpress': {
        'paths': ['/wp-content/', '/wp-includes/', '/wp-admin/'],
        'cookies': ['wordpress_logged_in', 'wp-settings'],
        'meta': ['wordpress', 'generator.*wordpress'],
    },
    'shopify': {
        'paths': ['/cdn.shopify.com/', '/shopify/'],
        'cookies': ['_shopify_s', '_shopify_y', 'cart'],
        'meta': ['shopify'],
        'scripts': ['cdn.shopify.com', 'shopify-'],
    },
    'prestashop': {
        'paths': ['/modules/', '/themes/'],
        'cookies': ['PrestaShop-'],
        'meta': ['prestashop'],
    },
    'opencart': {
        'paths': ['/catalog/view/theme/', '/index.php?route='],
        'cookies': ['OCSESSID'],
    },
}


# =============================================================================
# PageSpeed API Integration
# =============================================================================

def fetch_pagespeed_data(url: str) -> Dict[str, Any]:
    """
    Fetch performance data from Google PageSpeed Insights API.

    Args:
        url: The URL to analyze

    Returns:
        Dict containing performance metrics or error information
    """
    if not PAGESPEED_API_KEY:
        logger.warning("PAGESPEED_API_KEY not configured")
        return {
            'error': 'PageSpeed API key not configured',
            'performance_score': None,
            'metrics': {},
            'recommendations': [],
        }

    try:
        params = {
            'url': url,
            'key': PAGESPEED_API_KEY,
            'strategy': 'mobile',  # Mobile-first
            'category': 'performance',
        }

        logger.info(f"Fetching PageSpeed data for: {url}")
        response = requests.get(
            PAGESPEED_API_URL,
            params=params,
            timeout=60  # PageSpeed can be slow
        )

        if response.status_code == 429:
            logger.warning("PageSpeed API rate limit exceeded")
            return {
                'error': 'API rate limit exceeded',
                'performance_score': None,
                'metrics': {},
                'recommendations': [],
            }

        response.raise_for_status()
        data = response.json()

        # Extract lighthouse results
        lighthouse = data.get('lighthouseResult', {})
        categories = lighthouse.get('categories', {})
        audits = lighthouse.get('audits', {})

        # Performance score (0-100)
        performance_score = None
        if 'performance' in categories:
            performance_score = int(categories['performance'].get('score', 0) * 100)

        # Core Web Vitals
        metrics = {}

        # Largest Contentful Paint (LCP)
        if 'largest-contentful-paint' in audits:
            lcp_audit = audits['largest-contentful-paint']
            metrics['lcp'] = {
                'value_ms': lcp_audit.get('numericValue', 0),
                'display': lcp_audit.get('displayValue', 'N/A'),
                'score': lcp_audit.get('score', 0),
            }

        # First Input Delay (FID) - uses TBT as proxy in lab data
        if 'total-blocking-time' in audits:
            tbt_audit = audits['total-blocking-time']
            metrics['tbt'] = {
                'value_ms': tbt_audit.get('numericValue', 0),
                'display': tbt_audit.get('displayValue', 'N/A'),
                'score': tbt_audit.get('score', 0),
            }

        # Interaction to Next Paint (INP) - newer metric
        if 'interactive' in audits:
            tti_audit = audits['interactive']
            metrics['tti'] = {
                'value_ms': tti_audit.get('numericValue', 0),
                'display': tti_audit.get('displayValue', 'N/A'),
                'score': tti_audit.get('score', 0),
            }

        # Cumulative Layout Shift (CLS)
        if 'cumulative-layout-shift' in audits:
            cls_audit = audits['cumulative-layout-shift']
            metrics['cls'] = {
                'value': cls_audit.get('numericValue', 0),
                'display': cls_audit.get('displayValue', 'N/A'),
                'score': cls_audit.get('score', 0),
            }

        # First Contentful Paint (FCP)
        if 'first-contentful-paint' in audits:
            fcp_audit = audits['first-contentful-paint']
            metrics['fcp'] = {
                'value_ms': fcp_audit.get('numericValue', 0),
                'display': fcp_audit.get('displayValue', 'N/A'),
                'score': fcp_audit.get('score', 0),
            }

        # Speed Index
        if 'speed-index' in audits:
            si_audit = audits['speed-index']
            metrics['speed_index'] = {
                'value_ms': si_audit.get('numericValue', 0),
                'display': si_audit.get('displayValue', 'N/A'),
                'score': si_audit.get('score', 0),
            }

        # Extract recommendations (failed audits)
        recommendations = []
        opportunity_audits = [
            'render-blocking-resources',
            'unminified-css',
            'unminified-javascript',
            'unused-css-rules',
            'unused-javascript',
            'uses-responsive-images',
            'offscreen-images',
            'uses-optimized-images',
            'uses-webp-images',
            'uses-text-compression',
            'uses-long-cache-ttl',
            'dom-size',
            'server-response-time',
            'redirects',
            'uses-rel-preconnect',
            'efficient-animated-content',
            'duplicated-javascript',
            'legacy-javascript',
        ]

        for audit_key in opportunity_audits:
            if audit_key in audits:
                audit = audits[audit_key]
                score = audit.get('score', 1)
                if score is not None and score < 0.9:  # Not passing
                    savings = audit.get('details', {}).get('overallSavingsMs', 0)
                    recommendations.append({
                        'id': audit_key,
                        'title': audit.get('title', audit_key),
                        'description': audit.get('description', ''),
                        'score': score,
                        'savings_ms': savings,
                        'display_value': audit.get('displayValue', ''),
                    })

        # Sort recommendations by potential savings
        recommendations.sort(key=lambda x: x.get('savings_ms', 0), reverse=True)

        # Calculate total load time estimate
        load_time_ms = metrics.get('speed_index', {}).get('value_ms', 0) or \
                       metrics.get('lcp', {}).get('value_ms', 0) or \
                       metrics.get('tti', {}).get('value_ms', 0)

        return {
            'performance_score': performance_score,
            'metrics': metrics,
            'recommendations': recommendations[:10],  # Top 10
            'load_time_ms': int(load_time_ms),
            'fetch_timestamp': datetime.now().isoformat(),
            'strategy': 'mobile',
        }

    except requests.exceptions.Timeout:
        logger.error(f"PageSpeed API timeout for {url}")
        return {
            'error': 'PageSpeed API request timed out',
            'performance_score': None,
            'metrics': {},
            'recommendations': [],
        }
    except requests.exceptions.RequestException as e:
        logger.error(f"PageSpeed API error for {url}: {e}")
        return {
            'error': str(e),
            'performance_score': None,
            'metrics': {},
            'recommendations': [],
        }
    except (KeyError, ValueError, TypeError) as e:
        logger.error(f"Error parsing PageSpeed response for {url}: {e}")
        return {
            'error': f'Error parsing response: {e}',
            'performance_score': None,
            'metrics': {},
            'recommendations': [],
        }


# =============================================================================
# Custom HTTP Probes
# =============================================================================

def measure_ttfb(url: str, timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """
    Measure Time To First Byte (TTFB) for a URL.

    Args:
        url: The URL to measure
        timeout: Request timeout in seconds

    Returns:
        Dict with TTFB measurements
    """
    try:
        start_time = time.time()
        response = requests.get(
            url,
            timeout=timeout,
            stream=True,  # Don't download full content
            allow_redirects=True,
            headers={
                'User-Agent': 'Mozilla/5.0 (compatible; ShopHostingScanner/1.0)'
            }
        )
        ttfb = (time.time() - start_time) * 1000  # Convert to ms

        # Close the connection without reading body
        response.close()

        return {
            'ttfb_ms': int(ttfb),
            'status_code': response.status_code,
            'redirects': len(response.history),
            'final_url': response.url,
        }

    except requests.exceptions.Timeout:
        return {
            'ttfb_ms': timeout * 1000,
            'error': 'Request timed out',
            'status_code': None,
        }
    except requests.exceptions.RequestException as e:
        return {
            'ttfb_ms': None,
            'error': str(e),
            'status_code': None,
        }


def analyze_headers(url: str, timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """
    Analyze HTTP response headers for caching, CDN, and server info.

    Args:
        url: The URL to analyze
        timeout: Request timeout in seconds

    Returns:
        Dict with header analysis
    """
    try:
        response = requests.head(
            url,
            timeout=timeout,
            allow_redirects=True,
            headers={
                'User-Agent': 'Mozilla/5.0 (compatible; ShopHostingScanner/1.0)'
            }
        )
        headers = {k.lower(): v for k, v in response.headers.items()}

        result = {
            'server': headers.get('server', 'Unknown'),
            'powered_by': headers.get('x-powered-by', ''),
            'content_type': headers.get('content-type', ''),
            'caching': {},
            'cdn': {},
            'security': {},
            'compression': {},
        }

        # Caching headers
        result['caching'] = {
            'cache_control': headers.get('cache-control', ''),
            'expires': headers.get('expires', ''),
            'etag': bool(headers.get('etag')),
            'last_modified': bool(headers.get('last-modified')),
            'vary': headers.get('vary', ''),
            'age': headers.get('age', ''),
        }

        # Analyze cache quality
        cache_control = headers.get('cache-control', '').lower()
        if 'no-cache' in cache_control or 'no-store' in cache_control:
            result['caching']['quality'] = 'poor'
            result['caching']['issue'] = 'Caching disabled'
        elif 'max-age' in cache_control:
            # Extract max-age value
            match = re.search(r'max-age=(\d+)', cache_control)
            if match:
                max_age = int(match.group(1))
                if max_age < 3600:
                    result['caching']['quality'] = 'fair'
                    result['caching']['issue'] = 'Short cache duration'
                elif max_age < 86400:
                    result['caching']['quality'] = 'good'
                else:
                    result['caching']['quality'] = 'excellent'
        else:
            result['caching']['quality'] = 'poor'
            result['caching']['issue'] = 'No cache headers'

        # CDN detection
        cdn_detected = None
        cdn_headers_found = []

        for header in headers:
            # Cloudflare
            if header.startswith('cf-'):
                cdn_detected = 'cloudflare'
                cdn_headers_found.append(header)
            # AWS CloudFront
            elif header.startswith('x-amz-cf-'):
                cdn_detected = 'aws_cloudfront'
                cdn_headers_found.append(header)
            # Fastly
            elif header.startswith('x-fastly-'):
                cdn_detected = 'fastly'
                cdn_headers_found.append(header)
            # Akamai
            elif header.startswith('x-akamai-'):
                cdn_detected = 'akamai'
                cdn_headers_found.append(header)
            # KeyCDN
            elif header == 'x-pull':
                cdn_detected = 'keycdn'
                cdn_headers_found.append(header)
            # BunnyCDN
            elif header.startswith('x-bunnycdn-'):
                cdn_detected = 'bunnycdn'
                cdn_headers_found.append(header)

        result['cdn'] = {
            'detected': cdn_detected,
            'headers_found': cdn_headers_found,
            'using_cdn': cdn_detected is not None,
        }

        # Security headers
        result['security'] = {
            'hsts': bool(headers.get('strict-transport-security')),
            'csp': bool(headers.get('content-security-policy')),
            'x_frame_options': headers.get('x-frame-options', ''),
            'x_content_type_options': headers.get('x-content-type-options', ''),
            'referrer_policy': headers.get('referrer-policy', ''),
        }

        # Compression
        result['compression'] = {
            'content_encoding': headers.get('content-encoding', ''),
            'using_compression': 'gzip' in headers.get('content-encoding', '') or
                                'br' in headers.get('content-encoding', ''),
        }

        # Store all headers for reference
        result['all_headers'] = dict(headers)

        return result

    except requests.exceptions.RequestException as e:
        return {
            'error': str(e),
            'server': 'Unknown',
            'caching': {'quality': 'unknown'},
            'cdn': {'using_cdn': False},
            'security': {},
            'compression': {},
        }


def check_ssl_certificate(url: str) -> Dict[str, Any]:
    """
    Check SSL certificate details for a URL.

    Args:
        url: The URL to check

    Returns:
        Dict with SSL certificate information
    """
    parsed = urlparse(url)
    hostname = parsed.netloc or parsed.path.split('/')[0]

    # Remove port if present
    if ':' in hostname:
        hostname = hostname.split(':')[0]

    try:
        context = ssl.create_default_context()
        with socket.create_connection((hostname, 443), timeout=10) as sock:
            with context.wrap_socket(sock, server_hostname=hostname) as ssock:
                cert = ssock.getpeercert()

                # Parse certificate details
                subject = dict(x[0] for x in cert.get('subject', []))
                issuer = dict(x[0] for x in cert.get('issuer', []))

                # Parse dates
                not_before = cert.get('notBefore', '')
                not_after = cert.get('notAfter', '')

                # Calculate days until expiration
                days_until_expiry = None
                if not_after:
                    try:
                        expiry_date = datetime.strptime(not_after, '%b %d %H:%M:%S %Y %Z')
                        days_until_expiry = (expiry_date - datetime.now()).days
                    except ValueError:
                        pass

                # Determine certificate type
                cert_type = 'dv'  # Domain Validation (default)
                org = subject.get('organizationName', '')
                if org:
                    cert_type = 'ov'  # Organization Validation
                    if issuer.get('organizationName', '') in ['DigiCert', 'Comodo', 'Sectigo'] and \
                       'EV' in str(cert.get('subject', '')):
                        cert_type = 'ev'  # Extended Validation

                return {
                    'valid': True,
                    'hostname': hostname,
                    'issuer': issuer.get('organizationName', issuer.get('commonName', 'Unknown')),
                    'subject': subject.get('commonName', ''),
                    'expires': not_after,
                    'days_until_expiry': days_until_expiry,
                    'cert_type': cert_type,
                    'san': cert.get('subjectAltName', []),
                    'protocol': ssock.version(),
                }

    except ssl.SSLError as e:
        return {
            'valid': False,
            'error': f'SSL error: {e}',
            'hostname': hostname,
        }
    except socket.timeout:
        return {
            'valid': False,
            'error': 'Connection timed out',
            'hostname': hostname,
        }
    except socket.error as e:
        return {
            'valid': False,
            'error': f'Connection error: {e}',
            'hostname': hostname,
        }
    except Exception as e:
        return {
            'valid': False,
            'error': str(e),
            'hostname': hostname,
        }


def detect_technology(url: str, timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """
    Detect e-commerce platform and technologies used by the site.

    Args:
        url: The URL to analyze
        timeout: Request timeout in seconds

    Returns:
        Dict with detected technologies
    """
    detected = {
        'platform': 'unknown',
        'platform_confidence': 0,
        'cms': None,
        'technologies': [],
        'evidence': [],
    }

    try:
        response = requests.get(
            url,
            timeout=timeout,
            allow_redirects=True,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
        )

        html = response.text.lower()
        headers = {k.lower(): v.lower() for k, v in response.headers.items()}
        cookies = response.cookies.get_dict()

        scores = {}
        evidence = {}

        for tech, patterns in TECH_PATTERNS.items():
            scores[tech] = 0
            evidence[tech] = []

            # Check paths in HTML
            for path in patterns.get('paths', []):
                if path.lower() in html:
                    scores[tech] += 30
                    evidence[tech].append(f'Path: {path}')

            # Check cookies
            for cookie_pattern in patterns.get('cookies', []):
                for cookie_name in cookies:
                    if cookie_pattern.lower() in cookie_name.lower():
                        scores[tech] += 25
                        evidence[tech].append(f'Cookie: {cookie_name}')

            # Check meta tags
            for meta_pattern in patterns.get('meta', []):
                if re.search(meta_pattern, html):
                    scores[tech] += 20
                    evidence[tech].append(f'Meta: {meta_pattern}')

            # Check scripts
            for script_pattern in patterns.get('scripts', []):
                if script_pattern.lower() in html:
                    scores[tech] += 15
                    evidence[tech].append(f'Script: {script_pattern}')

            # Check headers
            for header_pattern in patterns.get('headers', []):
                for header in headers:
                    if header_pattern in header:
                        scores[tech] += 25
                        evidence[tech].append(f'Header: {header}')

        # Find the highest scoring technology
        if scores:
            best_tech = max(scores, key=scores.get)
            best_score = scores[best_tech]

            if best_score >= 25:  # Minimum confidence threshold
                detected['platform'] = best_tech
                detected['platform_confidence'] = min(best_score, 100)
                detected['evidence'] = evidence[best_tech]

        # Detect additional technologies
        techs = []

        # jQuery
        if 'jquery' in html:
            techs.append('jquery')

        # React
        if 'react' in html or '__react' in html:
            techs.append('react')

        # Vue.js
        if 'vue' in html or '__vue' in html:
            techs.append('vue')

        # Bootstrap
        if 'bootstrap' in html:
            techs.append('bootstrap')

        # Google Analytics
        if 'google-analytics' in html or 'gtag' in html or 'ga(' in html:
            techs.append('google_analytics')

        # Google Tag Manager
        if 'googletagmanager' in html:
            techs.append('google_tag_manager')

        # Facebook Pixel
        if 'fbq(' in html or 'facebook' in html and 'pixel' in html:
            techs.append('facebook_pixel')

        # PHP
        if 'x-powered-by' in headers and 'php' in headers.get('x-powered-by', ''):
            techs.append('php')

        detected['technologies'] = techs

        # Determine CMS
        if detected['platform'] in ['woocommerce', 'wordpress']:
            detected['cms'] = 'wordpress'
        elif detected['platform'] in ['magento']:
            detected['cms'] = 'magento'
        elif detected['platform'] in ['shopify']:
            detected['cms'] = 'shopify'
        elif detected['platform'] in ['prestashop']:
            detected['cms'] = 'prestashop'

        return detected

    except requests.exceptions.RequestException as e:
        detected['error'] = str(e)
        return detected


def fingerprint_hosting(headers_data: Dict[str, Any], ssl_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Attempt to identify the hosting provider from collected data.

    Args:
        headers_data: Results from analyze_headers()
        ssl_data: Results from check_ssl_certificate()

    Returns:
        Dict with hosting provider information
    """
    result = {
        'provider': 'unknown',
        'confidence': 0,
        'evidence': [],
    }

    if 'error' in headers_data:
        return result

    server = headers_data.get('server', '').lower()
    powered_by = headers_data.get('powered_by', '').lower()
    all_headers = headers_data.get('all_headers', {})

    scores = {}
    evidence = {}

    for provider, signatures in HOSTING_SIGNATURES.items():
        scores[provider] = 0
        evidence[provider] = []

        # Check server header
        for sig in signatures.get('server', []):
            if sig.lower() in server:
                scores[provider] += 40
                evidence[provider].append(f'Server: {server}')

        # Check x-powered-by header
        for sig in signatures.get('powered_by', []):
            if sig.lower() in powered_by:
                scores[provider] += 35
                evidence[provider].append(f'X-Powered-By: {powered_by}')

        # Check custom headers
        for header_prefix in signatures.get('headers', []):
            for header in all_headers:
                if header.lower().startswith(header_prefix.lower()):
                    scores[provider] += 30
                    evidence[provider].append(f'Header: {header}')

    # Check SSL certificate issuer for hints
    if ssl_data.get('valid'):
        issuer = ssl_data.get('issuer', '').lower()
        if 'lets encrypt' in issuer:
            # Let's Encrypt is common, doesn't strongly indicate hosting
            pass
        elif 'cloudflare' in issuer:
            scores['cloudflare'] = scores.get('cloudflare', 0) + 20
            evidence.setdefault('cloudflare', []).append(f'SSL Issuer: {issuer}')

    # Find best match
    if scores:
        best_provider = max(scores, key=scores.get)
        best_score = scores[best_provider]

        if best_score >= 30:  # Minimum threshold
            result['provider'] = best_provider
            result['confidence'] = min(best_score, 100)
            result['evidence'] = evidence[best_provider]

    return result


# =============================================================================
# Revenue Impact Calculation
# =============================================================================

def calculate_revenue_impact(
    load_time_seconds: float,
    performance_score: Optional[int] = None,
    monthly_revenue: Optional[float] = None
) -> Dict[str, Any]:
    """
    Calculate estimated revenue impact based on site performance.

    Formula: Every 1 second of load time = ~7% conversion drop
    Based on industry research (Google, Akamai, etc.)

    Args:
        load_time_seconds: Current page load time in seconds
        performance_score: PageSpeed performance score (0-100)
        monthly_revenue: Estimated monthly revenue (if provided by user)

    Returns:
        Dict with revenue impact estimates
    """
    # Industry benchmarks
    OPTIMAL_LOAD_TIME = 2.0  # seconds (target)
    CONVERSION_DROP_PER_SECOND = 0.07  # 7% per second
    BOUNCE_INCREASE_PER_SECOND = 0.10  # 10% bounce rate increase per second

    # Average e-commerce metrics (for estimation if not provided)
    AVG_CONVERSION_RATE = 0.025  # 2.5% baseline
    AVG_ORDER_VALUE = 85  # $85 average order
    AVG_MONTHLY_VISITORS = 5000  # Estimated for small-medium stores

    result = {
        'load_time_seconds': round(load_time_seconds, 2),
        'optimal_load_time': OPTIMAL_LOAD_TIME,
        'seconds_over_optimal': 0,
        'conversion_impact': {},
        'bounce_impact': {},
        'revenue_impact': {},
        'improvement_potential': {},
    }

    # Calculate time over optimal
    excess_time = max(0, load_time_seconds - OPTIMAL_LOAD_TIME)
    result['seconds_over_optimal'] = round(excess_time, 2)

    if excess_time <= 0:
        result['conversion_impact'] = {
            'message': 'Load time is within optimal range',
            'drop_percentage': 0,
        }
        result['revenue_impact'] = {
            'status': 'optimal',
            'monthly_loss_estimate': 0,
        }
        return result

    # Calculate conversion drop
    conversion_drop = excess_time * CONVERSION_DROP_PER_SECOND
    conversion_drop_capped = min(conversion_drop, 0.50)  # Cap at 50%

    result['conversion_impact'] = {
        'drop_percentage': round(conversion_drop_capped * 100, 1),
        'explanation': f'{round(conversion_drop_capped * 100, 1)}% fewer conversions due to {round(excess_time, 1)}s extra load time',
    }

    # Calculate bounce rate increase
    bounce_increase = excess_time * BOUNCE_INCREASE_PER_SECOND
    bounce_increase_capped = min(bounce_increase, 0.40)  # Cap at 40%

    result['bounce_impact'] = {
        'increase_percentage': round(bounce_increase_capped * 100, 1),
        'explanation': f'{round(bounce_increase_capped * 100, 1)}% more visitors leaving before engaging',
    }

    # Calculate revenue impact
    if monthly_revenue:
        # Use provided revenue
        monthly_loss = monthly_revenue * conversion_drop_capped
        result['revenue_impact'] = {
            'monthly_loss_estimate': round(monthly_loss, 2),
            'annual_loss_estimate': round(monthly_loss * 12, 2),
            'calculation_basis': 'provided_revenue',
        }
    else:
        # Estimate based on averages
        # Assume current conversion rate is already impacted
        baseline_conversions = AVG_MONTHLY_VISITORS * AVG_CONVERSION_RATE
        lost_conversions = baseline_conversions * conversion_drop_capped
        monthly_loss = lost_conversions * AVG_ORDER_VALUE

        # Apply a conservative multiplier based on performance score
        if performance_score is not None:
            if performance_score < 30:
                monthly_loss *= 1.5  # Very slow sites lose more
            elif performance_score < 50:
                monthly_loss *= 1.2

        result['revenue_impact'] = {
            'monthly_loss_estimate': round(monthly_loss, 2),
            'annual_loss_estimate': round(monthly_loss * 12, 2),
            'calculation_basis': 'industry_estimates',
            'assumptions': {
                'avg_conversion_rate': f'{AVG_CONVERSION_RATE * 100}%',
                'avg_order_value': f'${AVG_ORDER_VALUE}',
                'estimated_monthly_visitors': AVG_MONTHLY_VISITORS,
            }
        }

    # Calculate improvement potential
    if load_time_seconds > OPTIMAL_LOAD_TIME:
        potential_recovery = result['revenue_impact']['monthly_loss_estimate']

        # Be conservative - say they can recover 70-80% with optimization
        achievable_recovery = potential_recovery * 0.75

        result['improvement_potential'] = {
            'monthly_recovery': round(achievable_recovery, 2),
            'annual_recovery': round(achievable_recovery * 12, 2),
            'target_load_time': OPTIMAL_LOAD_TIME,
            'message': f'Optimizing to {OPTIMAL_LOAD_TIME}s load time could recover ~${round(achievable_recovery, 0)}/month',
        }

    return result


# =============================================================================
# Main Scanner Function
# =============================================================================

def run_scan(url: str, monthly_revenue: Optional[float] = None) -> Dict[str, Any]:
    """
    Run a comprehensive site scan including PageSpeed API and custom HTTP probes.

    This is the main function to be called as a background job.

    Args:
        url: The URL to scan
        monthly_revenue: Optional monthly revenue for more accurate impact calculation

    Returns:
        Dict containing all scan results, suitable for storing in SiteScan model
    """
    logger.info(f"Starting scan for: {url}")
    start_time = time.time()

    # Normalize URL
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url

    result = {
        'url': url,
        'scan_timestamp': datetime.now().isoformat(),
        'status': 'completed',
        'errors': [],
    }

    # 1. Measure TTFB (do this first as it's quick)
    logger.info(f"Measuring TTFB for: {url}")
    ttfb_data = measure_ttfb(url)
    result['ttfb'] = ttfb_data
    result['ttfb_ms'] = ttfb_data.get('ttfb_ms')

    if ttfb_data.get('error'):
        result['errors'].append(f"TTFB: {ttfb_data['error']}")

    # 2. Analyze headers
    logger.info(f"Analyzing headers for: {url}")
    headers_data = analyze_headers(url)
    result['headers'] = headers_data

    if headers_data.get('error'):
        result['errors'].append(f"Headers: {headers_data['error']}")

    # 3. Check SSL certificate
    logger.info(f"Checking SSL for: {url}")
    ssl_data = check_ssl_certificate(url)
    result['ssl'] = ssl_data

    if not ssl_data.get('valid'):
        result['errors'].append(f"SSL: {ssl_data.get('error', 'Invalid certificate')}")

    # 4. Detect technology
    logger.info(f"Detecting technology for: {url}")
    tech_data = detect_technology(url)
    result['technology'] = tech_data

    if tech_data.get('error'):
        result['errors'].append(f"Technology: {tech_data['error']}")

    # 5. Fingerprint hosting provider
    logger.info(f"Fingerprinting hosting for: {url}")
    hosting_data = fingerprint_hosting(headers_data, ssl_data)
    result['hosting'] = hosting_data

    # 6. Fetch PageSpeed data (this is the slowest operation)
    logger.info(f"Fetching PageSpeed data for: {url}")
    pagespeed_data = fetch_pagespeed_data(url)
    result['pagespeed'] = pagespeed_data

    if pagespeed_data.get('error'):
        result['errors'].append(f"PageSpeed: {pagespeed_data['error']}")

    # Extract key metrics for the model
    result['performance_score'] = pagespeed_data.get('performance_score')
    result['load_time_ms'] = pagespeed_data.get('load_time_ms') or ttfb_data.get('ttfb_ms')

    # 7. Calculate revenue impact
    load_time_seconds = (result['load_time_ms'] or 5000) / 1000  # Default to 5s if unknown

    revenue_impact = calculate_revenue_impact(
        load_time_seconds=load_time_seconds,
        performance_score=result['performance_score'],
        monthly_revenue=monthly_revenue
    )
    result['revenue_impact'] = revenue_impact
    result['estimated_revenue_loss'] = revenue_impact.get('revenue_impact', {}).get('monthly_loss_estimate', 0)

    # Calculate total scan time
    result['scan_duration_seconds'] = round(time.time() - start_time, 2)

    # Set status based on errors
    if result['errors']:
        if result['performance_score'] is None and result['ttfb_ms'] is None:
            result['status'] = 'failed'
        else:
            result['status'] = 'partial'

    logger.info(f"Scan completed for {url} in {result['scan_duration_seconds']}s - Score: {result['performance_score']}")

    # Prepare data for model storage
    result['pagespeed_data_json'] = json.dumps(pagespeed_data)
    result['custom_probe_data_json'] = json.dumps({
        'ttfb': ttfb_data,
        'headers': {k: v for k, v in headers_data.items() if k != 'all_headers'},  # Exclude raw headers
        'ssl': ssl_data,
        'technology': tech_data,
        'hosting': hosting_data,
        'revenue_impact': revenue_impact,
    })

    return result


# =============================================================================
# CLI Testing Interface
# =============================================================================

if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print("Usage: python scanner.py <url>")
        print("Example: python scanner.py https://example.com")
        sys.exit(1)

    test_url = sys.argv[1]
    print(f"\nScanning: {test_url}")
    print("=" * 60)

    results = run_scan(test_url)

    print(f"\nResults for: {results['url']}")
    print("-" * 60)
    print(f"Status: {results['status']}")
    print(f"Scan Duration: {results['scan_duration_seconds']}s")
    print(f"\nPerformance Score: {results['performance_score']}/100")
    print(f"Load Time: {results['load_time_ms']}ms")
    print(f"TTFB: {results['ttfb_ms']}ms")

    print(f"\nTechnology: {results['technology']['platform']}")
    print(f"  Confidence: {results['technology']['platform_confidence']}%")

    print(f"\nHosting: {results['hosting']['provider']}")
    print(f"  Confidence: {results['hosting']['confidence']}%")

    print(f"\nSSL: {'Valid' if results['ssl'].get('valid') else 'Invalid'}")
    if results['ssl'].get('valid'):
        print(f"  Issuer: {results['ssl'].get('issuer')}")
        print(f"  Expires in: {results['ssl'].get('days_until_expiry')} days")

    print(f"\nCDN: {results['headers'].get('cdn', {}).get('detected', 'None')}")
    print(f"Caching Quality: {results['headers'].get('caching', {}).get('quality', 'unknown')}")

    print(f"\nRevenue Impact:")
    ri = results['revenue_impact']
    print(f"  Load time over optimal: {ri.get('seconds_over_optimal', 0)}s")
    print(f"  Conversion drop: {ri.get('conversion_impact', {}).get('drop_percentage', 0)}%")
    print(f"  Estimated monthly loss: ${ri.get('revenue_impact', {}).get('monthly_loss_estimate', 0):.2f}")

    if results['errors']:
        print(f"\nErrors encountered:")
        for error in results['errors']:
            print(f"  - {error}")

    print("\n" + "=" * 60)
