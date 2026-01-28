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
