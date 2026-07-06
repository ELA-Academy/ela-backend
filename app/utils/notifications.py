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


def get_user_allowed_channels(user_id, user_role, category='general'):
    from app.utils.redis_client import get_redis_client
    import json
    
    redis_client = get_redis_client()
    cache_key = f"user_prefs:{user_role}:{user_id}"
    
    # Try loading from cache
    cached = None
    try:
        cached = redis_client.get(cache_key)
    except Exception:
        pass

    if cached:
        try:
            prefs = json.loads(cached.decode('utf-8'))
        except Exception:
            prefs = None
    else:
        prefs = None
        
    if prefs is None:
        # Load from DB
        from app.models.notification_request_model import UserNotificationPreference
        try:
            db_prefs = UserNotificationPreference.query.filter_by(user_id=user_id, user_role=user_role).all()
            prefs = {}
            for p in db_prefs:
                key = f"{p.category}:{p.channel}"
                prefs[key] = p.enabled
            # Cache in Redis with 1 hour expiration
            try:
                redis_client.set(cache_key, json.dumps(prefs), ex=3600)
            except Exception:
                pass
        except Exception:
            prefs = {}
            
    # Resolve allowed channels
    allowed = []
    for channel in ['in_app', 'email', 'push']:
        specific_key = f"{category}:{channel}"
        global_key = f"all:{channel}"
        
        enabled = True
        if specific_key in prefs:
            enabled = prefs[specific_key]
        elif global_key in prefs:
            enabled = prefs[global_key]
            
        if enabled:
            allowed.append(channel)
            
    return allowed

def enqueue_notification(recipient, message, idempotency_key=None, category='general', target_obj=None, target_link=None):
    """
    Ingestion phase of the Notification System.
    Checks user preferences (cached in Redis), enforces idempotency keys,
    and inserts notification requests into the database for asynchronous worker processing.
    """
    if not recipient:
        return None

    # Enforce idempotency key check
    if idempotency_key:
        from app.models.notification_request_model import NotificationRequest
        try:
            existing = NotificationRequest.query.filter_by(idempotency_key=idempotency_key).first()
            if existing:
                current_app.logger.info(f"Duplicate notification skipped (idempotency: {idempotency_key})")
                return existing
        except Exception as e:
            current_app.logger.error(f"Error checking idempotency: {e}")

    # Determine recipient details
    recipient_role = 'staff' if recipient.__class__.__name__ == 'Staff' else 'superadmin'
    recipient_id = recipient.id

    # Check user opt-out preferences (cached in Redis if available)
    allowed_channels = get_user_allowed_channels(recipient_id, recipient_role, category=category)
    if not allowed_channels:
        current_app.logger.info(f"User {recipient_role} {recipient_id} has opted out of all notifications for category {category}.")
        return None

    # Resolve target details
    resolved_link = target_link or "/"
    if target_obj:
        if target_obj.__class__.__name__ == 'Lead':
            resolved_link = f"/admin/admissions/leads/{target_obj.secure_token}"
        elif target_obj.__class__.__name__ == 'Task' and hasattr(target_obj, 'lead'):
            resolved_link = f"/admin/admissions/leads/{target_obj.lead.secure_token}"

    # Insert non-blocking request to the queue
    from app.models.notification_request_model import NotificationRequest
    from datetime import datetime

    req = NotificationRequest(
        idempotency_key=idempotency_key,
        recipient_id=recipient_id,
        recipient_role=recipient_role,
        message=message,
        category=category,
        target_type=target_obj.__class__.__name__ if target_obj else None,
        target_id=target_obj.id if target_obj else None,
        target_link=resolved_link,
        status='PENDING',
        channels=",".join(allowed_channels),
        created_at=datetime.utcnow()
    )
    
    db.session.add(req)
    # Commit ingestion transaction to make request visible to workers
    try:
        db.session.commit()
        current_app.logger.info(f"Notification request enqueued (id: {req.id}) for recipient {recipient_id} ({recipient_role})")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Failed to enqueue notification request: {e}")
        
    return req

def create_notifications_and_send_emails(recipients, message, target_obj=None):
    """
    Legacy wrapper that maps directly to the new asynchronous enqueue_notification system.
    """
    if not recipients:
        return
    for user in recipients:
        import hashlib
        import time
        role = 'staff' if user.__class__.__name__ == 'Staff' else 'superadmin'
        raw_key = f"{role}:{user.id}:{message}:{int(time.time() / 10)}" # 10s window
        idempotency_key = hashlib.md5(raw_key.encode('utf-8')).hexdigest()
        enqueue_notification(
            recipient=user,
            message=message,
            idempotency_key=idempotency_key,
            category='general',
            target_obj=target_obj
        )