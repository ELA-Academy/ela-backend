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

def send_staff_invite_email(mail, recipient_email, recipient_name, invite_link, sender_email="hr@ela-academy.org"):
    """Sends an invite email to a new staff member with their setup password link."""
    subject = 'Welcome to ELA Academy - Set Up Your Account'
    body = f"Hello {recipient_name},\n\nYou have been added as a staff member at ELA Academy.\n\nPlease click the following link to set up your password and access your dashboard:\n\n{invite_link}\n\nThis link will expire in 7 days.\n\nBest regards,\nELA Academy Team"
    
    if is_ms_graph_configured():
        return send_email_via_graph(subject, recipient_email, _convert_plain_to_html(body), sender_email=sender_email)

    try:
        sender = current_app.config.get('MAIL_DEFAULT_SENDER') if has_app_context() else None
        msg = Message(subject, sender=sender, recipients=[recipient_email], body=body)
        if mail:
            mail.send(msg)
        return True
    except Exception as e:
        print(f"Failed to send invite email to {recipient_email}: {e}")
        return False

def send_invite_email(mail, recipient_email, recipient_name, invite_link, role_name="member", sender_email="admin@ela-academy.org"):
    """Sends an invite email to a new user with their setup password link."""
    subject = 'Welcome to ELA Academy - Set Up Your Account'
    body = f"Hello {recipient_name},\n\nYou have been added as a {role_name} at ELA Academy.\n\nPlease click the following link to set up your password and access your dashboard:\n\n{invite_link}\n\nThis link will expire in 7 days.\n\nBest regards,\nELA Academy Team"
    
    if is_ms_graph_configured():
        return send_email_via_graph(subject, recipient_email, _convert_plain_to_html(body), sender_email=sender_email)

    try:
        sender = current_app.config.get('MAIL_DEFAULT_SENDER') if has_app_context() else None
        msg = Message(subject, sender=sender, recipients=[recipient_email], body=body)
        if mail:
            mail.send(msg)
        return True
    except Exception as e:
        print(f"Failed to send invite email to {recipient_email}: {e}")
        return False

def send_login_notice_email(mail, recipient_email, timestamp_str, ip_address, device_str, sender_email="itdept@ela-academy.org"):
    """Sends a security alert email notifying the user of a successful login."""
    subject = 'New Login Detected - ELA Academy'
    body = (
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
    
    if is_ms_graph_configured():
        return send_email_via_graph(subject, recipient_email, _convert_plain_to_html(body), sender_email=sender_email)

    try:
        sender = current_app.config.get('MAIL_DEFAULT_SENDER') if has_app_context() else None
        msg = Message(subject, sender=sender, recipients=[recipient_email], body=body)
        if mail:
            mail.send(msg)
        return True
    except Exception as e:
        print(f"Failed to send login notice email to {recipient_email}: {e}")
        return False

def send_password_reset_email(mail, recipient_email, recipient_name, reset_link, sender_email="itdept@ela-academy.org"):
    """Sends a password reset email."""
    subject = 'Reset Your Password - ELA Academy'
    body = f"Hello {recipient_name},\n\nYou requested a password reset for your ELA Academy account.\n\nPlease click the following link to reset your password:\n\n{reset_link}\n\nThis link will expire in 1 hour.\n\nIf you did not request this, you can safely ignore this email.\n\nBest regards,\nELA Academy Team"
    
    if is_ms_graph_configured():
        return send_email_via_graph(subject, recipient_email, _convert_plain_to_html(body), sender_email=sender_email)

    try:
        sender = current_app.config.get('MAIL_DEFAULT_SENDER') if has_app_context() else None
        msg = Message(subject, sender=sender, recipients=[recipient_email], body=body)
        if mail:
            mail.send(msg)
        return True
    except Exception as e:
        print(f"Failed to send password reset email to {recipient_email}: {e}")
        return False