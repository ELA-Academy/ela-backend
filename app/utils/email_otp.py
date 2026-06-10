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