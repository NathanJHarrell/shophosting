"""
ShopHosting.io - Email Utilities
Handles sending transactional emails for notifications
"""

import os
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

logger = logging.getLogger(__name__)


class EmailConfig:
    """Email configuration from environment variables"""

    SMTP_HOST = os.getenv('SMTP_HOST', 'localhost')
    SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))
    SMTP_USER = os.getenv('SMTP_USER', '')
    SMTP_PASSWORD = os.getenv('SMTP_PASSWORD', '')
    SMTP_USE_TLS = os.getenv('SMTP_USE_TLS', 'true').lower() == 'true'
    FROM_EMAIL = os.getenv('FROM_EMAIL', 'noreply@shophosting.io')
    FROM_NAME = os.getenv('FROM_NAME', 'ShopHosting.io')
    SUPPORT_EMAIL = os.getenv('SUPPORT_EMAIL', 'support@shophosting.io')
    SALES_EMAIL = os.getenv('SALES_EMAIL', 'sales@shophosting.io')

    # Feature flag for email sending (disable in dev/test)
    EMAILS_ENABLED = os.getenv('EMAILS_ENABLED', 'false').lower() == 'true'


def send_email(to_email, subject, html_body, text_body=None):
    """
    Send an email using SMTP.

    Args:
        to_email: Recipient email address
        subject: Email subject
        html_body: HTML content of the email
        text_body: Plain text fallback (optional, will be derived from html if not provided)

    Returns:
        tuple: (success: bool, message: str)
    """
    if not EmailConfig.EMAILS_ENABLED:
        logger.info(f"Email sending disabled. Would have sent to {to_email}: {subject}")
        return True, "Email sending disabled (logged only)"

    if not text_body:
        # Simple HTML to text conversion
        import re
        text_body = re.sub('<[^<]+?>', '', html_body)

    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = f"{EmailConfig.FROM_NAME} <{EmailConfig.FROM_EMAIL}>"
        msg['To'] = to_email

        # Attach both plain text and HTML versions
        part1 = MIMEText(text_body, 'plain')
        part2 = MIMEText(html_body, 'html')
        msg.attach(part1)
        msg.attach(part2)

        # Connect and send
        if EmailConfig.SMTP_USE_TLS:
            server = smtplib.SMTP(EmailConfig.SMTP_HOST, EmailConfig.SMTP_PORT)
            server.starttls()
        else:
            server = smtplib.SMTP(EmailConfig.SMTP_HOST, EmailConfig.SMTP_PORT)

        if EmailConfig.SMTP_USER and EmailConfig.SMTP_PASSWORD:
            server.login(EmailConfig.SMTP_USER, EmailConfig.SMTP_PASSWORD)

        server.sendmail(EmailConfig.FROM_EMAIL, to_email, msg.as_string())
        server.quit()

        logger.info(f"Email sent successfully to {to_email}: {subject}")
        return True, "Email sent successfully"

    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {str(e)}")
        return False, f"Failed to send email: {str(e)}"


def send_contact_notification(name, email, subject, website, message):
    """
    Send notification to support team about new contact form submission.

    Args:
        name: Contact's name
        email: Contact's email
        subject: Contact subject
        website: Contact's website (optional)
        message: Contact message
    """
    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6;">
        <h2 style="color: #333;">New Contact Form Submission</h2>
        <table style="border-collapse: collapse; width: 100%; max-width: 600px;">
            <tr>
                <td style="padding: 10px; border-bottom: 1px solid #ddd;"><strong>Name:</strong></td>
                <td style="padding: 10px; border-bottom: 1px solid #ddd;">{name}</td>
            </tr>
            <tr>
                <td style="padding: 10px; border-bottom: 1px solid #ddd;"><strong>Email:</strong></td>
                <td style="padding: 10px; border-bottom: 1px solid #ddd;"><a href="mailto:{email}">{email}</a></td>
            </tr>
            <tr>
                <td style="padding: 10px; border-bottom: 1px solid #ddd;"><strong>Subject:</strong></td>
                <td style="padding: 10px; border-bottom: 1px solid #ddd;">{subject}</td>
            </tr>
            <tr>
                <td style="padding: 10px; border-bottom: 1px solid #ddd;"><strong>Website:</strong></td>
                <td style="padding: 10px; border-bottom: 1px solid #ddd;">{website or 'Not provided'}</td>
            </tr>
        </table>
        <h3 style="color: #333; margin-top: 20px;">Message:</h3>
        <div style="background: #f5f5f5; padding: 15px; border-radius: 5px;">
            {message.replace(chr(10), '<br>')}
        </div>
        <p style="color: #666; font-size: 12px; margin-top: 20px;">
            Received at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC
        </p>
    </body>
    </html>
    """

    return send_email(
        EmailConfig.SUPPORT_EMAIL,
        f"[Contact Form] {subject}",
        html_body
    )


def send_consultation_confirmation(appointment):
    """
    Send confirmation email to prospect about their scheduled consultation.

    Args:
        appointment: ConsultationAppointment object
    """
    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6;">
        <h2 style="color: #2563eb;">Your Consultation is Scheduled!</h2>
        <p>Hi {appointment.first_name},</p>
        <p>Thank you for scheduling a consultation with ShopHosting.io. We're excited to learn more about your e-commerce needs!</p>

        <div style="background: #f0f9ff; padding: 20px; border-radius: 8px; margin: 20px 0;">
            <h3 style="margin-top: 0; color: #1e40af;">Appointment Details</h3>
            <p><strong>Date:</strong> {appointment.scheduled_date}</p>
            <p><strong>Time:</strong> {appointment.scheduled_time} {appointment.timezone}</p>
        </div>

        <p>One of our e-commerce specialists will call you at the scheduled time. Please make sure you're available at the phone number you provided: <strong>{appointment.phone}</strong></p>

        <h3 style="color: #333;">What to Expect</h3>
        <ul>
            <li>A 15-30 minute discussion about your e-commerce goals</li>
            <li>Recommendations for the best platform (WooCommerce or Magento)</li>
            <li>Pricing and timeline information</li>
            <li>Answers to any questions you have</li>
        </ul>

        <p>If you need to reschedule, please reply to this email or contact us at <a href="mailto:{EmailConfig.SUPPORT_EMAIL}">{EmailConfig.SUPPORT_EMAIL}</a>.</p>

        <p>We look forward to speaking with you!</p>

        <p>Best regards,<br>
        The ShopHosting.io Team</p>

        <hr style="border: none; border-top: 1px solid #ddd; margin: 30px 0;">
        <p style="color: #666; font-size: 12px;">
            ShopHosting.io - Managed E-commerce Hosting<br>
            <a href="https://shophosting.io">https://shophosting.io</a>
        </p>
    </body>
    </html>
    """

    return send_email(
        appointment.email,
        "Your ShopHosting.io Consultation is Confirmed",
        html_body
    )


def send_monitoring_alert(customer, alert):
    """
    Send monitoring alert email to admin.

    Args:
        customer: Customer object
        alert: MonitoringAlert object

    Returns:
        tuple: (success: bool, message: str)
    """
    admin_email = os.getenv('ADMIN_ALERT_EMAIL', os.getenv('ADMIN_EMAIL'))
    if not admin_email:
        return False, "No admin alert email configured"

    # Map alert types to colors and emojis
    alert_config = {
        'down': {'color': '#ef4444', 'emoji': '&#128308;', 'bg': '#fef2f2'},
        'degraded': {'color': '#f59e0b', 'emoji': '&#128993;', 'bg': '#fffbeb'},
        'recovered': {'color': '#22c55e', 'emoji': '&#128994;', 'bg': '#f0fdf4'},
        'resource_warning': {'color': '#f59e0b', 'emoji': '&#9888;', 'bg': '#fffbeb'}
    }
    config = alert_config.get(alert.alert_type, alert_config['down'])

    subject = f"[{alert.alert_type.upper()}] {alert.message}"

    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; background: #f4f4f4; padding: 20px;">
        <div style="max-width: 600px; margin: 0 auto; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
            <div style="background: {config['color']}; color: white; padding: 20px; text-align: center;">
                <h1 style="margin: 0; font-size: 24px;">{config['emoji']} Monitoring Alert</h1>
            </div>
            <div style="padding: 30px;">
                <div style="background: {config['bg']}; border-left: 4px solid {config['color']}; padding: 15px; margin-bottom: 20px; border-radius: 4px;">
                    <strong style="font-size: 18px;">{alert.message}</strong>
                </div>

                <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px;">
                    <tr>
                        <td style="padding: 10px; border-bottom: 1px solid #eee; color: #666;">Customer</td>
                        <td style="padding: 10px; border-bottom: 1px solid #eee;"><strong>{customer.company_name or customer.email}</strong></td>
                    </tr>
                    <tr>
                        <td style="padding: 10px; border-bottom: 1px solid #eee; color: #666;">Domain</td>
                        <td style="padding: 10px; border-bottom: 1px solid #eee;"><a href="https://{customer.domain}" style="color: #0088ff;">{customer.domain}</a></td>
                    </tr>
                    <tr>
                        <td style="padding: 10px; border-bottom: 1px solid #eee; color: #666;">Alert Type</td>
                        <td style="padding: 10px; border-bottom: 1px solid #eee;"><span style="background: {config['bg']}; color: {config['color']}; padding: 4px 12px; border-radius: 12px; font-weight: 600; text-transform: uppercase; font-size: 12px;">{alert.alert_type}</span></td>
                    </tr>
                    <tr>
                        <td style="padding: 10px; border-bottom: 1px solid #eee; color: #666;">Time</td>
                        <td style="padding: 10px; border-bottom: 1px solid #eee;">{alert.created_at.strftime('%Y-%m-%d %H:%M:%S')} UTC</td>
                    </tr>
                </table>

                <div style="text-align: center; margin-top: 30px;">
                    <a href="https://shophosting.io/admin/monitoring/{customer.id}" style="display: inline-block; background: #0088ff; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-weight: 600;">View in Admin Panel</a>
                </div>
            </div>
            <div style="background: #f9f9f9; padding: 15px; text-align: center; color: #666; font-size: 12px;">
                ShopHosting.io Monitoring System
            </div>
        </div>
    </body>
    </html>
    """

    return send_email(admin_email, subject, html_body)


def send_consultation_notification_to_sales(appointment):
    """
    Send notification to sales team about new consultation booking.

    Args:
        appointment: ConsultationAppointment object
    """
    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6;">
        <h2 style="color: #059669;">New Consultation Scheduled</h2>

        <div style="background: #ecfdf5; padding: 20px; border-radius: 8px; margin: 20px 0;">
            <h3 style="margin-top: 0; color: #065f46;">Appointment #{appointment.id}</h3>
            <table style="border-collapse: collapse; width: 100%;">
                <tr>
                    <td style="padding: 8px 0;"><strong>Name:</strong></td>
                    <td style="padding: 8px 0;">{appointment.first_name} {appointment.last_name}</td>
                </tr>
                <tr>
                    <td style="padding: 8px 0;"><strong>Email:</strong></td>
                    <td style="padding: 8px 0;"><a href="mailto:{appointment.email}">{appointment.email}</a></td>
                </tr>
                <tr>
                    <td style="padding: 8px 0;"><strong>Phone:</strong></td>
                    <td style="padding: 8px 0;"><a href="tel:{appointment.phone}">{appointment.phone}</a></td>
                </tr>
                <tr>
                    <td style="padding: 8px 0;"><strong>Date:</strong></td>
                    <td style="padding: 8px 0;">{appointment.scheduled_date}</td>
                </tr>
                <tr>
                    <td style="padding: 8px 0;"><strong>Time:</strong></td>
                    <td style="padding: 8px 0;">{appointment.scheduled_time} {appointment.timezone}</td>
                </tr>
            </table>
        </div>

        <p><strong>Action Required:</strong> Please add this to your calendar and prepare for the call.</p>

        <p style="color: #666; font-size: 12px; margin-top: 20px;">
            Booking received at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC
        </p>
    </body>
    </html>
    """

    return send_email(
        EmailConfig.SALES_EMAIL,
        f"[New Consultation] {appointment.first_name} {appointment.last_name} - {appointment.scheduled_date}",
        html_body
    )


def send_resource_alert(customer, alert_type, resource_type, used_gb, limit_gb, percent):
    """
    Send resource limit alert email to customer.

    Args:
        customer: Customer object
        alert_type: 'warning' or 'critical'
        resource_type: 'disk' or 'bandwidth'
        used_gb: Current usage in GB
        limit_gb: Limit in GB
        percent: Usage percentage
    """
    resource_name = 'Disk Space' if resource_type == 'disk' else 'Monthly Bandwidth'

    if alert_type == 'warning':
        subject = f"Warning: {resource_name} at {percent}% - Action Recommended"
        urgency = "approaching"
        color = "#f59e0b"  # Warning orange
    else:
        subject = f"Critical: {resource_name} at {percent}% - Immediate Action Required"
        urgency = "nearly reached"
        color = "#ef4444"  # Critical red

    action_text = ""
    if resource_type == 'disk':
        action_text = """
        <p>To free up space, consider:</p>
        <ul>
            <li>Deleting unused media files</li>
            <li>Clearing old backups</li>
            <li>Removing unused plugins/themes</li>
        </ul>
        """
    else:
        action_text = """
        <p>High bandwidth usage may indicate:</p>
        <ul>
            <li>Increased traffic (great news!)</li>
            <li>Large file downloads</li>
            <li>Unoptimized images</li>
        </ul>
        """

    html_body = f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
        <div style="background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%); padding: 30px; border-radius: 12px; color: white;">
            <h1 style="margin: 0 0 10px 0; font-size: 24px;">{resource_name} Alert</h1>
            <p style="margin: 0; opacity: 0.8;">for {customer.domain}</p>
        </div>

        <div style="padding: 30px 0;">
            <div style="background: {color}15; border: 1px solid {color}40; border-radius: 8px; padding: 20px; margin-bottom: 20px;">
                <p style="margin: 0; color: {color}; font-weight: 600; font-size: 18px;">
                    You've {urgency} your {resource_name.lower()} limit
                </p>
            </div>

            <div style="background: #f8fafc; border-radius: 8px; padding: 20px; margin-bottom: 20px;">
                <div style="display: flex; justify-content: space-between; margin-bottom: 10px;">
                    <span style="color: #64748b;">Current Usage</span>
                    <span style="font-weight: 600;">{used_gb:.1f} GB / {limit_gb} GB ({percent}%)</span>
                </div>
                <div style="background: #e2e8f0; border-radius: 4px; height: 8px; overflow: hidden;">
                    <div style="background: {color}; height: 100%; width: {min(percent, 100)}%;"></div>
                </div>
            </div>

            {action_text}

            <p>Need more resources? <a href="https://shophosting.io/dashboard" style="color: #0088ff;">Upgrade your plan</a> for increased limits.</p>
        </div>

        <div style="border-top: 1px solid #e2e8f0; padding-top: 20px; color: #64748b; font-size: 14px;">
            <p>Questions? Contact us at <a href="mailto:support@shophosting.io" style="color: #0088ff;">support@shophosting.io</a></p>
        </div>
    </body>
    </html>
    """

    return send_email(customer.email, subject, html_body)


def send_2fa_recovery_email(to_email, recovery_code):
    """
    Send 2FA recovery code via email.

    Args:
        to_email: Customer's email address
        recovery_code: The 8-character recovery code

    Returns:
        tuple: (success: bool, message: str)
    """
    subject = "ShopHosting.io - Your 2FA Recovery Code"

    html_body = f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; background: #f4f4f5;">
        <div style="background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%); padding: 30px; border-radius: 12px 12px 0 0; text-align: center;">
            <h1 style="margin: 0; color: white; font-size: 24px;">Two-Factor Authentication</h1>
            <p style="margin: 10px 0 0 0; color: rgba(255,255,255,0.7);">Recovery Code Request</p>
        </div>

        <div style="background: white; padding: 30px; border-radius: 0 0 12px 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.1);">
            <p style="color: #374151; font-size: 16px; margin-bottom: 20px;">
                You requested to bypass two-factor authentication. Use the code below to complete your login:
            </p>

            <div style="background: #f0f9ff; border: 2px dashed #0ea5e9; border-radius: 8px; padding: 20px; text-align: center; margin: 25px 0;">
                <span style="font-family: 'Courier New', monospace; font-size: 32px; font-weight: 700; letter-spacing: 4px; color: #0369a1;">
                    {recovery_code}
                </span>
            </div>

            <div style="background: #fef3c7; border-left: 4px solid #f59e0b; padding: 15px; margin: 20px 0; border-radius: 4px;">
                <p style="margin: 0; color: #92400e; font-size: 14px;">
                    <strong>Security Notice:</strong> This code expires in 15 minutes. If you did not request this code, please secure your account immediately.
                </p>
            </div>

            <p style="color: #6b7280; font-size: 14px; margin-top: 20px;">
                For your security, this code can only be used once and will expire shortly. After logging in, we recommend reviewing your account security settings.
            </p>
        </div>

        <div style="text-align: center; padding: 20px; color: #9ca3af; font-size: 12px;">
            <p style="margin: 0;">This email was sent by ShopHosting.io</p>
            <p style="margin: 5px 0 0 0;">If you didn't request this, please ignore this email.</p>
        </div>
    </body>
    </html>
    """

    text_body = f"""
Two-Factor Authentication Recovery Code

You requested to bypass two-factor authentication. Use the code below to complete your login:

Recovery Code: {recovery_code}

IMPORTANT: This code expires in 15 minutes.

If you did not request this code, please secure your account immediately.

--
ShopHosting.io
"""

    return send_email(to_email, subject, html_body, text_body)
