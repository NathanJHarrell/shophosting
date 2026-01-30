"""
ShopHosting.io - Flask Web Application
Multi-tenant Docker hosting platform for WooCommerce and Magento
"""

import os
import sys
import re
import logging
from datetime import datetime
from functools import wraps

import uuid
import mimetypes
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session, send_file, abort, g
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_wtf import FlaskForm
from flask_wtf.csrf import CSRFProtect, CSRFError
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_talisman import Talisman
from wtforms import StringField, PasswordField, SelectField, SubmitField, HiddenField, TextAreaField
from werkzeug.utils import secure_filename
from wtforms.validators import DataRequired, Email, Length, EqualTo, ValidationError
from dotenv import load_dotenv
import redis
import time

# Add provisioning module to path
sys.path.insert(0, '/opt/shophosting/provisioning')

from models import Customer, PortManager, PricingPlan, Subscription, Invoice, init_db_pool, get_db_connection
from models import Ticket, TicketMessage, TicketAttachment, TicketCategory, ConsultationAppointment
from models import StagingEnvironment, StagingPortManager
from models import CustomerBackupJob
from models import Customer2FASettings, CustomerLoginHistory, CustomerVerificationToken
from models import CustomerNotificationSettings, CustomerApiKey, CustomerWebhook
from models import CustomerDataExport, CustomerDeletionRequest
import pyotp
import hashlib
import secrets
import json
import base64
import io
from enqueue_provisioning import ProvisioningQueue
from stripe_integration import init_stripe, create_checkout_session, process_webhook, create_portal_session
from stripe_integration.checkout import get_checkout_session
from email_utils import send_contact_notification, send_consultation_confirmation, send_consultation_notification_to_sales

# Load environment variables
load_dotenv('/opt/shophosting/.env')

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/opt/shophosting/logs/webapp.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Security logger for audit trail
security_logger = logging.getLogger('security')
security_handler = logging.FileHandler('/opt/shophosting/logs/security.log')
security_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
security_logger.addHandler(security_handler)
security_logger.setLevel(logging.INFO)


# =============================================================================
# Configuration Validation - Fail-fast on missing secrets
# =============================================================================

def validate_required_config():
    """Validate that required configuration is present. Fail fast if missing."""
    required_vars = {
        'SECRET_KEY': 'Flask secret key for session security',
        'DB_PASSWORD': 'Database password',
    }

    missing = []
    insecure = []

    for var, description in required_vars.items():
        value = os.getenv(var)
        if not value:
            missing.append(f"  - {var}: {description}")
        elif var == 'SECRET_KEY' and 'change-this' in value.lower():
            insecure.append(f"  - {var}: Using insecure default value")

    if missing or insecure:
        error_msg = "\n\nCRITICAL CONFIGURATION ERROR\n" + "=" * 40 + "\n"
        if missing:
            error_msg += "Missing required environment variables:\n" + "\n".join(missing) + "\n"
        if insecure:
            error_msg += "Insecure configuration detected:\n" + "\n".join(insecure) + "\n"
        error_msg += "\nPlease configure these in /opt/shophosting/.env\n"

        # In production, fail fast. In development, warn but continue.
        if os.getenv('FLASK_ENV') == 'production' or os.getenv('FLASK_DEBUG', '').lower() != 'true':
            logger.critical(error_msg)
            raise RuntimeError(error_msg)
        else:
            logger.warning(error_msg)


# Validate configuration on startup
validate_required_config()


# =============================================================================
# Initialize Flask app with secure configuration
# =============================================================================

app = Flask(__name__)

# Secret key - must be set in environment
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')
if not app.config['SECRET_KEY']:
    # Only for development - production validated above
    app.config['SECRET_KEY'] = os.urandom(32).hex()
    logger.warning("Using randomly generated SECRET_KEY - sessions will not persist across restarts")

app.config['WTF_CSRF_ENABLED'] = True

# Request size limits to prevent DoS attacks
# 50MB max for regular requests, file uploads handled separately
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB

# Session security configuration
app.config['SESSION_COOKIE_SECURE'] = os.getenv('FLASK_ENV') == 'production'  # HTTPS only in production
app.config['SESSION_COOKIE_HTTPONLY'] = True  # Prevent JavaScript access
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  # CSRF protection
app.config['PERMANENT_SESSION_LIFETIME'] = 86400  # 24 hours


# =============================================================================
# Security Headers with Flask-Talisman
# =============================================================================

# Content Security Policy - balanced for security while allowing Bootstrap/jQuery
csp = {
    'default-src': "'self'",
    'script-src': [
        "'self'",
        "'unsafe-inline'",  # Required for some Bootstrap functionality
        "https://js.stripe.com",
        "https://cdn.jsdelivr.net",
        "https://cdn.tiny.cloud",  # TinyMCE for CMS editor
    ],
    'style-src': [
        "'self'",
        "'unsafe-inline'",  # Required for Bootstrap
        "https://cdn.jsdelivr.net",
        "https://fonts.googleapis.com",
    ],
    'font-src': [
        "'self'",
        "https://fonts.gstatic.com",
        "https://cdn.jsdelivr.net",
    ],
    'img-src': [
        "'self'",
        "data:",
        "https:",
    ],
    'frame-src': [
        "'self'",
        "https://js.stripe.com",
        "https://hooks.stripe.com",
        "https://shophosting.io",
    ],
    'connect-src': [
        "'self'",
        "https://api.stripe.com",
    ],
}

# Initialize Talisman with security headers
# Only force HTTPS in production
talisman = Talisman(
    app,
    force_https=os.getenv('FLASK_ENV') == 'production',
    strict_transport_security=True,
    strict_transport_security_max_age=31536000,  # 1 year
    strict_transport_security_include_subdomains=True,
    content_security_policy=csp,
    content_security_policy_nonce_in=['script-src'],
    referrer_policy='strict-origin-when-cross-origin',
    feature_policy={
        'geolocation': "'none'",
        'camera': "'none'",
        'microphone': "'none'",
    }
)


# =============================================================================
# Rate Limiting with Flask-Limiter
# =============================================================================

def get_real_ip():
    """Get real client IP, handling reverse proxy"""
    # Check X-Forwarded-For header (set by nginx)
    forwarded_for = request.headers.get('X-Forwarded-For')
    if forwarded_for:
        # Take the first IP (original client)
        return forwarded_for.split(',')[0].strip()
    return request.remote_addr


def get_rate_limit_key():
    """Get rate limit key - use user ID if authenticated, otherwise IP"""
    if current_user and current_user.is_authenticated:
        return f"user:{current_user.id}"
    return get_real_ip()


# Configure Redis storage for rate limiting (shared across workers)
redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379/1')

limiter = Limiter(
    key_func=get_rate_limit_key,
    app=app,
    storage_uri=redis_url,
    storage_options={"socket_connect_timeout": 5},
    default_limits=["200 per day", "50 per hour"],  # Global defaults
    strategy="fixed-window",
)


@app.errorhandler(429)
def ratelimit_handler(e):
    """Handle rate limit exceeded"""
    security_logger.warning(
        f"Rate limit exceeded: IP={request.remote_addr} "
        f"endpoint={request.endpoint} "
        f"user_agent={request.user_agent.string[:100]}"
    )
    # Return JSON for API endpoints or JSON requests
    if request.is_json or request.path.startswith('/api/'):
        return jsonify({
            'error': 'Rate limit exceeded',
            'message': 'Too many requests. Please try again later.',
            'retry_after': e.description
        }), 429
    flash('Too many requests. Please slow down and try again.', 'error')
    return redirect(request.referrer or url_for('index')), 429


# =============================================================================
# Request Logging for Security Forensics
# =============================================================================

# Session idle timeout (30 minutes)
SESSION_IDLE_TIMEOUT = int(os.getenv('SESSION_IDLE_TIMEOUT', '1800'))  # 30 minutes default


@app.before_request
def check_session_timeout():
    """Check for session idle timeout and enforce re-authentication"""
    # Skip for static files and public endpoints
    if request.endpoint in ['static', 'health_check', 'readiness_check', None]:
        return

    # Check for customer session timeout
    if current_user.is_authenticated:
        last_activity = session.get('last_activity')
        if last_activity:
            idle_time = time.time() - last_activity
            if idle_time > SESSION_IDLE_TIMEOUT:
                security_logger.info(
                    f"SESSION_TIMEOUT: user={current_user.id} email={current_user.email} "
                    f"idle_time={idle_time:.0f}s IP={request.remote_addr}"
                )
                logout_user()
                session.clear()
                flash('Your session has expired due to inactivity. Please log in again.', 'info')
                return redirect(url_for('login'))
        session['last_activity'] = time.time()

    # Check for admin session timeout
    admin_id = session.get('admin_user_id')
    if admin_id:
        last_admin_activity = session.get('admin_last_activity')
        if last_admin_activity:
            idle_time = time.time() - last_admin_activity
            if idle_time > SESSION_IDLE_TIMEOUT:
                security_logger.info(
                    f"ADMIN_SESSION_TIMEOUT: admin_id={admin_id} "
                    f"idle_time={idle_time:.0f}s IP={request.remote_addr}"
                )
                session.pop('admin_user_id', None)
                session.pop('admin_user_name', None)
                session.pop('admin_user_role', None)
                session.pop('admin_last_activity', None)
                flash('Your admin session has expired due to inactivity. Please log in again.', 'info')
                return redirect(url_for('admin.login'))
        session['admin_last_activity'] = time.time()


@app.before_request
def log_request_info():
    """Log request information for security forensics on public endpoints"""
    g.request_start_time = time.time()

    # Log authentication attempts and sensitive endpoints
    sensitive_endpoints = ['login', 'signup', 'stripe_webhook', 'api_backup', 'api_backup_restore']

    if request.endpoint in sensitive_endpoints:
        security_logger.info(
            f"REQUEST: {request.method} {request.path} "
            f"IP={request.remote_addr} "
            f"user_agent={request.user_agent.string[:100]} "
            f"referrer={request.referrer or '-'}"
        )


@app.after_request
def log_response_info(response):
    """Log response information for failed requests"""
    if hasattr(g, 'request_start_time'):
        elapsed = time.time() - g.request_start_time

        # Log failed authentication attempts
        if response.status_code in [401, 403] or (
            request.endpoint in ['login', 'signup'] and response.status_code >= 400
        ):
            security_logger.warning(
                f"FAILED REQUEST: {request.method} {request.path} "
                f"status={response.status_code} "
                f"IP={request.remote_addr} "
                f"elapsed={elapsed:.3f}s"
            )

    return response


# =============================================================================
# Initialize Extensions
# =============================================================================

csrf = CSRFProtect(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'

# Initialize database pool
init_db_pool()

# Initialize Stripe
init_stripe()

# Register admin blueprint
from admin import admin_bp
app.register_blueprint(admin_bp, url_prefix='/admin')

# Register metrics blueprint for Prometheus
from metrics import metrics_bp
app.register_blueprint(metrics_bp)

# Register container metrics blueprint
from container_metrics import container_metrics_bp
app.register_blueprint(container_metrics_bp)

# Register status blueprint for public status page
from status import status_bp
app.register_blueprint(status_bp, url_prefix='/status')

# Apply rate limiting to admin login (stricter than customer login)
# Admin accounts are high-value targets, so we limit more aggressively
limiter.limit("3 per minute")(app.view_functions['admin.login'])
limiter.limit("10 per hour")(app.view_functions['admin.login'])


@login_manager.user_loader
def load_user(user_id):
    """Load user for Flask-Login"""
    return Customer.get_by_id(int(user_id))


# =============================================================================
# Custom Validators
# =============================================================================

def validate_domain(form, field):
    """Validate domain format"""
    domain = field.data.lower().strip()
    # Basic domain validation
    pattern = r'^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z]{2,})+$'
    if not re.match(pattern, domain):
        raise ValidationError('Please enter a valid domain name (e.g., mystore.com)')
    if Customer.domain_exists(domain):
        raise ValidationError('This domain is already registered')


def validate_email_unique(form, field):
    """Check if email is already registered"""
    if Customer.email_exists(field.data):
        raise ValidationError('This email is already registered')


# =============================================================================
# Forms
# =============================================================================

class SignupForm(FlaskForm):
    """Customer signup form"""
    email = StringField('Email', validators=[
        DataRequired(),
        Email(),
        Length(max=255),
        validate_email_unique
    ])
    password = PasswordField('Password', validators=[
        DataRequired(),
        Length(min=8, message='Password must be at least 8 characters')
    ])
    confirm_password = PasswordField('Confirm Password', validators=[
        DataRequired(),
        EqualTo('password', message='Passwords must match')
    ])
    company_name = StringField('Company Name', validators=[
        DataRequired(),
        Length(min=2, max=255)
    ])
    domain = StringField('Domain', validators=[
        DataRequired(),
        Length(min=4, max=255),
        validate_domain
    ])
    platform = SelectField('Platform', choices=[
        ('woocommerce', 'WooCommerce (WordPress)'),
        ('magento', 'Magento')
    ], validators=[DataRequired()])
    plan_slug = HiddenField('Plan')
    submit = SubmitField('Continue to Payment')


class LoginForm(FlaskForm):
    """Customer login form"""
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Login')


class CreateTicketForm(FlaskForm):
    """Create support ticket form"""
    category = SelectField('Category', coerce=int, validators=[DataRequired()])
    subject = StringField('Subject', validators=[
        DataRequired(),
        Length(min=5, max=255, message='Subject must be between 5 and 255 characters')
    ])
    message = TextAreaField('Description', validators=[
        DataRequired(),
        Length(min=20, message='Please provide more detail (at least 20 characters)')
    ])
    submit = SubmitField('Create Ticket')


class TicketReplyForm(FlaskForm):
    """Reply to ticket form"""
    message = TextAreaField('Message', validators=[
        DataRequired(),
        Length(min=2, message='Message is too short')
    ])
    submit = SubmitField('Send Reply')


# Ticket attachment upload path
TICKET_UPLOAD_PATH = '/var/customers/tickets'


# =============================================================================
# Routes - Public
# =============================================================================

@app.route('/')
def index():
    """Landing page"""
    return render_template('index.html')


@app.route('/pricing')
def pricing():
    """Pricing page - display all plans"""
    plans = PricingPlan.get_all_active()
    return render_template('pricing.html', plans=plans)


@app.route('/features')
def features():
    """Features page"""
    return render_template('features.html')


@app.route('/about')
def about():
    """About us page"""
    return render_template('about.html')


@app.route('/contact')
def contact():
    """Contact us page"""
    return render_template('contact.html')


@app.route('/contact', methods=['POST'])
@limiter.limit("5 per hour", error_message="Too many contact submissions. Please try again later.")
def contact_submit():
    """Handle contact form submission"""
    name = request.form.get('name')
    email = request.form.get('email')
    subject = request.form.get('subject')
    website = request.form.get('website', '')
    message = request.form.get('message')

    # Log the contact form submission
    logger.info(f"Contact form submission: {name} ({email}) - Subject: {subject}")

    # Send email notification to support team
    success, msg = send_contact_notification(name, email, subject, website, message)
    if not success:
        logger.warning(f"Failed to send contact notification email: {msg}")

    flash('Thanks for reaching out! We\'ll get back to you within one business day.', 'success')
    return redirect(url_for('contact'))


@app.route('/api/health')
def api_health():
    """Health check endpoint for status page monitoring"""
    return jsonify({'status': 'ok', 'service': 'shophosting-api'})


@app.route('/api/schedule-consultation', methods=['POST'])
@limiter.limit("5 per hour", error_message="Too many consultation requests. Please try again later.")
def schedule_consultation():
    """Handle consultation scheduling form submission"""
    data = request.get_json()

    first_name = data.get('first_name')
    last_name = data.get('last_name')
    email = data.get('email')
    phone = data.get('phone')
    date = data.get('date')
    time = data.get('time')

    # Validate required fields
    if not all([first_name, last_name, email, phone, date, time]):
        return jsonify({'success': False, 'message': 'All fields are required.'}), 400

    # Save to database
    try:
        appointment = ConsultationAppointment(
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
            scheduled_date=date,
            scheduled_time=time,
            timezone='EST',
            status='pending'
        )
        appointment.save()

        # Log the consultation request
        logger.info(f"Consultation scheduled (ID: {appointment.id}): {first_name} {last_name} ({email}, {phone}) - {date} at {time} EST")

        # Send confirmation email to prospect
        confirm_success, confirm_msg = send_consultation_confirmation(appointment)
        if not confirm_success:
            logger.warning(f"Failed to send consultation confirmation email: {confirm_msg}")

        # Send notification to sales team
        notify_success, notify_msg = send_consultation_notification_to_sales(appointment)
        if not notify_success:
            logger.warning(f"Failed to send sales team notification: {notify_msg}")

        return jsonify({
            'success': True,
            'message': 'Consultation scheduled successfully.',
            'data': {
                'id': appointment.id,
                'name': f'{first_name} {last_name}',
                'email': email,
                'date': date,
                'time': time
            }
        })
    except Exception as e:
        logger.error(f"Error scheduling consultation: {str(e)}")
        return jsonify({'success': False, 'message': 'An error occurred. Please try again.'}), 500


@app.route('/signup', methods=['GET', 'POST'])
@app.route('/signup/<plan_slug>', methods=['GET', 'POST'])
@limiter.limit("30 per hour", error_message="Too many signup attempts. Please try again later.")
def signup(plan_slug=None):
    """Customer signup page with payment integration"""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    # Get plan from URL or query param
    plan_slug = plan_slug or request.args.get('plan')
    plan = None
    if plan_slug:
        plan = PricingPlan.get_by_slug(plan_slug)

    # Redirect to pricing if no valid plan selected
    if not plan:
        flash('Please select a plan to continue.', 'info')
        return redirect(url_for('pricing'))

    form = SignupForm()

    # Pre-set platform based on plan
    if plan.platform == 'woocommerce':
        form.platform.data = 'woocommerce'
    else:
        form.platform.data = 'magento'

    # Set plan slug in hidden field
    form.plan_slug.data = plan_slug

    # Check port availability
    port_usage = PortManager.get_port_usage()
    if port_usage['available'] == 0:
        flash('We are currently at capacity. Please try again later.', 'warning')
        return render_template('signup.html', form=form, plan=plan, ports_available=False)

    if form.validate_on_submit():
        try:
            # Get next available port
            web_port = PortManager.get_next_available_port()
            if web_port is None:
                flash('No ports available. Please try again later.', 'error')
                return render_template('signup.html', form=form, plan=plan, ports_available=False)

            # Create customer with pending_payment status
            customer = Customer(
                email=form.email.data.lower().strip(),
                company_name=form.company_name.data.strip(),
                domain=form.domain.data.lower().strip(),
                platform=plan.platform,  # Use platform from plan
                status='pending_payment',  # New status for payment pending
                web_port=web_port,
                plan_id=plan.id
            )
            customer.set_password(form.password.data)
            customer.save()

            logger.info(f"New customer signup (pending payment): {customer.email} - {customer.domain}")

            # Check if plan has Stripe price configured
            if not plan.stripe_price_id:
                logger.error(f"Plan {plan.slug} missing Stripe price ID")
                flash('This plan is not available for purchase yet. Please contact support.', 'error')
                return render_template('signup.html', form=form, plan=plan, ports_available=True)

            # Create Stripe Checkout Session
            try:
                checkout_session = create_checkout_session(customer, plan)

                # Store customer ID in session for post-checkout
                session['pending_customer_id'] = customer.id

                logger.info(f"Redirecting customer {customer.id} to Stripe Checkout: {checkout_session.id}")

                # Redirect to Stripe Checkout
                return redirect(checkout_session.url, code=303)

            except Exception as e:
                logger.error(f"Stripe checkout error: {e}")
                # Clean up the customer record on payment failure
                customer.delete()
                flash('Unable to process payment at this time. Please try again.', 'error')
                return render_template('signup.html', form=form, plan=plan, ports_available=True)

        except Exception as e:
            logger.error(f"Signup error: {e}")
            flash('An error occurred. Please try again.', 'error')

    return render_template('signup.html', form=form, plan=plan, ports_available=True)


@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("5 per minute", error_message="Too many login attempts. Please wait a minute.")
@limiter.limit("20 per hour", error_message="Too many login attempts. Please try again later.")
def login():
    """Customer login page"""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    form = LoginForm()

    if form.validate_on_submit():
        customer = Customer.get_by_email(form.email.data.lower().strip())

        if customer and customer.check_password(form.password.data):
            # Check if 2FA is enabled
            tfa_settings = Customer2FASettings.get_by_customer(customer.id)
            if tfa_settings and tfa_settings.is_enabled:
                # Store pending 2FA verification in session
                session['pending_2fa_customer_id'] = customer.id
                session['pending_2fa_next'] = request.args.get('next')
                logger.info(f"2FA required for customer: {customer.email}")
                return redirect(url_for('auth_2fa'))

            # No 2FA, complete login
            login_user(customer)
            _record_login(customer.id, success=True)
            logger.info(f"Customer login: {customer.email}")

            next_page = request.args.get('next')
            if next_page:
                return redirect(next_page)
            return redirect(url_for('dashboard'))
        else:
            # Record failed login attempt if customer exists
            if customer:
                _record_login(customer.id, success=False, failure_reason='invalid_password')
            flash('Invalid email or password', 'error')

    return render_template('login.html', form=form)


def _record_login(customer_id, success=True, failure_reason=None):
    """Helper to record login history"""
    try:
        ip_address = request.headers.get('X-Forwarded-For', request.remote_addr)
        if ip_address and ',' in ip_address:
            ip_address = ip_address.split(',')[0].strip()
        user_agent = request.headers.get('User-Agent', '')
        session_id = session.sid if hasattr(session, 'sid') else session.get('_id')
        if not session_id:
            session_id = secrets.token_hex(32)
            session['_id'] = session_id

        CustomerLoginHistory.create(
            customer_id=customer_id,
            ip_address=ip_address,
            user_agent=user_agent,
            success=success,
            failure_reason=failure_reason,
            session_id=session_id if success else None
        )
    except Exception as e:
        logger.error(f"Failed to record login history: {e}")


@app.route('/logout')
@login_required
def logout():
    """Log out customer"""
    logger.info(f"Customer logout: {current_user.email}")
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))


# =============================================================================
# Two-Factor Authentication Routes
# =============================================================================

@app.route('/auth/2fa')
def auth_2fa():
    """2FA verification page during login"""
    customer_id = session.get('pending_2fa_customer_id')
    if not customer_id:
        return redirect(url_for('login'))

    customer = Customer.get_by_id(customer_id)
    if not customer:
        session.pop('pending_2fa_customer_id', None)
        return redirect(url_for('login'))

    # Check for lockout
    lockout_until = session.get('2fa_lockout_until')
    if lockout_until and datetime.now().timestamp() < lockout_until:
        remaining = int(lockout_until - datetime.now().timestamp())
        flash(f'Too many failed attempts. Try again in {remaining // 60} minutes.', 'error')

    return render_template('auth/2fa_verify.html', customer=customer)


@app.route('/auth/2fa/verify', methods=['POST'])
@csrf.exempt
@limiter.limit("5 per minute")
def auth_2fa_verify():
    """Verify 2FA code during login"""
    customer_id = session.get('pending_2fa_customer_id')
    if not customer_id:
        return jsonify({'success': False, 'error': 'Session expired'}), 401

    # Check for lockout
    lockout_until = session.get('2fa_lockout_until')
    if lockout_until and datetime.now().timestamp() < lockout_until:
        remaining = int(lockout_until - datetime.now().timestamp())
        return jsonify({'success': False, 'error': f'Locked out. Try again in {remaining // 60} minutes.'}), 429

    data = request.get_json()
    code = data.get('code', '').strip()

    if not code:
        return jsonify({'success': False, 'error': 'Code is required'}), 400

    customer = Customer.get_by_id(customer_id)
    tfa_settings = Customer2FASettings.get_by_customer(customer_id)

    if not customer or not tfa_settings or not tfa_settings.is_enabled:
        session.pop('pending_2fa_customer_id', None)
        return jsonify({'success': False, 'error': 'Invalid session'}), 401

    # Track attempts
    attempts = session.get('2fa_attempts', 0)

    # Try TOTP code
    totp = pyotp.TOTP(tfa_settings.totp_secret)
    if totp.verify(code, valid_window=1):
        # Success - complete login
        session.pop('pending_2fa_customer_id', None)
        session.pop('2fa_attempts', None)
        session.pop('2fa_lockout_until', None)

        login_user(customer)
        _record_login(customer.id, success=True)
        tfa_settings.update_last_used()
        logger.info(f"2FA verified for customer: {customer.email}")

        next_page = session.pop('pending_2fa_next', None)
        return jsonify({'success': True, 'redirect': next_page or url_for('dashboard')})

    # Try backup code
    if len(code) == 8 and tfa_settings.backup_codes:
        code_hash = hashlib.sha256(code.upper().encode()).hexdigest()
        backup_codes = json.loads(tfa_settings.backup_codes)
        if code_hash in backup_codes:
            # Valid backup code
            tfa_settings.use_backup_code(code_hash)

            session.pop('pending_2fa_customer_id', None)
            session.pop('2fa_attempts', None)
            session.pop('2fa_lockout_until', None)

            login_user(customer)
            _record_login(customer.id, success=True)
            logger.info(f"2FA backup code used for customer: {customer.email}")

            next_page = session.pop('pending_2fa_next', None)
            return jsonify({
                'success': True,
                'redirect': next_page or url_for('dashboard'),
                'warning': f'Backup code used. {tfa_settings.backup_codes_remaining - 1} remaining.'
            })

    # Failed attempt
    attempts += 1
    session['2fa_attempts'] = attempts

    if attempts >= 5:
        # Lock out for 15 minutes
        session['2fa_lockout_until'] = datetime.now().timestamp() + (15 * 60)
        _record_login(customer_id, success=False, failure_reason='2fa_lockout')
        logger.warning(f"2FA lockout triggered for customer: {customer.email}")
        return jsonify({'success': False, 'error': 'Too many failed attempts. Locked out for 15 minutes.'}), 429

    return jsonify({'success': False, 'error': 'Invalid code', 'attempts_remaining': 5 - attempts}), 401


@app.route('/auth/2fa/recovery/send', methods=['POST'])
@csrf.exempt
@limiter.limit("3 per hour")
def auth_2fa_recovery_send():
    """Send 2FA recovery code via email"""
    customer_id = session.get('pending_2fa_customer_id')
    if not customer_id:
        return jsonify({'success': False, 'error': 'Session expired'}), 401

    customer = Customer.get_by_id(customer_id)
    if not customer:
        return jsonify({'success': False, 'error': 'Invalid session'}), 401

    # Generate 8-character recovery code
    recovery_code = secrets.token_hex(4).upper()
    code_hash = hashlib.sha256(recovery_code.encode()).hexdigest()

    # Store hashed code as verification token
    CustomerVerificationToken.create(
        customer_id=customer_id,
        token=code_hash,
        token_type='2fa_recovery',
        expires_minutes=15
    )

    # Send email
    try:
        from email_utils import send_2fa_recovery_email
        send_2fa_recovery_email(customer.email, recovery_code)
        logger.info(f"2FA recovery email sent to: {customer.email}")
        return jsonify({'success': True, 'message': 'Recovery code sent to your email'})
    except Exception as e:
        logger.error(f"Failed to send 2FA recovery email: {e}")
        return jsonify({'success': False, 'error': 'Failed to send email'}), 500


@app.route('/auth/2fa/recovery/verify', methods=['POST'])
@csrf.exempt
@limiter.limit("5 per minute")
def auth_2fa_recovery_verify():
    """Verify email recovery code"""
    customer_id = session.get('pending_2fa_customer_id')
    if not customer_id:
        return jsonify({'success': False, 'error': 'Session expired'}), 401

    data = request.get_json()
    code = data.get('code', '').strip().upper()

    if not code:
        return jsonify({'success': False, 'error': 'Code is required'}), 400

    code_hash = hashlib.sha256(code.encode()).hexdigest()
    token = CustomerVerificationToken.verify(code_hash, '2fa_recovery')

    if not token or token.customer_id != customer_id:
        return jsonify({'success': False, 'error': 'Invalid or expired code'}), 401

    # Valid - complete login
    token.mark_used()
    customer = Customer.get_by_id(customer_id)

    session.pop('pending_2fa_customer_id', None)
    session.pop('2fa_attempts', None)
    session.pop('2fa_lockout_until', None)

    login_user(customer)
    _record_login(customer.id, success=True)
    logger.info(f"2FA email recovery used for customer: {customer.email}")

    next_page = session.pop('pending_2fa_next', None)
    return jsonify({'success': True, 'redirect': next_page or url_for('dashboard')})


# =============================================================================
# Stripe Checkout Routes
# =============================================================================

@app.route('/checkout/success')
def checkout_success():
    """Handle successful checkout - display success page"""
    session_id = request.args.get('session_id')
    customer = None
    plan = None

    if session_id:
        # Retrieve session to get customer info
        checkout_session = get_checkout_session(session_id)
        if checkout_session:
            customer_id = checkout_session.get('client_reference_id')
            if customer_id:
                customer = Customer.get_by_id(int(customer_id))
                if customer and customer.plan_id:
                    plan = PricingPlan.get_by_id(customer.plan_id)
                # Log the user in
                if customer:
                    login_user(customer)

    return render_template('checkout_success.html', customer=customer, plan=plan)


@app.route('/checkout/cancel')
def checkout_cancel():
    """Handle cancelled checkout"""
    # Clean up pending customer if they cancel
    pending_customer_id = session.pop('pending_customer_id', None)
    if pending_customer_id:
        customer = Customer.get_by_id(pending_customer_id)
        if customer and customer.status == 'pending_payment':
            customer.delete()
            logger.info(f"Cleaned up pending customer {pending_customer_id} after checkout cancel")

    return render_template('checkout_cancel.html')


@app.route('/webhook/stripe', methods=['POST'])
@csrf.exempt
def stripe_webhook():
    """Handle Stripe webhooks"""
    payload = request.get_data()
    sig_header = request.headers.get('Stripe-Signature')

    if not sig_header:
        logger.warning("Stripe webhook received without signature")
        return jsonify({'error': 'No signature'}), 400

    success, message = process_webhook(payload, sig_header)

    if success:
        return jsonify({'status': 'success', 'message': message}), 200
    else:
        # Return 200 for permanent errors to prevent retry loops
        # Return 500 only for transient errors
        logger.error(f"Webhook processing failed: {message}")
        return jsonify({'status': 'error', 'message': message}), 200


# =============================================================================
# Billing Routes
# =============================================================================

@app.route('/billing')
@login_required
def billing():
    """Billing management page"""
    customer = Customer.get_by_id(current_user.id)

    # Get subscription and plan
    subscription = Subscription.get_by_customer_id(customer.id)
    plan = None
    if subscription and subscription.plan_id:
        plan = PricingPlan.get_by_id(subscription.plan_id)
    elif customer.plan_id:
        plan = PricingPlan.get_by_id(customer.plan_id)

    # Get invoices
    invoices = Invoice.get_by_customer_id(customer.id)

    return render_template('billing.html',
                          customer=customer,
                          subscription=subscription,
                          plan=plan,
                          invoices=invoices)


@app.route('/billing/portal', methods=['POST'])
@login_required
def billing_portal():
    """Redirect to Stripe Customer Portal"""
    customer = Customer.get_by_id(current_user.id)

    if not customer.stripe_customer_id:
        flash('No billing information found. Please contact support.', 'error')
        return redirect(url_for('billing'))

    try:
        portal_session = create_portal_session(customer.stripe_customer_id)
        return redirect(portal_session.url, code=303)
    except Exception as e:
        logger.error(f"Error creating portal session: {e}")
        flash('Unable to access billing portal. Please try again.', 'error')
        return redirect(url_for('billing'))


# =============================================================================
# Routes - Protected (require login)
# =============================================================================

@app.route('/dashboard')
@login_required
def dashboard():
    """Redirect to dashboard overview"""
    return redirect(url_for('dashboard_overview'))


@app.route('/dashboard/overview')
@login_required
def dashboard_overview():
    """Dashboard overview page"""
    customer = Customer.get_by_id(current_user.id)
    credentials = customer.get_credentials()
    plan = PricingPlan.get_by_id(customer.plan_id) if customer.plan_id else None
    usage = customer.get_resource_usage() if hasattr(customer, 'get_resource_usage') else {
        'disk': {'used_gb': 0, 'limit_gb': 10, 'percent': 0},
        'bandwidth': {'used_gb': 0, 'limit_gb': 100, 'percent': 0}
    }

    return render_template('dashboard/overview.html',
                          customer=customer,
                          credentials=credentials,
                          plan=plan,
                          usage=usage,
                          active_page='overview')


@app.route('/dashboard/health')
@login_required
def dashboard_health():
    """Site health page"""
    customer = Customer.get_by_id(current_user.id)
    credentials = customer.get_credentials()

    return render_template('dashboard/health.html',
                          customer=customer,
                          credentials=credentials,
                          active_page='health')


@app.route('/dashboard/backups')
@login_required
def dashboard_backups():
    """Backups management page"""
    customer = Customer.get_by_id(current_user.id)
    active_job = CustomerBackupJob.get_active_job(customer.id)
    recent_jobs = CustomerBackupJob.get_recent_jobs(customer.id, limit=5)

    # Get manual backups from restic
    manual_backups = get_customer_manual_backups(customer.id)

    # Get daily backups (filtered to this customer's data)
    daily_backups = get_customer_daily_backups(customer.id)

    return render_template('dashboard/backups.html',
                          customer=customer,
                          active_job=active_job,
                          recent_jobs=recent_jobs,
                          manual_backups=manual_backups,
                          daily_backups=daily_backups,
                          active_page='backups')


@app.route('/dashboard/staging')
@login_required
def dashboard_staging():
    """Staging environments page"""
    customer = Customer.get_by_id(current_user.id)
    if not customer:
        flash('Customer account not found.', 'error')
        return redirect(url_for('dashboard'))

    # Defensive handling in case staging_environments table doesn't exist yet
    try:
        staging_envs = StagingEnvironment.get_by_customer(customer.id)
        can_create = StagingEnvironment.can_create_staging(customer.id) and customer.status == 'active'
        max_staging = StagingEnvironment.MAX_STAGING_PER_CUSTOMER
    except Exception as e:
        app.logger.warning(f"Staging feature not available: {e}")
        staging_envs = []
        can_create = False
        max_staging = 3

    return render_template('dashboard/staging.html',
                          customer=customer,
                          staging_envs=staging_envs,
                          can_create=can_create,
                          max_staging=max_staging,
                          active_page='staging')


@app.route('/dashboard/domains')
@login_required
def dashboard_domains():
    """Domains management page"""
    from cloudflare.models import CloudflareConnection, DNSRecordCache

    customer = Customer.get_by_id(current_user.id)
    if not customer:
        flash('Customer account not found.', 'error')
        return redirect(url_for('dashboard'))

    # Server IP for DNS configuration
    server_ip = os.environ.get('SERVER_IP', '147.135.8.170')

    # Get Cloudflare connection status and DNS records
    cloudflare_connection = CloudflareConnection.get_by_customer_id(customer.id)
    cloudflare_connected = cloudflare_connection is not None
    dns_records = []
    last_sync_time = None

    if cloudflare_connected and cloudflare_connection.last_sync_at:
        dns_records = DNSRecordCache.get_by_customer_id(customer.id)
        last_sync_time = cloudflare_connection.last_sync_at.strftime('%Y-%m-%d %H:%M:%S')

    return render_template('dashboard/domains.html',
                          customer=customer,
                          server_ip=server_ip,
                          cloudflare_connected=cloudflare_connected,
                          dns_records=dns_records,
                          last_sync_time=last_sync_time,
                          active_page='domains')


@app.route('/api/domain/health')
@login_required
def api_domain_health():
    """Check domain health (DNS resolution, SSL status)"""
    import socket
    import ssl
    from datetime import datetime

    customer = Customer.get_by_id(current_user.id)
    if not customer or not customer.domain:
        return jsonify({'error': 'No domain configured'}), 400

    domain = customer.domain
    server_ip = os.environ.get('SERVER_IP', '147.135.8.170')
    result = {
        'domain': domain,
        'dns': {'status': 'unknown', 'resolved_ip': None, 'points_to_us': False},
        'ssl': {'status': 'unknown', 'issuer': None, 'expiry': None, 'days_remaining': None},
        'http': {'status': 'unknown'}
    }

    # Check DNS resolution
    try:
        resolved_ip = socket.gethostbyname(domain)
        result['dns']['resolved_ip'] = resolved_ip
        result['dns']['points_to_us'] = (resolved_ip == server_ip)
        result['dns']['status'] = 'ok' if resolved_ip == server_ip else 'misconfigured'
    except socket.gaierror:
        result['dns']['status'] = 'not_found'
    except Exception as e:
        result['dns']['status'] = 'error'
        result['dns']['error'] = str(e)

    # Check SSL certificate
    try:
        context = ssl.create_default_context()
        with socket.create_connection((domain, 443), timeout=10) as sock:
            with context.wrap_socket(sock, server_hostname=domain) as ssock:
                cert = ssock.getpeercert()
                # Get expiry date
                expiry_str = cert.get('notAfter', '')
                if expiry_str:
                    expiry = datetime.strptime(expiry_str, '%b %d %H:%M:%S %Y %Z')
                    result['ssl']['expiry'] = expiry.strftime('%Y-%m-%d')
                    result['ssl']['days_remaining'] = (expiry - datetime.utcnow()).days

                # Get issuer
                issuer = dict(x[0] for x in cert.get('issuer', []))
                result['ssl']['issuer'] = issuer.get('organizationName', issuer.get('commonName', 'Unknown'))
                result['ssl']['status'] = 'valid'
    except ssl.SSLCertVerificationError as e:
        result['ssl']['status'] = 'invalid'
        result['ssl']['error'] = 'Certificate verification failed'
    except socket.timeout:
        result['ssl']['status'] = 'timeout'
    except ConnectionRefusedError:
        result['ssl']['status'] = 'no_https'
    except Exception as e:
        result['ssl']['status'] = 'error'
        result['ssl']['error'] = str(e)[:100]

    # Check HTTP connectivity
    try:
        import urllib.request
        req = urllib.request.Request(
            f'https://{domain}',
            headers={'User-Agent': 'ShopHosting Health Check'}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            result['http']['status'] = 'ok'
            result['http']['status_code'] = response.status
    except urllib.error.HTTPError as e:
        result['http']['status'] = 'ok'  # Server responded, even if error
        result['http']['status_code'] = e.code
    except Exception as e:
        result['http']['status'] = 'error'
        result['http']['error'] = str(e)[:100]

    return jsonify(result)


@app.route('/dashboard/billing')
@login_required
def dashboard_billing():
    """Billing page"""
    customer = Customer.get_by_id(current_user.id)

    # Get subscription and plan
    subscription = Subscription.get_by_customer_id(customer.id)
    plan = None
    if subscription and subscription.plan_id:
        plan = PricingPlan.get_by_id(subscription.plan_id)
    elif customer.plan_id:
        plan = PricingPlan.get_by_id(customer.plan_id)

    # Get invoices
    invoices = Invoice.get_by_customer_id(customer.id)

    return render_template('dashboard/billing.html',
                          customer=customer,
                          subscription=subscription,
                          plan=plan,
                          invoices=invoices,
                          active_page='billing')


@app.route('/dashboard/settings')
@login_required
def dashboard_settings():
    """Account settings page with security features"""
    customer = Customer.get_by_id(current_user.id)

    # Get 2FA settings
    tfa_settings = Customer2FASettings.get_by_customer(current_user.id)

    # Get login history
    login_history = CustomerLoginHistory.get_by_customer(current_user.id, limit=10)

    # Get active sessions
    current_session_id = session.get('_id')
    active_sessions = CustomerLoginHistory.get_active_sessions(
        current_user.id,
        current_session_id=current_session_id
    )

    # Phase 2 data
    notification_settings = CustomerNotificationSettings.get_or_create(current_user.id)
    api_keys = CustomerApiKey.get_by_customer(current_user.id)
    webhooks = CustomerWebhook.get_by_customer(current_user.id)
    deletion_request = CustomerDeletionRequest.get_by_customer(current_user.id)

    # Common timezones for dropdown
    common_timezones = [
        'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles',
        'America/Toronto', 'America/Vancouver', 'Europe/London', 'Europe/Paris',
        'Europe/Berlin', 'Europe/Amsterdam', 'Asia/Tokyo', 'Asia/Shanghai',
        'Asia/Singapore', 'Australia/Sydney', 'Pacific/Auckland', 'UTC'
    ]

    return render_template('dashboard/settings.html',
                          customer=customer,
                          tfa_settings=tfa_settings,
                          login_history=login_history,
                          active_sessions=active_sessions,
                          notification_settings=notification_settings,
                          api_keys=api_keys,
                          webhooks=webhooks,
                          deletion_request=deletion_request,
                          common_timezones=common_timezones,
                          webhook_events=CustomerWebhook.VALID_EVENTS,
                          active_page='settings')


# =============================================================================
# Settings API Routes
# =============================================================================

@app.route('/api/settings/password', methods=['POST'])
@csrf.exempt
@login_required
@limiter.limit("5 per hour")
def api_settings_password():
    """Change password"""
    data = request.get_json()
    current_password = data.get('current_password', '')
    new_password = data.get('new_password', '')

    if not current_password or not new_password:
        return jsonify({'success': False, 'error': 'Both passwords are required'}), 400

    customer = Customer.get_by_id(current_user.id)

    if not customer.check_password(current_password):
        security_logger.warning(f"Password change failed - wrong current password: {customer.email}")
        return jsonify({'success': False, 'error': 'Current password is incorrect'}), 401

    if len(new_password) < 8:
        return jsonify({'success': False, 'error': 'New password must be at least 8 characters'}), 400

    # Update password
    customer.set_password(new_password)
    customer.update_password_changed_at()

    security_logger.info(f"Password changed for customer: {customer.email}")
    return jsonify({'success': True, 'message': 'Password updated successfully'})


@app.route('/api/settings/2fa/setup', methods=['POST'])
@csrf.exempt
@login_required
@limiter.limit("10 per hour")
def api_settings_2fa_setup():
    """Generate TOTP secret and QR code for 2FA setup"""
    customer = Customer.get_by_id(current_user.id)

    # Generate new secret
    secret = pyotp.random_base32()

    # Store in database (not yet enabled)
    Customer2FASettings.create(current_user.id, secret)

    # Generate provisioning URI for QR code
    totp = pyotp.TOTP(secret)
    provisioning_uri = totp.provisioning_uri(
        name=customer.email,
        issuer_name='ShopHosting.io'
    )

    # Generate QR code as base64
    try:
        import qrcode
        qr = qrcode.QRCode(version=1, box_size=4, border=2)
        qr.add_data(provisioning_uri)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")

        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)
        qr_base64 = base64.b64encode(buffer.getvalue()).decode()
    except ImportError:
        # qrcode library not installed, return URI for client-side generation
        qr_base64 = None

    return jsonify({
        'success': True,
        'secret': secret,
        'qr_code': f'data:image/png;base64,{qr_base64}' if qr_base64 else None,
        'provisioning_uri': provisioning_uri
    })


@app.route('/api/settings/2fa/verify', methods=['POST'])
@csrf.exempt
@login_required
@limiter.limit("10 per hour")
def api_settings_2fa_verify():
    """Verify TOTP code and enable 2FA"""
    data = request.get_json()
    code = data.get('code', '').strip()

    if not code or len(code) != 6:
        return jsonify({'success': False, 'error': 'Invalid code format'}), 400

    tfa_settings = Customer2FASettings.get_by_customer(current_user.id)
    if not tfa_settings or not tfa_settings.totp_secret:
        return jsonify({'success': False, 'error': 'Setup not started'}), 400

    if tfa_settings.is_enabled:
        return jsonify({'success': False, 'error': '2FA is already enabled'}), 400

    # Verify code
    totp = pyotp.TOTP(tfa_settings.totp_secret)
    if not totp.verify(code, valid_window=1):
        return jsonify({'success': False, 'error': 'Invalid code'}), 401

    # Generate backup codes
    backup_codes = []
    backup_codes_hashed = []
    for _ in range(10):
        code = secrets.token_hex(4).upper()  # 8-char codes
        backup_codes.append(code)
        backup_codes_hashed.append(hashlib.sha256(code.encode()).hexdigest())

    # Enable 2FA
    tfa_settings.enable(json.dumps(backup_codes_hashed))

    customer = Customer.get_by_id(current_user.id)
    security_logger.info(f"2FA enabled for customer: {customer.email}")

    return jsonify({
        'success': True,
        'message': '2FA enabled successfully',
        'backup_codes': backup_codes
    })


@app.route('/api/settings/2fa/disable', methods=['POST'])
@csrf.exempt
@login_required
@limiter.limit("5 per hour")
def api_settings_2fa_disable():
    """Disable 2FA"""
    data = request.get_json()
    password = data.get('password', '')

    if not password:
        return jsonify({'success': False, 'error': 'Password is required'}), 400

    customer = Customer.get_by_id(current_user.id)
    if not customer.check_password(password):
        security_logger.warning(f"2FA disable failed - wrong password: {customer.email}")
        return jsonify({'success': False, 'error': 'Incorrect password'}), 401

    tfa_settings = Customer2FASettings.get_by_customer(current_user.id)
    if not tfa_settings or not tfa_settings.is_enabled:
        return jsonify({'success': False, 'error': '2FA is not enabled'}), 400

    tfa_settings.disable()

    security_logger.info(f"2FA disabled for customer: {customer.email}")
    return jsonify({'success': True, 'message': '2FA disabled successfully'})


@app.route('/api/settings/2fa/backup-codes/regenerate', methods=['POST'])
@csrf.exempt
@login_required
@limiter.limit("5 per hour")
def api_settings_2fa_backup_codes_regenerate():
    """Regenerate backup codes"""
    data = request.get_json()
    password = data.get('password', '')

    if not password:
        return jsonify({'success': False, 'error': 'Password is required'}), 400

    customer = Customer.get_by_id(current_user.id)
    if not customer.check_password(password):
        return jsonify({'success': False, 'error': 'Incorrect password'}), 401

    tfa_settings = Customer2FASettings.get_by_customer(current_user.id)
    if not tfa_settings or not tfa_settings.is_enabled:
        return jsonify({'success': False, 'error': '2FA is not enabled'}), 400

    # Generate new backup codes
    backup_codes = []
    backup_codes_hashed = []
    for _ in range(10):
        code = secrets.token_hex(4).upper()
        backup_codes.append(code)
        backup_codes_hashed.append(hashlib.sha256(code.encode()).hexdigest())

    tfa_settings.regenerate_backup_codes(json.dumps(backup_codes_hashed))

    security_logger.info(f"Backup codes regenerated for customer: {customer.email}")
    return jsonify({
        'success': True,
        'message': 'Backup codes regenerated',
        'backup_codes': backup_codes
    })


@app.route('/api/settings/sessions', methods=['GET'])
@login_required
def api_settings_sessions():
    """Get active sessions"""
    current_session_id = session.get('_id')
    sessions = CustomerLoginHistory.get_active_sessions(
        current_user.id,
        current_session_id=current_session_id
    )

    return jsonify({
        'success': True,
        'sessions': [{
            'id': s.id,
            'ip_address': s.ip_address,
            'user_agent': s.user_agent,
            'created_at': s.created_at.isoformat() if s.created_at else None,
            'is_current': getattr(s, 'is_current', False)
        } for s in sessions]
    })


@app.route('/api/settings/sessions/logout-all', methods=['POST'])
@csrf.exempt
@login_required
@limiter.limit("5 per hour")
def api_settings_logout_all():
    """Logout all sessions except current"""
    data = request.get_json()
    password = data.get('password', '')

    if not password:
        return jsonify({'success': False, 'error': 'Password is required'}), 400

    customer = Customer.get_by_id(current_user.id)
    if not customer.check_password(password):
        return jsonify({'success': False, 'error': 'Incorrect password'}), 401

    current_session_id = session.get('_id')
    count = CustomerLoginHistory.invalidate_all_sessions(
        current_user.id,
        except_session_id=current_session_id
    )

    security_logger.info(f"All sessions logged out for customer: {customer.email}")
    return jsonify({
        'success': True,
        'message': f'Logged out {count} other session(s)'
    })


@app.route('/api/settings/login-history', methods=['GET'])
@login_required
def api_settings_login_history():
    """Get login history"""
    history = CustomerLoginHistory.get_by_customer(current_user.id, limit=20)

    return jsonify({
        'success': True,
        'history': [{
            'id': h.id,
            'ip_address': h.ip_address,
            'user_agent': h.user_agent,
            'location': h.location,
            'success': h.success,
            'failure_reason': h.failure_reason,
            'created_at': h.created_at.isoformat() if h.created_at else None
        } for h in history]
    })


# =============================================================================
# Settings API Routes - Phase 2 (Profile, Notifications, API Keys, Webhooks)
# =============================================================================

@app.route('/api/settings/profile', methods=['POST'])
@csrf.exempt
@login_required
def api_settings_profile():
    """Update profile information"""
    data = request.get_json()
    company_name = data.get('company_name')
    timezone = data.get('timezone')

    customer = Customer.get_by_id(current_user.id)
    customer.update_profile(company_name=company_name, timezone=timezone)

    return jsonify({'success': True, 'message': 'Profile updated successfully'})


@app.route('/api/settings/email/change', methods=['POST'])
@csrf.exempt
@login_required
@limiter.limit("3 per hour")
def api_settings_email_change():
    """Request email change - sends verification to new email"""
    data = request.get_json()
    new_email = data.get('new_email', '').strip().lower()
    password = data.get('password', '')

    if not new_email or not password:
        return jsonify({'success': False, 'error': 'Email and password are required'}), 400

    # Validate email format
    import re
    if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', new_email):
        return jsonify({'success': False, 'error': 'Invalid email format'}), 400

    customer = Customer.get_by_id(current_user.id)

    if not customer.check_password(password):
        return jsonify({'success': False, 'error': 'Incorrect password'}), 401

    if new_email == customer.email:
        return jsonify({'success': False, 'error': 'New email must be different'}), 400

    # Check if email already exists
    existing = Customer.get_by_email(new_email)
    if existing:
        return jsonify({'success': False, 'error': 'Email already in use'}), 400

    # Generate verification token
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    CustomerVerificationToken.create(
        customer_id=current_user.id,
        token=token_hash,
        token_type='email_change',
        new_value=new_email,
        expires_minutes=60
    )

    # Send verification email to new address
    try:
        from email_utils import send_email_change_verification
        send_email_change_verification(new_email, token)
        return jsonify({'success': True, 'message': 'Verification email sent to new address'})
    except Exception as e:
        logger.error(f"Failed to send email change verification: {e}")
        return jsonify({'success': False, 'error': 'Failed to send verification email'}), 500


@app.route('/api/settings/email/verify', methods=['POST'])
@csrf.exempt
@limiter.limit("5 per hour")
def api_settings_email_verify():
    """Verify email change token"""
    data = request.get_json()
    token = data.get('token', '')

    if not token:
        return jsonify({'success': False, 'error': 'Token is required'}), 400

    token_hash = hashlib.sha256(token.encode()).hexdigest()
    verification = CustomerVerificationToken.verify(token_hash, 'email_change')

    if not verification:
        return jsonify({'success': False, 'error': 'Invalid or expired token'}), 400

    # Update the email
    customer = Customer.get_by_id(verification.customer_id)
    old_email = customer.email
    customer.update_email(verification.new_value)
    verification.mark_used()

    security_logger.info(f"Email changed for customer {verification.customer_id}: {old_email} -> {verification.new_value}")

    return jsonify({'success': True, 'message': 'Email updated successfully'})


@app.route('/api/settings/notifications', methods=['GET'])
@login_required
def api_settings_notifications_get():
    """Get notification preferences"""
    settings = CustomerNotificationSettings.get_or_create(current_user.id)

    return jsonify({
        'success': True,
        'settings': {
            'email_security_alerts': settings.email_security_alerts,
            'email_login_alerts': settings.email_login_alerts,
            'email_billing_alerts': settings.email_billing_alerts,
            'email_maintenance_alerts': settings.email_maintenance_alerts,
            'email_marketing': settings.email_marketing
        }
    })


@app.route('/api/settings/notifications', methods=['POST'])
@csrf.exempt
@login_required
def api_settings_notifications_update():
    """Update notification preferences"""
    data = request.get_json()
    settings = CustomerNotificationSettings.get_or_create(current_user.id)

    # Update only provided fields
    update_fields = {}
    for field in ['email_security_alerts', 'email_login_alerts', 'email_billing_alerts',
                  'email_maintenance_alerts', 'email_marketing']:
        if field in data:
            update_fields[field] = bool(data[field])

    settings.update(**update_fields)

    return jsonify({'success': True, 'message': 'Notification preferences updated'})


@app.route('/api/settings/api-keys', methods=['GET'])
@login_required
def api_settings_api_keys_list():
    """List API keys"""
    keys = CustomerApiKey.get_by_customer(current_user.id)

    return jsonify({
        'success': True,
        'keys': [{
            'id': k.id,
            'name': k.name,
            'key_prefix': f"shk_{k.key_prefix}_...",
            'created_at': k.created_at.isoformat() if k.created_at else None,
            'last_used_at': k.last_used_at.isoformat() if k.last_used_at else None,
            'expires_at': k.expires_at.isoformat() if k.expires_at else None
        } for k in keys]
    })


@app.route('/api/settings/api-keys', methods=['POST'])
@csrf.exempt
@login_required
@limiter.limit("10 per hour")
def api_settings_api_keys_create():
    """Create a new API key"""
    data = request.get_json()
    name = data.get('name', '').strip()
    expires_days = data.get('expires_days')

    if not name:
        return jsonify({'success': False, 'error': 'Name is required'}), 400

    if len(name) > 100:
        return jsonify({'success': False, 'error': 'Name too long (max 100 chars)'}), 400

    # Check limit (max 10 active keys)
    existing = CustomerApiKey.get_by_customer(current_user.id)
    if len(existing) >= 10:
        return jsonify({'success': False, 'error': 'Maximum 10 API keys allowed'}), 400

    api_key, raw_key = CustomerApiKey.create(
        customer_id=current_user.id,
        name=name,
        expires_days=expires_days
    )

    security_logger.info(f"API key created for customer {current_user.id}: {name}")

    return jsonify({
        'success': True,
        'key': {
            'id': api_key.id,
            'name': api_key.name,
            'key': raw_key,  # Only shown once!
            'created_at': api_key.created_at.isoformat() if api_key.created_at else None
        },
        'message': 'API key created. Save it now - it won\'t be shown again!'
    })


@app.route('/api/settings/api-keys/<int:key_id>', methods=['DELETE'])
@csrf.exempt
@login_required
def api_settings_api_keys_revoke(key_id):
    """Revoke an API key"""
    keys = CustomerApiKey.get_by_customer(current_user.id)
    key = next((k for k in keys if k.id == key_id), None)

    if not key:
        return jsonify({'success': False, 'error': 'API key not found'}), 404

    key.revoke()
    security_logger.info(f"API key revoked for customer {current_user.id}: {key.name}")

    return jsonify({'success': True, 'message': 'API key revoked'})


@app.route('/api/settings/webhooks', methods=['GET'])
@login_required
def api_settings_webhooks_list():
    """List webhooks"""
    webhooks = CustomerWebhook.get_by_customer(current_user.id)

    return jsonify({
        'success': True,
        'webhooks': [{
            'id': w.id,
            'name': w.name,
            'url': w.url,
            'events': json.loads(w.events) if w.events else [],
            'is_active': w.is_active,
            'failure_count': w.failure_count,
            'last_triggered_at': w.last_triggered_at.isoformat() if w.last_triggered_at else None,
            'created_at': w.created_at.isoformat() if w.created_at else None
        } for w in webhooks],
        'available_events': CustomerWebhook.VALID_EVENTS
    })


@app.route('/api/settings/webhooks', methods=['POST'])
@csrf.exempt
@login_required
@limiter.limit("10 per hour")
def api_settings_webhooks_create():
    """Create a new webhook"""
    data = request.get_json()
    name = data.get('name', '').strip()
    url = data.get('url', '').strip()
    events = data.get('events', [])

    if not name or not url:
        return jsonify({'success': False, 'error': 'Name and URL are required'}), 400

    if not url.startswith('https://'):
        return jsonify({'success': False, 'error': 'URL must use HTTPS'}), 400

    if not events:
        return jsonify({'success': False, 'error': 'At least one event is required'}), 400

    # Validate events
    invalid_events = [e for e in events if e not in CustomerWebhook.VALID_EVENTS]
    if invalid_events:
        return jsonify({'success': False, 'error': f'Invalid events: {invalid_events}'}), 400

    # Check limit (max 5 webhooks)
    existing = CustomerWebhook.get_by_customer(current_user.id)
    if len(existing) >= 5:
        return jsonify({'success': False, 'error': 'Maximum 5 webhooks allowed'}), 400

    webhook = CustomerWebhook.create(
        customer_id=current_user.id,
        name=name,
        url=url,
        events=events
    )

    return jsonify({
        'success': True,
        'webhook': {
            'id': webhook.id,
            'name': webhook.name,
            'secret': webhook.secret  # Only shown once!
        },
        'message': 'Webhook created. Save the secret - it won\'t be shown again!'
    })


@app.route('/api/settings/webhooks/<int:webhook_id>', methods=['PUT'])
@csrf.exempt
@login_required
def api_settings_webhooks_update(webhook_id):
    """Update a webhook"""
    webhook = CustomerWebhook.get_by_id(webhook_id)

    if not webhook or webhook.customer_id != current_user.id:
        return jsonify({'success': False, 'error': 'Webhook not found'}), 404

    data = request.get_json()
    update_fields = {}

    if 'name' in data:
        update_fields['name'] = data['name'].strip()
    if 'url' in data:
        url = data['url'].strip()
        if not url.startswith('https://'):
            return jsonify({'success': False, 'error': 'URL must use HTTPS'}), 400
        update_fields['url'] = url
    if 'events' in data:
        events = data['events']
        invalid_events = [e for e in events if e not in CustomerWebhook.VALID_EVENTS]
        if invalid_events:
            return jsonify({'success': False, 'error': f'Invalid events: {invalid_events}'}), 400
        update_fields['events'] = events
    if 'is_active' in data:
        update_fields['is_active'] = bool(data['is_active'])

    webhook.update(**update_fields)

    return jsonify({'success': True, 'message': 'Webhook updated'})


@app.route('/api/settings/webhooks/<int:webhook_id>', methods=['DELETE'])
@csrf.exempt
@login_required
def api_settings_webhooks_delete(webhook_id):
    """Delete a webhook"""
    webhook = CustomerWebhook.get_by_id(webhook_id)

    if not webhook or webhook.customer_id != current_user.id:
        return jsonify({'success': False, 'error': 'Webhook not found'}), 404

    webhook.delete()

    return jsonify({'success': True, 'message': 'Webhook deleted'})


@app.route('/api/settings/data-export', methods=['POST'])
@csrf.exempt
@login_required
@limiter.limit("3 per day")
def api_settings_data_export():
    """Request a data export (GDPR)"""
    from background_tasks import run_task, process_data_export

    # Check for existing pending export and re-trigger it
    existing_exports = CustomerDataExport.get_by_customer(current_user.id, limit=1)
    if existing_exports and existing_exports[0].status == 'pending':
        export = existing_exports[0]
        run_task(process_data_export, export.id, current_user.id)
        return jsonify({
            'success': True,
            'message': 'Export already requested. Processing has been started.',
            'export_id': export.id
        })

    export = CustomerDataExport.create(current_user.id)

    if not export:
        return jsonify({'success': False, 'error': 'Export already in progress'}), 400

    # Start background export task
    run_task(process_data_export, export.id, current_user.id)

    return jsonify({
        'success': True,
        'message': 'Data export requested. You will receive an email when it\'s ready.',
        'export_id': export.id
    })


@app.route('/api/settings/data-export', methods=['GET'])
@login_required
def api_settings_data_export_list():
    """List data export requests"""
    exports = CustomerDataExport.get_by_customer(current_user.id)

    return jsonify({
        'success': True,
        'exports': [{
            'id': e.id,
            'status': e.status,
            'file_size_bytes': e.file_size_bytes,
            'requested_at': e.requested_at.isoformat() if e.requested_at else None,
            'completed_at': e.completed_at.isoformat() if e.completed_at else None,
            'expires_at': e.expires_at.isoformat() if e.expires_at else None
        } for e in exports]
    })


@app.route('/dashboard/settings/export/download')
@login_required
def settings_export_download():
    """Download a data export file"""
    from background_tasks import verify_download_token, EXPORT_DIR

    token = request.args.get('token', '')
    if not token:
        flash('Invalid download link', 'error')
        return redirect(url_for('dashboard_settings'))

    export = verify_download_token(token, current_user.id)
    if not export:
        flash('Invalid or expired download link', 'error')
        return redirect(url_for('dashboard_settings'))

    if not export.file_path:
        flash('Export file not found', 'error')
        return redirect(url_for('dashboard_settings'))

    filepath = os.path.join(EXPORT_DIR, export.file_path)
    if not os.path.exists(filepath):
        flash('Export file no longer available', 'error')
        return redirect(url_for('dashboard_settings'))

    return send_file(
        filepath,
        mimetype='application/zip',
        as_attachment=True,
        download_name=f'shophosting_data_export_{current_user.id}.zip'
    )


@app.route('/api/settings/account/delete', methods=['POST'])
@csrf.exempt
@login_required
@limiter.limit("3 per day")
def api_settings_account_delete():
    """Request account deletion"""
    data = request.get_json()
    password = data.get('password', '')
    reason = data.get('reason', '')

    if not password:
        return jsonify({'success': False, 'error': 'Password is required'}), 400

    customer = Customer.get_by_id(current_user.id)

    if not customer.check_password(password):
        return jsonify({'success': False, 'error': 'Incorrect password'}), 401

    deletion = CustomerDeletionRequest.create(
        customer_id=current_user.id,
        reason=reason,
        delay_days=14
    )

    security_logger.warning(f"Account deletion requested for customer {current_user.id}")

    return jsonify({
        'success': True,
        'message': 'Account deletion scheduled',
        'scheduled_at': deletion.scheduled_at.isoformat()
    })


@app.route('/api/settings/account/delete', methods=['GET'])
@login_required
def api_settings_account_delete_status():
    """Get account deletion status"""
    deletion = CustomerDeletionRequest.get_by_customer(current_user.id)

    if not deletion:
        return jsonify({'success': True, 'pending': False})

    return jsonify({
        'success': True,
        'pending': True,
        'scheduled_at': deletion.scheduled_at.isoformat(),
        'reason': deletion.reason
    })


@app.route('/api/settings/account/delete/cancel', methods=['POST'])
@csrf.exempt
@login_required
def api_settings_account_delete_cancel():
    """Cancel account deletion"""
    deletion = CustomerDeletionRequest.get_by_customer(current_user.id)

    if not deletion:
        return jsonify({'success': False, 'error': 'No pending deletion request'}), 404

    deletion.cancel()
    security_logger.info(f"Account deletion cancelled for customer {current_user.id}")

    return jsonify({'success': True, 'message': 'Deletion request cancelled'})


@app.route('/dashboard/support')
@login_required
def dashboard_support():
    """Support ticketing page"""
    customer = Customer.get_by_id(current_user.id)
    status_filter = request.args.get('status')
    page = request.args.get('page', 1, type=int)

    tickets, total = Ticket.get_by_customer(current_user.id, status=status_filter, page=page)
    total_pages = (total + 19) // 20

    return render_template('dashboard/support.html',
                          customer=customer,
                          tickets=tickets,
                          total=total,
                          page=page,
                          total_pages=total_pages,
                          status_filter=status_filter,
                          active_page='support')


# =============================================================================
# Health Check Endpoints
# =============================================================================

@app.route('/health')
@limiter.exempt  # Health checks should not be rate limited
def health_check():
    """
    Health check endpoint for load balancers and monitoring.
    Returns 200 if the application can connect to its dependencies.
    """
    health_status = {
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'checks': {}
    }
    overall_healthy = True

    # Check database connectivity
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.fetchone()
        cursor.close()
        conn.close()
        health_status['checks']['database'] = {'status': 'healthy'}
    except Exception as e:
        health_status['checks']['database'] = {
            'status': 'unhealthy',
            'error': str(e)[:100]  # Truncate error message
        }
        overall_healthy = False

    # Check Redis connectivity
    try:
        redis_client = redis.from_url(redis_url, socket_connect_timeout=2)
        redis_client.ping()
        health_status['checks']['redis'] = {'status': 'healthy'}
    except Exception as e:
        health_status['checks']['redis'] = {
            'status': 'unhealthy',
            'error': str(e)[:100]
        }
        overall_healthy = False

    if not overall_healthy:
        health_status['status'] = 'unhealthy'
        return jsonify(health_status), 503

    return jsonify(health_status), 200


@app.route('/ready')
@limiter.exempt  # Readiness checks should not be rate limited
def readiness_check():
    """
    Readiness probe for Kubernetes/orchestration.
    Indicates if the app is ready to receive traffic.
    """
    # For readiness, we just check if the app is initialized
    # Health check handles dependency verification
    return jsonify({
        'status': 'ready',
        'timestamp': datetime.utcnow().isoformat() + 'Z'
    }), 200


# =============================================================================
# API Endpoints
# =============================================================================

@app.route('/api/status')
@login_required
def api_status():
    """API endpoint for checking provisioning status"""
    customer = Customer.get_by_id(current_user.id)
    return jsonify({
        'status': customer.status,
        'domain': customer.domain,
        'platform': customer.platform,
        'error_message': customer.error_message
    })


@app.route('/api/container/status')
@login_required
@limiter.limit("60 per minute")  # Allow frequent polling
def api_container_status():
    """Get container status for current customer"""
    import subprocess

    customer = Customer.get_by_id(current_user.id)

    if not customer:
        return jsonify({'error': 'Customer not found', 'status': 'error', 'running': False}), 404

    if customer.status != 'active':
        return jsonify({'error': 'Store not active', 'status': 'error', 'running': False}), 400

    # Container name follows pattern: customer-{id}-web
    container_name = f"customer-{customer.id}-web"

    try:
        # Get container status
        result = subprocess.run(
            ['docker', 'inspect', container_name, '--format',
             '{{.State.Status}} {{.State.Running}} {{.State.StartedAt}}'],
            capture_output=True, text=True, timeout=10
        )

        if result.returncode != 0:
            return jsonify({
                'status': 'unknown',
                'running': False,
                'uptime': None,
                'message': 'Container not found'
            })

        parts = result.stdout.strip().split()
        status = parts[0] if parts else 'unknown'
        running = parts[1].lower() == 'true' if len(parts) > 1 else False
        started_at = parts[2] if len(parts) > 2 else None

        # Calculate uptime
        uptime_str = None
        if started_at and running:
            from datetime import datetime
            try:
                # Docker returns ISO format with timezone
                started = datetime.fromisoformat(started_at.replace('Z', '+00:00'))
                now = datetime.now(started.tzinfo)
                delta = now - started
                days = delta.days
                hours, remainder = divmod(delta.seconds, 3600)
                minutes, _ = divmod(remainder, 60)
                uptime_str = f"{days}d {hours}h {minutes}m"
            except:
                uptime_str = "unknown"

        return jsonify({
            'status': status,
            'running': running,
            'uptime': uptime_str,
            'container_name': container_name
        })

    except subprocess.TimeoutExpired:
        return jsonify({'status': 'timeout', 'running': False, 'uptime': None}), 504
    except Exception as e:
        return jsonify({'status': 'error', 'running': False, 'message': str(e)}), 500


@app.route('/api/container/restart', methods=['POST'])
@login_required
@limiter.limit("1 per 5 minutes", error_message="Please wait 5 minutes between restarts.")
@csrf.exempt
def api_container_restart():
    """Restart container for current customer"""
    import subprocess

    customer = Customer.get_by_id(current_user.id)

    if not customer:
        return jsonify({'success': False, 'message': 'Customer not found'}), 404

    if customer.status != 'active':
        return jsonify({'success': False, 'message': 'Store not active'}), 400

    # Container name follows pattern: customer-{id}-web
    container_name = f"customer-{customer.id}-web"

    try:
        # Restart the container
        result = subprocess.run(
            ['docker', 'restart', container_name],
            capture_output=True, text=True, timeout=60
        )

        if result.returncode != 0:
            return jsonify({
                'success': False,
                'message': f'Restart failed: {result.stderr}'
            }), 500

        return jsonify({
            'success': True,
            'message': 'Container restart initiated. Your store will be back online in ~30 seconds.'
        })

    except subprocess.TimeoutExpired:
        return jsonify({
            'success': False,
            'message': 'Restart timed out. Please try again or contact support.'
        }), 504
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Error: {str(e)}'
        }), 500


@app.route('/api/container/logs')
@login_required
@limiter.limit("30 per minute")
def api_container_logs():
    """Get recent container logs for current customer"""
    import subprocess
    import re

    customer = Customer.get_by_id(current_user.id)

    if not customer:
        return jsonify({'error': 'Customer not found', 'logs': []}), 404

    if customer.status != 'active':
        return jsonify({'error': 'Store not active', 'logs': []}), 400

    # Container name follows pattern: customer-{id}-web
    container_name = f"customer-{customer.id}-web"

    lines = request.args.get('lines', 50, type=int)
    lines = min(lines, 100)  # Cap at 100 lines

    try:
        result = subprocess.run(
            ['docker', 'logs', container_name, '--tail', str(lines), '--timestamps'],
            capture_output=True, text=True, timeout=30
        )

        # Combine stdout and stderr (logs can be in either)
        logs = result.stdout + result.stderr

        # Sanitize sensitive data
        patterns_to_redact = [
            (r'password["\s:=]+[^\s"]+', 'password=***REDACTED***'),
            (r'api[_-]?key["\s:=]+[^\s"]+', 'api_key=***REDACTED***'),
            (r'secret["\s:=]+[^\s"]+', 'secret=***REDACTED***'),
            (r'token["\s:=]+[^\s"]+', 'token=***REDACTED***'),
            (r'Authorization:\s*\S+', 'Authorization: ***REDACTED***'),
        ]

        for pattern, replacement in patterns_to_redact:
            logs = re.sub(pattern, replacement, logs, flags=re.IGNORECASE)

        # Split into lines and return
        log_lines = logs.strip().split('\n') if logs.strip() else []

        return jsonify({
            'logs': log_lines,
            'container_name': container_name,
            'line_count': len(log_lines)
        })

    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Log retrieval timed out'}), 504
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/credentials')
@login_required
def api_credentials():
    """API endpoint for getting store credentials"""
    customer = Customer.get_by_id(current_user.id)

    if customer.status != 'active':
        return jsonify({'error': 'Store not yet active'}), 400

    credentials = customer.get_credentials()
    return jsonify(credentials)


@app.route('/api/backup', methods=['POST'])
@login_required
@limiter.limit("3 per hour", error_message="Too many backup requests. Please try again later.")
@csrf.exempt
def api_backup():
    """API endpoint for triggering a customer backup"""
    import subprocess
    import os

    customer = Customer.get_by_id(current_user.id)

    if customer.status != 'active':
        return jsonify({'error': 'Store not active, cannot backup'}), 400

    customer_dir = f"/var/customers/customer-{customer.id}"
    if not os.path.exists(customer_dir):
        return jsonify({'error': 'Customer directory not found'}), 404

    BACKUP_SCRIPT = "/opt/shophosting/scripts/customer-backup.sh"

    try:
        subprocess.Popen(
            ['sudo', BACKUP_SCRIPT, str(customer.id)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        # Audit log for backup operation
        security_logger.info(
            f"BACKUP_STARTED: customer={customer.id} email={customer.email} "
            f"domain={customer.domain} IP={request.remote_addr}"
        )

        return jsonify({
            'success': True,
            'message': 'Backup started. This may take a few minutes.',
            'note': 'Check back shortly for completion status.'
        })
    except Exception as e:
        logger.error(f"Failed to start backup for customer {customer.id}: {str(e)}")
        return jsonify({'error': 'Failed to start backup. Please try again later.'}), 500


@app.route('/api/backup/status')
@login_required
@csrf.exempt
def api_backup_status():
    """API endpoint for checking backup status and recent snapshots"""
    import subprocess
    import json
    
    customer = Customer.get_by_id(current_user.id)

    if customer.status != 'active':
        return jsonify({'error': 'Store not active'}), 400

    # Get backup configuration from environment
    restic_repo = os.getenv('RESTIC_REPOSITORY')
    restic_password_file = os.getenv('RESTIC_PASSWORD_FILE')

    if not restic_repo or not restic_password_file:
        logger.error("Backup configuration missing: RESTIC_REPOSITORY or RESTIC_PASSWORD_FILE not set")
        return jsonify({'error': 'Backup service not configured'}), 503

    try:
        result = subprocess.run(
            [
                'restic', 'snapshots', '--json',
                '--tag', f'customer-{customer.id}',
                '--latest', '20'
            ],
            capture_output=True, text=True,
            env={**os.environ, 'RESTIC_REPOSITORY': restic_repo,
                 'RESTIC_PASSWORD_FILE': restic_password_file},
            timeout=30  # Add timeout to prevent hanging
        )

        if result.returncode == 0:
            snapshots = json.loads(result.stdout) if result.stdout.strip() else []
            snapshot_list = []
            for snap in snapshots:
                snapshot_list.append({
                    'id': snap.get('id', '')[:8],
                    'short_id': snap.get('id', ''),
                    'time': snap.get('time', '').replace('T', ' ').replace('Z', ''),
                    'paths': snap.get('paths', [])
                })
            return jsonify({
                'success': True,
                'snapshots': snapshot_list
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Could not fetch backup status'
            })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/backup/restore', methods=['POST'])
@login_required
@csrf.exempt
def api_backup_restore():
    """API endpoint for restoring from a backup snapshot"""
    import subprocess
    import os

    customer = Customer.get_by_id(current_user.id)

    if customer.status != 'active':
        return jsonify({'error': 'Store not active'}), 400

    data = request.get_json() or {}
    snapshot_id = data.get('snapshot_id', '').strip()
    restore_target = data.get('target', 'all')  # db, files, or all

    if not snapshot_id:
        return jsonify({'error': 'Snapshot ID is required'}), 400

    # Validate snapshot_id format - restic uses hex strings (8-64 chars for short/full IDs)
    if not re.match(r'^[a-f0-9]{8,64}$', snapshot_id):
        security_logger.warning(
            f"Invalid snapshot_id format attempted: customer={customer.id} "
            f"snapshot_id={snapshot_id[:20]}... IP={request.remote_addr}"
        )
        return jsonify({'error': 'Invalid snapshot ID format'}), 400

    if restore_target not in ['db', 'files', 'all']:
        return jsonify({'error': 'Invalid restore target. Must be db, files, or all'}), 400

    RESTORE_SCRIPT = "/opt/shophosting/scripts/customer-restore.sh"

    try:
        subprocess.Popen(
            ['sudo', RESTORE_SCRIPT, str(customer.id), snapshot_id, restore_target],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        # Audit log for restore operation (critical operation)
        security_logger.warning(
            f"RESTORE_STARTED: customer={customer.id} email={customer.email} "
            f"domain={customer.domain} snapshot={snapshot_id} target={restore_target} "
            f"IP={request.remote_addr}"
        )

        target_labels = {
            'db': 'database only',
            'files': 'files only',
            'all': 'database and files'
        }

        return jsonify({
            'success': True,
            'message': f'Restore started. This will restore {target_labels[restore_target]}. Your store will be briefly unavailable during restore.',
            'note': 'The restore process is running in the background. Your store will be back shortly.'
        })
    except Exception as e:
        logger.error(f"Failed to start restore for customer {customer.id}: {str(e)}")
        return jsonify({'error': 'Failed to start restore. Please try again later.'}), 500


@app.route('/backup')
@login_required
def backup_page():
    """Customer backup management page"""
    customer = Customer.get_by_id(current_user.id)
    credentials = customer.get_credentials() if customer.status == 'active' else None

    return render_template('backup.html',
                          customer=customer,
                          credentials=credentials)


# =============================================================================
# Staging Environment Routes
# =============================================================================

@app.route('/staging')
@login_required
def staging_list():
    """List customer's staging environments"""
    customer = Customer.get_by_id(current_user.id)
    staging_envs = StagingEnvironment.get_by_customer(customer.id)
    can_create = StagingEnvironment.can_create_staging(customer.id)

    return render_template('staging.html',
                          customer=customer,
                          staging_envs=staging_envs,
                          can_create=can_create,
                          max_staging=StagingEnvironment.MAX_STAGING_PER_CUSTOMER)


@app.route('/staging/create', methods=['POST'])
@login_required
def staging_create():
    """Create a new staging environment"""
    customer = Customer.get_by_id(current_user.id)

    if customer.status != 'active':
        flash('Your production site must be active before creating staging environments.', 'error')
        return redirect(url_for('staging_list'))

    if not StagingEnvironment.can_create_staging(customer.id):
        flash(f'Maximum of {StagingEnvironment.MAX_STAGING_PER_CUSTOMER} staging environments allowed.', 'error')
        return redirect(url_for('staging_list'))

    staging_name = request.form.get('staging_name', '').strip()
    if not staging_name:
        staging_name = None  # Will auto-generate

    try:
        # Import and enqueue the staging creation job
        from redis import Redis
        from rq import Queue

        redis_host = os.getenv('REDIS_HOST', 'localhost')
        redis_conn = Redis(host=redis_host, port=6379)
        queue = Queue('staging', connection=redis_conn)

        # Enqueue the job
        from staging_worker import create_staging_job
        job = queue.enqueue(create_staging_job, customer.id, staging_name,
                           job_timeout=600, result_ttl=3600)

        flash('Staging environment is being created. This may take a few minutes.', 'success')
        logger.info(f"Staging creation queued for customer {customer.id}, job {job.id}")

    except Exception as e:
        logger.error(f"Failed to queue staging creation: {e}")
        flash('Failed to create staging environment. Please try again later.', 'error')

    return redirect(url_for('staging_list'))


@app.route('/staging/<int:staging_id>')
@login_required
def staging_detail(staging_id):
    """View staging environment details"""
    staging = StagingEnvironment.get_by_id(staging_id)

    if not staging or staging.customer_id != current_user.id:
        flash('Staging environment not found.', 'error')
        return redirect(url_for('staging_list'))

    customer = Customer.get_by_id(current_user.id)
    sync_history = staging.get_sync_history(limit=10)

    return render_template('staging_detail.html',
                          customer=customer,
                          staging=staging,
                          sync_history=sync_history)


@app.route('/staging/<int:staging_id>/push', methods=['POST'])
@login_required
def staging_push(staging_id):
    """Push staging changes to production"""
    staging = StagingEnvironment.get_by_id(staging_id)

    if not staging or staging.customer_id != current_user.id:
        return jsonify({'success': False, 'message': 'Staging environment not found'}), 404

    if staging.status != 'active':
        return jsonify({'success': False, 'message': 'Staging must be active to push'}), 400

    sync_type = request.form.get('sync_type', 'all')
    if sync_type not in ['files', 'db', 'all']:
        return jsonify({'success': False, 'message': 'Invalid sync type'}), 400

    try:
        from redis import Redis
        from rq import Queue

        redis_host = os.getenv('REDIS_HOST', 'localhost')
        redis_conn = Redis(host=redis_host, port=6379)
        queue = Queue('staging', connection=redis_conn)

        from staging_worker import push_to_production_job
        job = queue.enqueue(push_to_production_job, staging_id, sync_type,
                           job_timeout=600, result_ttl=3600)

        logger.info(f"Push to production queued for staging {staging_id}, type={sync_type}, job {job.id}")
        return jsonify({'success': True, 'message': 'Push to production started'})

    except Exception as e:
        logger.error(f"Failed to queue push to production: {e}")
        return jsonify({'success': False, 'message': 'Failed to start push'}), 500


@app.route('/staging/<int:staging_id>/delete', methods=['POST'])
@login_required
def staging_delete(staging_id):
    """Delete a staging environment"""
    staging = StagingEnvironment.get_by_id(staging_id)

    if not staging or staging.customer_id != current_user.id:
        flash('Staging environment not found.', 'error')
        return redirect(url_for('staging_list'))

    try:
        from redis import Redis
        from rq import Queue

        redis_host = os.getenv('REDIS_HOST', 'localhost')
        redis_conn = Redis(host=redis_host, port=6379)
        queue = Queue('staging', connection=redis_conn)

        from staging_worker import delete_staging_job
        job = queue.enqueue(delete_staging_job, staging_id,
                           job_timeout=300, result_ttl=3600)

        flash('Staging environment is being deleted.', 'success')
        logger.info(f"Staging deletion queued for staging {staging_id}, job {job.id}")

    except Exception as e:
        logger.error(f"Failed to queue staging deletion: {e}")
        flash('Failed to delete staging environment.', 'error')

    return redirect(url_for('staging_list'))


@app.route('/api/staging/<int:staging_id>/status')
@login_required
def staging_status(staging_id):
    """API endpoint for checking staging status"""
    staging = StagingEnvironment.get_by_id(staging_id)

    if not staging or staging.customer_id != current_user.id:
        return jsonify({'error': 'Not found'}), 404

    return jsonify(staging.to_dict())


# =============================================================================
# Support Ticket Routes
# =============================================================================

def save_ticket_attachment(file, ticket, customer_id=None, admin_id=None, message_id=None):
    """Save uploaded file and create attachment record with security validation"""
    if not file or file.filename == '':
        return None, "No file selected"

    if not TicketAttachment.allowed_file(file.filename):
        return None, "File type not allowed"

    # Check file size
    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(0)

    if size > TicketAttachment.MAX_FILE_SIZE:
        return None, "File too large (max 10MB)"

    # Validate file content using magic numbers (not just extension)
    try:
        import magic
        file_content = file.read(2048)  # Read first 2KB for magic number detection
        file.seek(0)  # Reset file pointer

        detected_mime = magic.from_buffer(file_content, mime=True)

        # Map of allowed extensions to their expected MIME types
        allowed_mimes = {
            'image/png': ['png'],
            'image/jpeg': ['jpg', 'jpeg'],
            'image/gif': ['gif'],
            'application/pdf': ['pdf'],
            'text/plain': ['txt'],
            'application/msword': ['doc'],
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ['docx'],
            'application/zip': ['zip'],
            'application/x-zip-compressed': ['zip'],
        }

        # Get claimed extension
        ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''

        # Check if detected MIME type is in allowed list
        if detected_mime not in allowed_mimes:
            security_logger.warning(
                f"File upload blocked - invalid MIME type: claimed_ext={ext} "
                f"detected_mime={detected_mime} filename={file.filename[:50]}"
            )
            return None, "File content does not match allowed types"

        # Verify extension matches detected type
        if ext not in allowed_mimes.get(detected_mime, []):
            security_logger.warning(
                f"File upload blocked - extension mismatch: claimed_ext={ext} "
                f"detected_mime={detected_mime} filename={file.filename[:50]}"
            )
            return None, "File extension does not match content type"

    except ImportError:
        # python-magic not installed, fall back to extension-only validation
        logger.warning("python-magic not installed, using extension-only validation")
    except Exception as e:
        logger.error(f"Error validating file type: {str(e)}")
        return None, "Error validating file type"

    # Generate unique filename
    original_filename = secure_filename(file.filename)
    unique_prefix = uuid.uuid4().hex[:8]
    filename = f"{unique_prefix}_{original_filename}"

    # Create directory structure
    now = datetime.now()
    relative_path = f"{now.year}/{now.month:02d}/{ticket.ticket_number}"
    full_dir = os.path.join(TICKET_UPLOAD_PATH, relative_path)
    os.makedirs(full_dir, exist_ok=True)

    # Save file
    file_path = os.path.join(relative_path, filename)
    full_path = os.path.join(TICKET_UPLOAD_PATH, file_path)
    file.save(full_path)

    # Set restrictive permissions on uploaded file (no execute)
    os.chmod(full_path, 0o644)

    # Get mime type from detection or filename
    try:
        import magic
        mime_type = magic.from_file(full_path, mime=True)
    except (ImportError, Exception):
        mime_type = mimetypes.guess_type(original_filename)[0] or 'application/octet-stream'

    # Create attachment record
    attachment = TicketAttachment(
        ticket_id=ticket.id,
        message_id=message_id,
        filename=filename,
        original_filename=original_filename,
        file_path=file_path,
        file_size=size,
        mime_type=mime_type,
        uploaded_by_customer_id=customer_id,
        uploaded_by_admin_id=admin_id
    )
    attachment.save()

    return attachment, None


@app.route('/support')
@login_required
def support_tickets():
    """List customer's support tickets"""
    status_filter = request.args.get('status')
    page = request.args.get('page', 1, type=int)

    tickets, total = Ticket.get_by_customer(current_user.id, status=status_filter, page=page)
    total_pages = (total + 19) // 20

    return render_template('support/tickets.html',
                          tickets=tickets,
                          total=total,
                          page=page,
                          total_pages=total_pages,
                          status_filter=status_filter)


@app.route('/support/new', methods=['GET', 'POST'])
@login_required
def create_ticket():
    """Create new support ticket"""
    form = CreateTicketForm()

    # Populate category choices
    categories = TicketCategory.get_all_active()
    form.category.choices = [(c.id, c.name) for c in categories]

    if form.validate_on_submit():
        try:
            # Create ticket
            ticket = Ticket(
                customer_id=current_user.id,
                category_id=form.category.data,
                subject=form.subject.data.strip(),
                status='open',
                priority='medium'
            )
            ticket.save()

            # Create initial message
            message = TicketMessage(
                ticket_id=ticket.id,
                customer_id=current_user.id,
                message=form.message.data.strip()
            )
            message.save()

            # Handle file attachment
            if 'attachment' in request.files:
                file = request.files['attachment']
                if file and file.filename:
                    attachment, error = save_ticket_attachment(
                        file, ticket,
                        customer_id=current_user.id,
                        message_id=message.id
                    )
                    if error:
                        flash(f'Ticket created but attachment failed: {error}', 'warning')
                    else:
                        logger.info(f"Attachment saved for ticket {ticket.ticket_number}")

            logger.info(f"New ticket created: {ticket.ticket_number} by customer {current_user.email}")
            flash(f'Ticket {ticket.ticket_number} created successfully!', 'success')
            return redirect(url_for('view_ticket', ticket_number=ticket.ticket_number))

        except Exception as e:
            logger.error(f"Error creating ticket: {e}")
            flash('An error occurred while creating the ticket. Please try again.', 'error')

    return render_template('support/create_ticket.html', form=form, categories=categories)


@app.route('/support/<ticket_number>')
@login_required
def view_ticket(ticket_number):
    """View ticket details and messages"""
    ticket = Ticket.get_by_ticket_number(ticket_number)

    if not ticket:
        flash('Ticket not found.', 'error')
        return redirect(url_for('support_tickets'))

    # Verify ownership
    if ticket.customer_id != current_user.id:
        flash('Access denied.', 'error')
        return redirect(url_for('support_tickets'))

    # Get messages (excluding internal notes)
    messages = ticket.get_messages(include_internal=False)

    # Get attachments
    attachments = ticket.get_attachments()

    # Get category
    category = TicketCategory.get_by_id(ticket.category_id) if ticket.category_id else None

    form = TicketReplyForm()

    return render_template('support/view_ticket.html',
                          ticket=ticket,
                          messages=messages,
                          attachments=attachments,
                          category=category,
                          form=form)


@app.route('/support/<ticket_number>/reply', methods=['POST'])
@login_required
def reply_ticket(ticket_number):
    """Add reply to ticket"""
    ticket = Ticket.get_by_ticket_number(ticket_number)

    if not ticket:
        flash('Ticket not found.', 'error')
        return redirect(url_for('support_tickets'))

    # Verify ownership
    if ticket.customer_id != current_user.id:
        flash('Access denied.', 'error')
        return redirect(url_for('support_tickets'))

    # Check if ticket is closed
    if ticket.status == 'closed':
        flash('Cannot reply to a closed ticket.', 'error')
        return redirect(url_for('view_ticket', ticket_number=ticket_number))

    form = TicketReplyForm()

    if form.validate_on_submit():
        try:
            # Create message
            message = TicketMessage(
                ticket_id=ticket.id,
                customer_id=current_user.id,
                message=form.message.data.strip()
            )
            message.save()

            # Update ticket status if it was waiting for customer
            if ticket.status == 'waiting_customer':
                ticket.status = 'open'
                ticket.save()

            # Handle file attachment
            if 'attachment' in request.files:
                file = request.files['attachment']
                if file and file.filename:
                    attachment, error = save_ticket_attachment(
                        file, ticket,
                        customer_id=current_user.id,
                        message_id=message.id
                    )
                    if error:
                        flash(f'Reply sent but attachment failed: {error}', 'warning')

            logger.info(f"Reply added to ticket {ticket.ticket_number} by customer {current_user.email}")
            flash('Reply sent successfully!', 'success')

        except Exception as e:
            logger.error(f"Error adding reply to ticket: {e}")
            flash('An error occurred. Please try again.', 'error')

    return redirect(url_for('view_ticket', ticket_number=ticket_number))


@app.route('/support/attachment/<int:attachment_id>')
@login_required
def serve_attachment(attachment_id):
    """Serve attachment file with access control"""
    attachment = TicketAttachment.get_by_id(attachment_id)

    if not attachment:
        abort(404)

    # Verify access - customer can only access their own ticket attachments
    ticket = Ticket.get_by_id(attachment.ticket_id)
    if not ticket or ticket.customer_id != current_user.id:
        abort(403)

    full_path = os.path.join(TICKET_UPLOAD_PATH, attachment.file_path)

    if not os.path.exists(full_path):
        abort(404)

    return send_file(
        full_path,
        download_name=attachment.original_filename,
        as_attachment=True
    )


# =============================================================================
# Customer Backup Routes
# =============================================================================

@app.route('/backups')
@login_required
def backups():
    """Customer backups page"""
    customer = Customer.get_by_id(current_user.id)
    active_job = CustomerBackupJob.get_active_job(customer.id)
    recent_jobs = CustomerBackupJob.get_recent_jobs(customer.id, limit=5)

    # Get manual backups from restic
    manual_backups = get_customer_manual_backups(customer.id)

    # Get daily backups (filtered to this customer's data)
    daily_backups = get_customer_daily_backups(customer.id)

    return render_template('backups.html',
                          customer=customer,
                          active_job=active_job,
                          recent_jobs=recent_jobs,
                          manual_backups=manual_backups,
                          daily_backups=daily_backups)


@app.route('/backups/create', methods=['POST'])
@login_required
def backup_create():
    """Create a manual backup"""
    customer = Customer.get_by_id(current_user.id)

    # Check for active job
    active_job = CustomerBackupJob.get_active_job(customer.id)
    if active_job:
        return jsonify({
            'success': False,
            'message': 'A backup operation is already in progress'
        }), 400

    backup_type = request.form.get('backup_type', 'both')
    if backup_type not in ('db', 'files', 'both'):
        return jsonify({'success': False, 'message': 'Invalid backup type'}), 400

    try:
        # Create job record
        job = CustomerBackupJob(
            customer_id=customer.id,
            job_type='backup',
            backup_type=backup_type,
            status='pending'
        )
        job.save()

        # Queue the job
        from redis import Redis
        from rq import Queue

        redis_host = os.getenv('REDIS_HOST', 'localhost')
        redis_conn = Redis(host=redis_host, port=6379)
        queue = Queue('backups', connection=redis_conn)

        from backup_worker import create_backup_job
        queue.enqueue(create_backup_job, job.id, job_timeout=700)

        logger.info(f"Backup job {job.id} queued for customer {customer.id}")
        return jsonify({'success': True, 'message': 'Backup started', 'job_id': job.id})

    except Exception as e:
        logger.error(f"Failed to queue backup: {e}")
        return jsonify({'success': False, 'message': 'Failed to start backup'}), 500


@app.route('/backups/<snapshot_id>/restore', methods=['POST'])
@login_required
def backup_restore(snapshot_id):
    """Restore from a backup"""
    customer = Customer.get_by_id(current_user.id)

    # Check for active job
    active_job = CustomerBackupJob.get_active_job(customer.id)
    if active_job:
        return jsonify({
            'success': False,
            'message': 'A backup operation is already in progress'
        }), 400

    # Verify confirmation
    confirmation = request.form.get('confirmation', '')
    if confirmation != 'RESTORE':
        return jsonify({
            'success': False,
            'message': 'Please type RESTORE to confirm'
        }), 400

    restore_type = request.form.get('restore_type', 'both')
    if restore_type not in ('db', 'files', 'both'):
        return jsonify({'success': False, 'message': 'Invalid restore type'}), 400

    try:
        # Create job record
        job = CustomerBackupJob(
            customer_id=customer.id,
            job_type='restore',
            backup_type=restore_type,
            snapshot_id=snapshot_id,
            status='pending'
        )
        job.save()

        # Queue the job
        from redis import Redis
        from rq import Queue

        redis_host = os.getenv('REDIS_HOST', 'localhost')
        redis_conn = Redis(host=redis_host, port=6379)
        queue = Queue('backups', connection=redis_conn)

        from backup_worker import restore_backup_job
        queue.enqueue(restore_backup_job, job.id, job_timeout=1300)

        logger.info(f"Restore job {job.id} queued for customer {customer.id}")
        return jsonify({'success': True, 'message': 'Restore started', 'job_id': job.id})

    except Exception as e:
        logger.error(f"Failed to queue restore: {e}")
        return jsonify({'success': False, 'message': 'Failed to start restore'}), 500


@app.route('/api/backups/status')
@login_required
def backup_status():
    """Get current backup job status"""
    customer = Customer.get_by_id(current_user.id)
    active_job = CustomerBackupJob.get_active_job(customer.id)

    if active_job:
        return jsonify({
            'has_active_job': True,
            'job': active_job.to_dict()
        })
    else:
        # Check for recently failed job to show error
        recent_jobs = CustomerBackupJob.get_recent_jobs(customer.id, limit=1)
        last_failed = None
        if recent_jobs and recent_jobs[0].status == 'failed':
            last_failed = recent_jobs[0].to_dict()

        return jsonify({
            'has_active_job': False,
            'last_failed': last_failed
        })


def get_customer_manual_backups(customer_id, limit=5):
    """Get manual backups for a customer from restic"""
    import subprocess
    try:
        result = subprocess.run(
            ['sudo', 'bash', '-c',
             f'export RESTIC_REPOSITORY="sftp:sh-backup@15.204.249.219:/home/sh-backup/manual-backups" && '
             f'export RESTIC_PASSWORD_FILE="/opt/shophosting/.manual-restic-password" && '
             f'export HOME=/root && '
             f'restic snapshots --json --tag "customer-{customer_id}" --tag "manual"'],
            capture_output=True,
            text=True,
            timeout=30
        )

        logger.info(f"Manual backup list for customer {customer_id}: returncode={result.returncode}, stdout_len={len(result.stdout)}, stderr={result.stderr[:200] if result.stderr else 'none'}")

        if result.returncode == 0 and result.stdout.strip():
            import json
            snapshots = json.loads(result.stdout)
            # Sort by time descending and limit
            snapshots.sort(key=lambda x: x.get('time', ''), reverse=True)
            return snapshots[:limit]
    except Exception as e:
        logger.error(f"Error fetching manual backups: {e}")

    return []


def get_customer_daily_backups(customer_id, limit=10):
    """Get daily backups that contain this customer's data"""
    import subprocess
    try:
        result = subprocess.run(
            ['sudo', 'bash', '-c',
             f'export RESTIC_REPOSITORY="sftp:sh-backup@15.204.249.219:/home/sh-backup/backups" && '
             f'export RESTIC_PASSWORD_FILE="/root/.restic-password" && '
             f'export HOME=/root && '
             f'restic snapshots --json --tag "daily"'],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode == 0 and result.stdout.strip():
            import json
            snapshots = json.loads(result.stdout)
            # Filter to snapshots that have customer path, sort descending
            customer_path = f"/var/customers/customer-{customer_id}"
            filtered = [s for s in snapshots if any(customer_path in p for p in s.get('paths', []))]
            filtered.sort(key=lambda x: x.get('time', ''), reverse=True)
            return filtered[:limit]
    except Exception as e:
        logger.error(f"Error fetching daily backups: {e}")

    return []


# =============================================================================
# Error Handlers
# =============================================================================

@app.errorhandler(404)
def not_found_error(error):
    """Handle 404 errors"""
    return render_template('errors/404.html'), 404


@app.errorhandler(500)
def internal_error(error):
    """Handle 500 errors"""
    logger.error(f"Internal server error: {error}")
    return render_template('errors/500.html'), 500


@app.errorhandler(CSRFError)
def handle_csrf_error(error):
    """Handle CSRF token errors gracefully"""
    flash('Your session has expired. Please try again.', 'info')
    # Redirect back to the referring page, or home if no referrer
    referrer = request.referrer
    if referrer:
        return redirect(referrer)
    return redirect(url_for('home'))


# =============================================================================
# Template Context
# =============================================================================

@app.context_processor
def inject_now():
    """Inject current datetime into templates"""
    return {'now': datetime.now()}


# =============================================================================
# Main Entry Point
# =============================================================================

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=os.getenv('FLASK_DEBUG', 'False').lower() == 'true')
