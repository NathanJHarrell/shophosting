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

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_wtf import FlaskForm
from flask_wtf.csrf import CSRFProtect
from wtforms import StringField, PasswordField, SelectField, SubmitField, HiddenField
from wtforms.validators import DataRequired, Email, Length, EqualTo, ValidationError
from dotenv import load_dotenv

# Add provisioning module to path
sys.path.insert(0, '/opt/shophosting.io/provisioning')

from models import Customer, PortManager, PricingPlan, Subscription, Invoice, init_db_pool
from enqueue_provisioning import ProvisioningQueue
from stripe_integration import init_stripe, create_checkout_session, process_webhook, create_portal_session
from stripe_integration.checkout import get_checkout_session

# Load environment variables
load_dotenv('/opt/shophosting.io/.env')

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/opt/shophosting.io/logs/webapp.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'change-this-in-production-use-a-real-secret')
app.config['WTF_CSRF_ENABLED'] = True

# Initialize extensions
csrf = CSRFProtect(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'

# Initialize database pool
init_db_pool()

# Initialize Stripe
init_stripe()


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


@app.route('/signup', methods=['GET', 'POST'])
@app.route('/signup/<plan_slug>', methods=['GET', 'POST'])
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
def login():
    """Customer login page"""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    form = LoginForm()

    if form.validate_on_submit():
        customer = Customer.get_by_email(form.email.data.lower().strip())

        if customer and customer.check_password(form.password.data):
            login_user(customer)
            logger.info(f"Customer login: {customer.email}")

            next_page = request.args.get('next')
            if next_page:
                return redirect(next_page)
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid email or password', 'error')

    return render_template('login.html', form=form)


@app.route('/logout')
@login_required
def logout():
    """Log out customer"""
    logger.info(f"Customer logout: {current_user.email}")
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))


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
    """Customer dashboard"""
    # Refresh customer data from database
    customer = Customer.get_by_id(current_user.id)
    credentials = customer.get_credentials()

    return render_template('dashboard.html',
                          customer=customer,
                          credentials=credentials)


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


@app.route('/api/credentials')
@login_required
def api_credentials():
    """API endpoint for getting store credentials"""
    customer = Customer.get_by_id(current_user.id)

    if customer.status != 'active':
        return jsonify({'error': 'Store not yet active'}), 400

    credentials = customer.get_credentials()
    return jsonify(credentials)


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
