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
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session, send_file, abort
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_wtf import FlaskForm
from flask_wtf.csrf import CSRFProtect
from wtforms import StringField, PasswordField, SelectField, SubmitField, HiddenField, TextAreaField
from werkzeug.utils import secure_filename
from wtforms.validators import DataRequired, Email, Length, EqualTo, ValidationError
from dotenv import load_dotenv

# Add provisioning module to path
sys.path.insert(0, '/opt/shophosting/provisioning')

from models import Customer, PortManager, PricingPlan, Subscription, Invoice, init_db_pool
from models import Ticket, TicketMessage, TicketAttachment, TicketCategory
from enqueue_provisioning import ProvisioningQueue
from stripe_integration import init_stripe, create_checkout_session, process_webhook, create_portal_session
from stripe_integration.checkout import get_checkout_session

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

# Register admin blueprint
from admin import admin_bp
app.register_blueprint(admin_bp, url_prefix='/admin')


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
def contact_submit():
    """Handle contact form submission"""
    name = request.form.get('name')
    email = request.form.get('email')
    subject = request.form.get('subject')
    website = request.form.get('website', '')
    message = request.form.get('message')

    # Log the contact form submission
    logger.info(f"Contact form submission: {name} ({email}) - Subject: {subject}")

    # TODO: Send email notification to support team
    # For now, just flash a success message
    flash('Thanks for reaching out! We\'ll get back to you within one business day.', 'success')
    return redirect(url_for('contact'))


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


@app.route('/api/backup', methods=['POST'])
@login_required
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
    BACKUP_LOG = "/var/log/shophosting-customer-backup.log"

    try:
        subprocess.Popen(
            [BACKUP_SCRIPT, str(customer.id)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        return jsonify({
            'success': True,
            'message': 'Backup started. This may take a few minutes.',
            'note': 'Check back shortly for completion status.'
        })
    except Exception as e:
        return jsonify({'error': f'Failed to start backup: {str(e)}'}), 500


@app.route('/api/backup/status')
@login_required
def api_backup_status():
    """API endpoint for checking backup status and recent snapshots"""
    import subprocess
    import json
    
    customer = Customer.get_by_id(current_user.id)

    if customer.status != 'active':
        return jsonify({'error': 'Store not active'}), 400

    try:
        result = subprocess.run(
            [
                'restic', 'snapshots', '--json',
                '--tag', f'customer-{customer.id}',
                '--latest', '20'
            ],
            capture_output=True, text=True,
            env={**os.environ, 'RESTIC_REPOSITORY': 'sftp:sh-backup@15.204.249.219:/home/sh-backup/backups',
                 'RESTIC_PASSWORD_FILE': '/root/.restic-password'}
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

    if restore_target not in ['db', 'files', 'all']:
        return jsonify({'error': 'Invalid restore target. Must be db, files, or all'}), 400

    RESTORE_SCRIPT = "/opt/shophosting/scripts/customer-restore.sh"
    BACKUP_LOG = "/var/log/shophosting-customer-restore.log"

    try:
        subprocess.Popen(
            [RESTORE_SCRIPT, str(customer.id), snapshot_id, restore_target],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
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
        return jsonify({'error': f'Failed to start restore: {str(e)}'}), 500


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
# Support Ticket Routes
# =============================================================================

def save_ticket_attachment(file, ticket, customer_id=None, admin_id=None, message_id=None):
    """Save uploaded file and create attachment record"""
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

    # Get mime type
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
