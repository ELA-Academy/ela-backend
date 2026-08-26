from flask_mail import Message
from flask import current_app, has_app_context
import random
import string
from app.utils.ms_graph_email import is_ms_graph_configured, send_email_via_graph

def _convert_plain_to_html(text):
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    lines = escaped.split("\n")
    return "<br/>".join(lines)

def generate_otp(length=6):
    """Generate a random numeric OTP."""
    return ''.join(random.choices(string.digits, k=length))

def send_otp_email(mail, recipient_email, otp, sender_email=None):
    """Sends an email with the generated OTP."""
    subject = 'Your Verification Code for ELA Academy'
    body = f'Your verification code is: {otp}\nThis code is valid for 10 minutes.'
    
    if is_ms_graph_configured():
        return send_email_via_graph(subject, recipient_email, _convert_plain_to_html(body), sender_email=sender_email)

    try:
        sender = current_app.config.get('MAIL_DEFAULT_SENDER') if has_app_context() else None
        msg = Message(subject, sender=sender, recipients=[recipient_email], body=body)
        if mail:
            mail.send(msg)
        return True
    except Exception as e:
        print(f"Failed to send OTP email to {recipient_email}: {e}")
        return False

def _build_action_email_html(title, recipient_name, message_lines, action_button_text, action_url, footer_note=""):
    """Builds a clean, responsive HTML email with a styled action button and unbreakable link."""
    paragraphs = "".join(f"<p style='margin: 0 0 14px 0; font-size: 15px; line-height: 1.6; color: #334155;'>{line}</p>" for line in message_lines)
    
    button_html = ""
    if action_button_text and action_url:
        button_html = f"""
        <div style="margin: 28px 0; text-align: center;">
            <a href="{action_url}" target="_blank" style="background-color: #673de6; color: #ffffff; padding: 14px 32px; border-radius: 8px; text-decoration: none; font-weight: 700; font-size: 15px; display: inline-block; box-shadow: 0 4px 12px rgba(103, 61, 230, 0.25);">
                {action_button_text}
            </a>
        </div>
        <p style="margin: 20px 0 6px 0; font-size: 12px; color: #64748b;">If the button above does not work, copy and paste this entire link into your browser:</p>
        <p style="margin: 0 0 16px 0; font-size: 11px; line-height: 1.4; color: #673de6; word-break: break-all;">
            <a href="{action_url}" target="_blank" style="color: #673de6; text-decoration: underline;">{action_url}</a>
        </p>
        """

    footer_html = f"<p style='margin: 16px 0 0 0; font-size: 12px; color: #94a3b8;'>{footer_note}</p>" if footer_note else ""

    return f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
    <body style="margin: 0; padding: 24px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc;">
        <table width="100%" border="0" cellspacing="0" cellpadding="0">
            <tr>
                <td align="center">
                    <table width="100%" max-width="560" style="max-width: 560px; background-color: #ffffff; border-radius: 12px; border: 1px solid #e2e8f0; padding: 36px 32px; box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);">
                        <tr>
                            <td>
                                <h2 style="margin: 0 0 20px 0; font-size: 20px; font-weight: 700; color: #0f172a;">{title}</h2>
                                {paragraphs}
                                {button_html}
                                {footer_html}
                                <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 24px 0 16px 0;" />
                                <p style="margin: 0; font-size: 12px; color: #94a3b8; text-align: center;">ELA Academy &bull; School Management App</p>
                            </td>
                        </tr>
                    </table>
                </td>
            </tr>
        </table>
    </body>
    </html>
    """

def send_staff_invite_email(mail, recipient_email, recipient_name, invite_link, sender_email="hr@ela-academy.org"):
    """Sends an invite email to a new staff member with their setup password link."""
    subject = 'Welcome to ELA Academy - Set Up Your Account'
    body_text = f"Hello {recipient_name},\n\nYou have been added as a staff member at ELA Academy.\nPlease click the link below to set up your password:\n\n{invite_link}\n\nThis link will expire in 7 days.\n\nBest regards,\nELA Academy Team"
    
    html_content = _build_action_email_html(
        title="Welcome to ELA Academy",
        recipient_name=recipient_name,
        message_lines=[
            f"Hello <strong>{recipient_name}</strong>,",
            "You have been invited to join the <strong>ELA Academy</strong> workspace.",
            "Please click the button below to set up your password and access your dashboard:"
        ],
        action_button_text="Set Up My Account",
        action_url=invite_link,
        footer_note="This setup link is secure and will expire in 7 days."
    )
    
    if is_ms_graph_configured():
        return send_email_via_graph(subject, recipient_email, html_content, sender_email=sender_email)

    try:
        sender = current_app.config.get('MAIL_DEFAULT_SENDER') if has_app_context() else None
        msg = Message(subject, sender=sender, recipients=[recipient_email], body=body_text, html=html_content)
        if mail:
            mail.send(msg)
        return True
    except Exception as e:
        print(f"Failed to send invite email to {recipient_email}: {e}")
        return False

def send_invite_email(mail, recipient_email, recipient_name, invite_link, role_name="member", sender_email="admin@ela-academy.org"):
    """Sends an invite email to a new user with their setup password link."""
    subject = 'Welcome to ELA Academy - Set Up Your Account'
    body_text = f"Hello {recipient_name},\n\nYou have been added as a {role_name} at ELA Academy.\nPlease click the link below to set up your password:\n\n{invite_link}\n\nThis link will expire in 7 days.\n\nBest regards,\nELA Academy Team"
    
    html_content = _build_action_email_html(
        title="Welcome to ELA Academy",
        recipient_name=recipient_name,
        message_lines=[
            f"Hello <strong>{recipient_name}</strong>,",
            f"You have been added as a <strong>{role_name}</strong> at ELA Academy.",
            "Please click the button below to set up your password and access your dashboard:"
        ],
        action_button_text="Set Up My Account",
        action_url=invite_link,
        footer_note="This link will expire in 7 days."
    )
    
    if is_ms_graph_configured():
        return send_email_via_graph(subject, recipient_email, html_content, sender_email=sender_email)

    try:
        sender = current_app.config.get('MAIL_DEFAULT_SENDER') if has_app_context() else None
        msg = Message(subject, sender=sender, recipients=[recipient_email], body=body_text, html=html_content)
        if mail:
            mail.send(msg)
        return True
    except Exception as e:
        print(f"Failed to send invite email to {recipient_email}: {e}")
        return False

def send_login_notice_email(mail, recipient_email, timestamp_str, ip_address, device_str, sender_email="itdept@ela-academy.org"):
    """Sends a security alert email notifying the user of a successful login."""
    subject = 'New Login Detected - ELA Academy'
    body_text = (
        f"Hello,\n\n"
        f"We detected a new login to your ELA Academy account.\n\n"
        f"Time: {timestamp_str}\n"
        f"IP Address: {ip_address}\n"
        f"Device/Browser: {device_str}\n\n"
        f"If this was you, no action is needed. If you do not recognize this login, "
        f"please change your password and secure your account immediately.\n\n"
        f"Best regards,\n"
        f"ELA Academy Team"
    )
    
    html_content = _build_action_email_html(
        title="Security Alert: New Login Detected",
        recipient_name="there",
        message_lines=[
            "We detected a new sign-in to your ELA Academy account:",
            f"&bull; <strong>Time:</strong> {timestamp_str}",
            f"&bull; <strong>IP Address:</strong> {ip_address}",
            f"&bull; <strong>Device/Browser:</strong> {device_str}",
            "If this was you, no action is needed. If you do not recognize this activity, please secure your account immediately."
        ],
        action_button_text=None,
        action_url=None
    )
    
    if is_ms_graph_configured():
        return send_email_via_graph(subject, recipient_email, html_content, sender_email=sender_email)

    try:
        sender = current_app.config.get('MAIL_DEFAULT_SENDER') if has_app_context() else None
        msg = Message(subject, sender=sender, recipients=[recipient_email], body=body_text, html=html_content)
        if mail:
            mail.send(msg)
        return True
    except Exception as e:
        print(f"Failed to send login notice email to {recipient_email}: {e}")
        return False

def send_password_reset_email(mail, recipient_email, recipient_name, reset_link, sender_email="itdept@ela-academy.org"):
    """Sends a password reset email."""
    subject = 'Reset Your Password - ELA Academy'
    body_text = f"Hello {recipient_name},\n\nYou requested a password reset for your ELA Academy account.\nPlease click the link below to reset your password:\n\n{reset_link}\n\nThis link will expire in 1 hour.\n\nIf you did not request this, you can safely ignore this email.\n\nBest regards,\nELA Academy Team"
    
    html_content = _build_action_email_html(
        title="Reset Your Password",
        recipient_name=recipient_name,
        message_lines=[
            f"Hello <strong>{recipient_name}</strong>,",
            "You requested a password reset for your ELA Academy account.",
            "Click the button below to set a new password:"
        ],
        action_button_text="Reset Password",
        action_url=reset_link,
        footer_note="This link will expire in 1 hour. If you did not request a password reset, you can safely ignore this email."
    )
    
    if is_ms_graph_configured():
        return send_email_via_graph(subject, recipient_email, html_content, sender_email=sender_email)

    try:
        sender = current_app.config.get('MAIL_DEFAULT_SENDER') if has_app_context() else None
        msg = Message(subject, sender=sender, recipients=[recipient_email], body=body_text, html=html_content)
        if mail:
            mail.send(msg)
        return True
    except Exception as e:
        print(f"Failed to send password reset email to {recipient_email}: {e}")
        return False