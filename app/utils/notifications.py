import os
import json
from flask import current_app, render_template, has_app_context
from flask_mail import Message
from threading import Thread
from app.models import db
from app.models.notification_model import Notification
from app.models.push_subscription_model import PushSubscription
from pywebpush import webpush, WebPushException

def _safe_log(level, msg):
    try:
        if has_app_context() and current_app:
            logger = getattr(current_app, 'logger', None)
            if logger:
                log_fn = getattr(logger, level, logger.info)
                log_fn(msg)
                return
    except Exception:
        pass
    print(f"[{level.upper()}] {msg}")

def send_async_email(app, msg):
    with app.app_context():
        from app import mail
        try:
            mail.send(msg)
        except Exception as e:
            _safe_log('error', f"Failed to send email: {e}")

def send_email_in_background(subject, recipients, template_data, sender_email=None):
    try:
        from app.utils.ms_graph_email import is_ms_graph_configured, send_email_via_graph_background
        
        if has_app_context() and current_app:
            app = current_app._get_current_object()
        else:
            from app import create_app
            app = create_app()

        raw_content = template_data.get('html_content') or template_data.get('html_body') or template_data.get('message')
        if raw_content and ('<!doctype html' in str(raw_content).lower() or '<table' in str(raw_content).lower() or '<div style=' in str(raw_content).lower()):
            html_body = str(raw_content)
        else:
            if 'message' not in template_data and raw_content:
                template_data['message'] = raw_content
            with app.test_request_context('/'):
                html_body = render_template('email/notification.html', **template_data)

        # 1. Use Microsoft Graph API if credentials are set
        if is_ms_graph_configured():
            _safe_log('info', f"Dispatching email via Microsoft Graph API ({sender_email or 'default'})")
            return send_email_via_graph_background(subject, recipients, html_body, sender_email=sender_email)

        # 2. Fallback to SMTP/Flask-Mail
        sender_name = "Ela Academy"
        from_email = sender_email or os.getenv("MAIL_USERNAME")
        sender = f"{sender_name} <{from_email}>" if from_email else None
        
        msg = Message(subject, sender=sender, recipients=recipients, html=html_body)
        thr = Thread(target=send_async_email, args=[app, msg])
        thr.start()
        return thr
    except Exception as e:
        _safe_log('error', f"Failed to queue background email: {e}")
        return None

def send_push_notification(user, payload):
    """Finds a user's subscription and sends a push notification."""
    from app.models.staff_model import Staff
    
    if isinstance(user, Staff):
        sub_record = PushSubscription.query.filter_by(staff_id=user.id).first()
    else: # SuperAdmin
        sub_record = PushSubscription.query.filter_by(super_admin_id=user.id).first()

    if not sub_record:
        _safe_log('info', f"No push subscription found for user {user.id}.")
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
        _safe_log('info', f"Successfully sent push to user {user.id}")
    except WebPushException as ex:
        _safe_log('error', f"WebPush Error for user {user.id}: {ex}")
        if ex.response and ex.response.status_code in [404, 410]:
            _safe_log('warning', f"Deleting expired subscription for user {user.id}")
            db.session.delete(sub_record)
            db.session.commit()
    except Exception as e:
        _safe_log('error', f"An unexpected error occurred in send_push_notification: {e}")


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
                _safe_log('info', f"Duplicate notification skipped (idempotency: {idempotency_key})")
                return existing
        except Exception as e:
            _safe_log('error', f"Error checking idempotency: {e}")

    # Determine recipient details
    recipient_role = 'staff' if recipient.__class__.__name__ == 'Staff' else 'superadmin'
    recipient_id = recipient.id

    # Check user opt-out preferences (cached in Redis if available)
    if category == 'mention':
        allowed_channels = ['in_app', 'email', 'push']
    else:
        allowed_channels = get_user_allowed_channels(recipient_id, recipient_role, category=category)
    if not allowed_channels:
        _safe_log('info', f"User {recipient_role} {recipient_id} has opted out of all notifications for category {category}.")
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
        _safe_log('info', f"Notification request enqueued (id: {req.id}) for recipient {recipient_id} ({recipient_role})")
    except Exception as e:
        db.session.rollback()
        _safe_log('error', f"Failed to enqueue notification request: {e}")
        
    return req
        
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

def enqueue_user_notification(user_id, user_role, message, category='general', target_type=None, target_id=None, target_link=None, idempotency_key=None):
    """
    Helper to enqueue a notification request by recipient ID and role.
    """
    from app.models.staff_model import Staff
    from app.models.super_admin_model import SuperAdmin
    
    if user_role == 'staff':
        recipient = Staff.query.get(user_id)
    else:
        recipient = SuperAdmin.query.get(user_id)
        
    if not recipient:
        return None
        
    class TargetMock:
        def __init__(self, name, id):
            self.__class__.__name__ = name
            self.id = id
            
    target_obj = TargetMock(target_type, target_id) if target_type else None
    
    return enqueue_notification(
        recipient=recipient,
        message=message,
        idempotency_key=idempotency_key,
        category=category,
        target_obj=target_obj,
        target_link=target_link
    )