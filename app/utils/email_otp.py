from flask_mail import Message
from flask import current_app
import random
import string

def generate_otp(length=6):
    """Generate a random numeric OTP."""
    return ''.join(random.choices(string.digits, k=length))

def send_otp_email(mail, recipient_email, otp):
    """Sends an email with the generated OTP."""
    try:
        msg = Message(
            'Your Verification Code for ELA Academy',
            sender=current_app.config['MAIL_DEFAULT_SENDER'],
            recipients=[recipient_email]
        )
        msg.body = f'Your verification code is: {otp}\nThis code is valid for 10 minutes.'
        mail.send(msg)
        return True
    except Exception as e:
        current_app.logger.error(f"Failed to send email to {recipient_email}: {e}")
        return False

def send_staff_invite_email(mail, recipient_email, recipient_name, invite_link):
    """Sends an invite email to a new staff member with their setup password link."""
    try:
        msg = Message(
            'Welcome to ELA Academy - Set Up Your Account',
            sender=current_app.config['MAIL_DEFAULT_SENDER'],
            recipients=[recipient_email]
        )
        msg.body = f"Hello {recipient_name},\n\nYou have been added as a staff member at ELA Academy.\n\nPlease click the following link to set up your password and access your dashboard:\n\n{invite_link}\n\nThis link will expire in 7 days.\n\nBest regards,\nELA Academy Team"
        mail.send(msg)
        return True
    except Exception as e:
        current_app.logger.error(f"Failed to send invite email to {recipient_email}: {e}")
        return False

def send_invite_email(mail, recipient_email, recipient_name, invite_link, role_name="member"):
    """Sends an invite email to a new user with their setup password link."""
    try:
        msg = Message(
            'Welcome to ELA Academy - Set Up Your Account',
            sender=current_app.config['MAIL_DEFAULT_SENDER'],
            recipients=[recipient_email]
        )
        msg.body = f"Hello {recipient_name},\n\nYou have been added as a {role_name} at ELA Academy.\n\nPlease click the following link to set up your password and access your dashboard:\n\n{invite_link}\n\nThis link will expire in 7 days.\n\nBest regards,\nELA Academy Team"
        mail.send(msg)
        return True
    except Exception as e:
        current_app.logger.error(f"Failed to send invite email to {recipient_email}: {e}")
        return False

def send_login_notice_email(mail, recipient_email, timestamp_str, ip_address, device_str):
    """Sends a security alert email notifying the user of a successful login."""
    try:
        msg = Message(
            'New Login Detected - ELA Academy',
            sender=current_app.config['MAIL_DEFAULT_SENDER'],
            recipients=[recipient_email]
        )
        msg.body = (
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
        mail.send(msg)
        return True
    except Exception as e:
        current_app.logger.error(f"Failed to send login notice email to {recipient_email}: {e}")
        return False

def send_password_reset_email(mail, recipient_email, recipient_name, reset_link):
    """Sends a password reset email."""
    try:
        msg = Message(
            'Reset Your Password - ELA Academy',
            sender=current_app.config['MAIL_DEFAULT_SENDER'],
            recipients=[recipient_email]
        )
        msg.body = f"Hello {recipient_name},\n\nYou requested a password reset for your ELA Academy account.\n\nPlease click the following link to reset your password:\n\n{reset_link}\n\nThis link will expire in 1 hour.\n\nIf you did not request this, you can safely ignore this email.\n\nBest regards,\nELA Academy Team"
        mail.send(msg)
        return True
    except Exception as e:
        current_app.logger.error(f"Failed to send password reset email to {recipient_email}: {e}")
        return False