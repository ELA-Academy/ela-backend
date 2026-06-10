import os
import json
from flask import current_app, render_template
from flask_mail import Message
from threading import Thread
from app.models import db
from app.models.notification_model import Notification
from app.models.push_subscription_model import PushSubscription
from pywebpush import webpush, WebPushException

def send_async_email(app, msg):
    with app.app_context():
        from app import mail
        try:
            mail.send(msg)
        except Exception as e:
            app.logger.error(f"Failed to send email: {e}")

def send_email_in_background(subject, recipients, template_data):
    app = current_app._get_current_object()
    html_body = render_template('email/notification.html', **template_data)
    msg = Message(subject, recipients=recipients, html=html_body)
    thr = Thread(target=send_async_email, args=[app, msg])
    thr.start()
    return thr

def send_push_notification(user, payload):
    """Finds a user's subscription and sends a push notification."""
    from app.models.staff_model import Staff
    
    if isinstance(user, Staff):
        sub_record = PushSubscription.query.filter_by(staff_id=user.id).first()
    else: # SuperAdmin
        sub_record = PushSubscription.query.filter_by(super_admin_id=user.id).first()

    if not sub_record:
        current_app.logger.info(f"No push subscription found for user {user.id}.")
        return

    try:
        frontend_base_url = os.getenv('FRONTEND_URL', 'http://localhost:5173')
        full_url = f"{frontend_base_url}{payload.get('url', '/')}"
        
        push_payload_data = {
            "title": payload.get('title', 'New Notification'),
            "body": payload.get('body'),
            "url": full_url
        }

        webpush(
            subscription_info=json.loads(sub_record.subscription_json),
            data=json.dumps(push_payload_data),
            vapid_private_key=os.getenv("VAPID_PRIVATE_KEY"),
            vapid_claims={"sub": os.getenv("VAPID_CLAIMS_EMAIL")}
        )
        current_app.logger.info(f"Successfully sent push to user {user.id}")
    except WebPushException as ex:
        current_app.logger.error(f"WebPush Error for user {user.id}: {ex}")
        if ex.response and ex.response.status_code in [404, 410]:
            current_app.logger.warning(f"Deleting expired subscription for user {user.id}")
            db.session.delete(sub_record)
            db.session.commit()
    except Exception as e:
        current_app.logger.error(f"An unexpected error occurred in send_push_notification: {e}")


def create_notifications_and_send_emails(recipients, message, target_obj=None):
    """
    Central function for creating in-app, email, and push notifications.
    """
    if not recipients:
        return

    target_link = "/"
    if target_obj:
        if target_obj.__class__.__name__ == 'Lead':
            target_link = f"/admin/admissions/leads/{target_obj.secure_token}"
        elif target_obj.__class__.__name__ == 'Task' and hasattr(target_obj, 'lead'):
             target_link = f"/admin/admissions/leads/{target_obj.lead.secure_token}"

    frontend_base_url = os.getenv('FRONTEND_URL', 'http://localhost:5173')
    full_action_link = f"{frontend_base_url}{target_link}"

    recipient_emails = [user.email for user in recipients]
    
    for user in recipients:
        if user.__class__.__name__ == 'Staff':
            db.session.add(Notification(
                staff_id=user.id,
                message=message,
                target_type=target_obj.__class__.__name__ if target_obj else None,
                target_id=target_obj.id if target_obj else None,
                target_link=target_link
            ))

        push_payload = {
            "title": "ELA Academy Notification",
            "body": message,
            "url": target_link
        }
        send_push_notification(user, push_payload)
    
    email_data = { 'message': message, 'action_link': full_action_link }
    send_email_in_background(
        subject="You have a new notification",
        recipients=recipient_emails,
        template_data=email_data
    )