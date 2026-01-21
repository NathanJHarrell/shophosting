"""
ShopHosting.io Email Service
Sends styled transactional emails using local Postfix or external SMTP
"""

import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger(__name__)


class EmailService:
    """Service for sending styled transactional emails"""

    def __init__(self):
        self.smtp_server = os.getenv('SMTP_SERVER', 'localhost')
        self.smtp_port = int(os.getenv('SMTP_PORT') or '25')
        self.smtp_user = os.getenv('SMTP_USER')
        self.smtp_password = os.getenv('SMTP_PASSWORD')
        self.from_email = os.getenv('SMTP_FROM', 'noreply@shophosting.io')
        self.from_name = 'ShopHosting.io'

    def _get_base_template(self, content: str, preview_text: str = '') -> str:
        """Generate styled HTML email template matching website design"""
        return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="X-UA-Compatible" content="IE=edge">
    <title>ShopHosting.io</title>
    <!--[if mso]>
    <style type="text/css">
        body, table, td {{font-family: Arial, sans-serif !important;}}
    </style>
    <![endif]-->
</head>
<body style="margin: 0; padding: 0; background-color: #08080a; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;">
    <!-- Preview text -->
    <div style="display: none; max-height: 0; overflow: hidden;">
        {preview_text}
    </div>

    <!-- Email wrapper -->
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background-color: #08080a;">
        <tr>
            <td align="center" style="padding: 40px 20px;">
                <!-- Main container -->
                <table role="presentation" width="600" cellspacing="0" cellpadding="0" border="0" style="max-width: 600px; width: 100%;">

                    <!-- Header with logo -->
                    <tr>
                        <td align="center" style="padding-bottom: 32px;">
                            <a href="https://shophosting.io" style="text-decoration: none;">
                                <span style="font-size: 28px; font-weight: 700; background: linear-gradient(135deg, #00d4ff 0%, #0088ff 50%, #5b5bd6 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text;">ShopHosting.io</span>
                            </a>
                        </td>
                    </tr>

                    <!-- Content card -->
                    <tr>
                        <td>
                            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background-color: #18181c; border-radius: 16px; border: 1px solid rgba(255, 255, 255, 0.06);">
                                <tr>
                                    <td style="padding: 40px;">
                                        {content}
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>

                    <!-- Footer -->
                    <tr>
                        <td align="center" style="padding-top: 32px;">
                            <p style="margin: 0; color: #71717a; font-size: 14px;">
                                &copy; 2026 ShopHosting.io. All rights reserved.
                            </p>
                            <p style="margin: 8px 0 0 0; color: #52525b; font-size: 13px;">
                                <a href="https://shophosting.io" style="color: #52525b; text-decoration: underline;">Website</a>
                                &nbsp;&nbsp;|&nbsp;&nbsp;
                                <a href="https://shophosting.io/support" style="color: #52525b; text-decoration: underline;">Support</a>
                            </p>
                        </td>
                    </tr>

                </table>
            </td>
        </tr>
    </table>
</body>
</html>'''

    def _send_email(self, to_email: str, subject: str, html_content: str, plain_content: str = None) -> bool:
        """Send an email with HTML and optional plain text content"""
        if not self.smtp_server:
            logger.warning("SMTP server not configured, skipping email")
            return False

        message = MIMEMultipart('alternative')
        message['From'] = f"{self.from_name} <{self.from_email}>"
        message['To'] = to_email
        message['Subject'] = subject

        # Add plain text version (fallback)
        if plain_content:
            message.attach(MIMEText(plain_content, 'plain'))

        # Add HTML version
        message.attach(MIMEText(html_content, 'html'))

        try:
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                if self.smtp_user and self.smtp_password:
                    server.starttls()
                    server.login(self.smtp_user, self.smtp_password)
                server.send_message(message)

            logger.info(f"Email sent to {to_email}: {subject}")
            return True

        except Exception as e:
            logger.error(f"Failed to send email to {to_email}: {e}")
            return False

    def send_welcome_email(self, to_email: str, domain: str, platform: str, admin_user: str, admin_password: str) -> bool:
        """Send welcome email when store is provisioned"""
        platform_title = platform.title()
        admin_url = f"https://{domain}/wp-admin" if platform == 'woocommerce' else f"https://{domain}/admin"

        content = f'''
            <h1 style="margin: 0 0 24px 0; color: #f4f4f6; font-size: 24px; font-weight: 600;">
                Your {platform_title} Store is Ready!
            </h1>

            <p style="margin: 0 0 24px 0; color: #a1a1aa; font-size: 16px; line-height: 1.6;">
                Great news! Your store has been successfully provisioned and is ready to use.
            </p>

            <!-- Store details card -->
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background-color: #111114; border-radius: 10px; margin-bottom: 24px;">
                <tr>
                    <td style="padding: 24px;">
                        <p style="margin: 0 0 16px 0; color: #71717a; font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; font-weight: 600;">Store Details</p>

                        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
                            <tr>
                                <td style="padding: 8px 0; color: #71717a; font-size: 14px; width: 120px;">Store URL</td>
                                <td style="padding: 8px 0;">
                                    <a href="https://{domain}" style="color: #0088ff; text-decoration: none; font-size: 14px;">https://{domain}</a>
                                </td>
                            </tr>
                            <tr>
                                <td style="padding: 8px 0; color: #71717a; font-size: 14px;">Admin URL</td>
                                <td style="padding: 8px 0;">
                                    <a href="{admin_url}" style="color: #0088ff; text-decoration: none; font-size: 14px;">{admin_url}</a>
                                </td>
                            </tr>
                            <tr>
                                <td style="padding: 8px 0; color: #71717a; font-size: 14px;">Username</td>
                                <td style="padding: 8px 0; color: #f4f4f6; font-size: 14px; font-family: monospace;">{admin_user}</td>
                            </tr>
                            <tr>
                                <td style="padding: 8px 0; color: #71717a; font-size: 14px;">Password</td>
                                <td style="padding: 8px 0; color: #f4f4f6; font-size: 14px; font-family: monospace;">{admin_password}</td>
                            </tr>
                        </table>
                    </td>
                </tr>
            </table>

            <!-- Warning box -->
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background-color: rgba(245, 158, 11, 0.1); border-radius: 10px; border: 1px solid rgba(245, 158, 11, 0.2); margin-bottom: 24px;">
                <tr>
                    <td style="padding: 16px 20px;">
                        <p style="margin: 0; color: #f59e0b; font-size: 14px;">
                            <strong>Important:</strong> Please change your password after your first login.
                        </p>
                    </td>
                </tr>
            </table>

            <!-- CTA Button -->
            <table role="presentation" cellspacing="0" cellpadding="0" border="0">
                <tr>
                    <td style="border-radius: 10px; background: linear-gradient(135deg, #00d4ff 0%, #0088ff 50%, #5b5bd6 100%);">
                        <a href="{admin_url}" style="display: inline-block; padding: 14px 28px; color: #08080a; text-decoration: none; font-weight: 600; font-size: 15px;">Access Your Store</a>
                    </td>
                </tr>
            </table>

            <p style="margin: 24px 0 0 0; color: #71717a; font-size: 14px;">
                If you have any questions, our support team is here to help.
            </p>
        '''

        plain_text = f"""Your {platform_title} Store is Ready!

Store URL: https://{domain}
Admin URL: {admin_url}
Username: {admin_user}
Password: {admin_password}

Important: Please change your password after your first login.

If you have any questions, contact support@shophosting.io

- ShopHosting.io Team
"""

        html = self._get_base_template(content, f"Your {platform_title} store is ready!")
        return self._send_email(to_email, f"Your {platform_title} Store is Ready!", html, plain_text)

    def send_payment_failed_email(self, to_email: str, domain: str, amount: float = None, invoice_url: str = None) -> bool:
        """Send payment failed notification"""
        amount_str = f"${amount / 100:.2f}" if amount else "your subscription"

        content = f'''
            <!-- Alert icon -->
            <div style="text-align: center; margin-bottom: 24px;">
                <div style="display: inline-block; width: 64px; height: 64px; background-color: rgba(239, 68, 68, 0.15); border-radius: 50%; line-height: 64px; font-size: 32px;">
                    ⚠️
                </div>
            </div>

            <h1 style="margin: 0 0 24px 0; color: #f4f4f6; font-size: 24px; font-weight: 600; text-align: center;">
                Payment Failed
            </h1>

            <p style="margin: 0 0 24px 0; color: #a1a1aa; font-size: 16px; line-height: 1.6; text-align: center;">
                We were unable to process your payment of <strong style="color: #f4f4f6;">{amount_str}</strong> for your store at <strong style="color: #f4f4f6;">{domain}</strong>.
            </p>

            <!-- Warning box -->
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background-color: rgba(239, 68, 68, 0.1); border-radius: 10px; border: 1px solid rgba(239, 68, 68, 0.2); margin-bottom: 24px;">
                <tr>
                    <td style="padding: 20px;">
                        <p style="margin: 0 0 8px 0; color: #ef4444; font-size: 14px; font-weight: 600;">
                            Action Required
                        </p>
                        <p style="margin: 0; color: #fca5a5; font-size: 14px; line-height: 1.5;">
                            Please update your payment method to avoid service interruption. Your store may be suspended if payment is not received within 7 days.
                        </p>
                    </td>
                </tr>
            </table>

            <!-- CTA Button -->
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
                <tr>
                    <td align="center">
                        <table role="presentation" cellspacing="0" cellpadding="0" border="0">
                            <tr>
                                <td style="border-radius: 10px; background: linear-gradient(135deg, #00d4ff 0%, #0088ff 50%, #5b5bd6 100%);">
                                    <a href="https://shophosting.io/billing" style="display: inline-block; padding: 14px 28px; color: #08080a; text-decoration: none; font-weight: 600; font-size: 15px;">Update Payment Method</a>
                                </td>
                            </tr>
                        </table>
                    </td>
                </tr>
            </table>

            {f'<p style="margin: 24px 0 0 0; color: #71717a; font-size: 14px; text-align: center;"><a href="{invoice_url}" style="color: #0088ff;">View Invoice</a></p>' if invoice_url else ''}

            <p style="margin: 24px 0 0 0; color: #71717a; font-size: 14px; text-align: center;">
                Need help? Contact our support team.
            </p>
        '''

        plain_text = f"""Payment Failed

We were unable to process your payment of {amount_str} for your store at {domain}.

Please update your payment method at https://shophosting.io/billing to avoid service interruption.

Your store may be suspended if payment is not received within 7 days.

{f'View Invoice: {invoice_url}' if invoice_url else ''}

Need help? Contact support@shophosting.io

- ShopHosting.io Team
"""

        html = self._get_base_template(content, f"Payment failed for {domain}")
        return self._send_email(to_email, "Action Required: Payment Failed", html, plain_text)

    def send_subscription_cancelled_email(self, to_email: str, domain: str, end_date: str = None) -> bool:
        """Send subscription cancellation confirmation"""
        end_date_str = end_date if end_date else "the end of your current billing period"

        content = f'''
            <h1 style="margin: 0 0 24px 0; color: #f4f4f6; font-size: 24px; font-weight: 600;">
                Subscription Cancelled
            </h1>

            <p style="margin: 0 0 24px 0; color: #a1a1aa; font-size: 16px; line-height: 1.6;">
                Your subscription for <strong style="color: #f4f4f6;">{domain}</strong> has been cancelled.
            </p>

            <!-- Info box -->
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background-color: rgba(0, 136, 255, 0.1); border-radius: 10px; border: 1px solid rgba(0, 136, 255, 0.2); margin-bottom: 24px;">
                <tr>
                    <td style="padding: 20px;">
                        <p style="margin: 0 0 8px 0; color: #0088ff; font-size: 14px; font-weight: 600;">
                            What happens next?
                        </p>
                        <p style="margin: 0; color: #a1a1aa; font-size: 14px; line-height: 1.5;">
                            Your store will remain active until <strong style="color: #f4f4f6;">{end_date_str}</strong>. After this date, your store will be suspended and data may be deleted after 30 days.
                        </p>
                    </td>
                </tr>
            </table>

            <p style="margin: 0 0 24px 0; color: #a1a1aa; font-size: 16px; line-height: 1.6;">
                Changed your mind? You can reactivate your subscription at any time before the end date.
            </p>

            <!-- CTA Button -->
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
                <tr>
                    <td align="center">
                        <table role="presentation" cellspacing="0" cellpadding="0" border="0">
                            <tr>
                                <td style="border-radius: 10px; background: linear-gradient(135deg, #00d4ff 0%, #0088ff 50%, #5b5bd6 100%);">
                                    <a href="https://shophosting.io/billing" style="display: inline-block; padding: 14px 28px; color: #08080a; text-decoration: none; font-weight: 600; font-size: 15px;">Reactivate Subscription</a>
                                </td>
                            </tr>
                        </table>
                    </td>
                </tr>
            </table>

            <p style="margin: 24px 0 0 0; color: #71717a; font-size: 14px; text-align: center;">
                We're sorry to see you go. If you have feedback, we'd love to hear it.
            </p>
        '''

        plain_text = f"""Subscription Cancelled

Your subscription for {domain} has been cancelled.

Your store will remain active until {end_date_str}. After this date, your store will be suspended and data may be deleted after 30 days.

Changed your mind? Reactivate at https://shophosting.io/billing

We're sorry to see you go. If you have feedback, contact support@shophosting.io

- ShopHosting.io Team
"""

        html = self._get_base_template(content, f"Subscription cancelled for {domain}")
        return self._send_email(to_email, "Subscription Cancelled", html, plain_text)


# Singleton instance for easy importing
email_service = EmailService()
