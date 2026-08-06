import time
import threading
from datetime import datetime, timedelta
from app.models import db
from app.models.board_model import CalendarEvent, BoardAccessMember
from app.models.notification_model import Notification
from app.models.staff_model import Staff
from app.models.super_admin_model import SuperAdmin

def start_reminders_scheduler(app):
    """Starts a background thread that polls for calendar event reminders."""
    def run_scheduler():
        with app.app_context():
            print("=== Reminders Scheduler Daemon Started ===")
            while True:
                try:
                    now = datetime.utcnow()
                    
                    # 1. Fetch unnotified events with reminder settings
                    events = CalendarEvent.query.filter(
                        CalendarEvent.reminder_sent == False,
                        CalendarEvent.reminder_minutes != None,
                        CalendarEvent.start_datetime != None
                    ).all()
                    
                    for event in events:
                        # Target reminder time: start_datetime - reminder_minutes
                        reminder_time = event.start_datetime - timedelta(minutes=event.reminder_minutes)
                        
                        if now >= reminder_time:
                            print(f"[Reminder Daemon] Event '{event.title}' is due for notification.")
                            
                            # Construct notification message
                            time_left_str = f"starts in {event.reminder_minutes} minutes" if event.reminder_minutes > 0 else "starts now"
                            msg = f"Reminder: '{event.title}' {time_left_str}!"
                            
                            notified_users = set() # Avoid duplicates
                            
                            # Find users to notify:
                            # A. Event Creator (Staff or SuperAdmin)
                            creator_staff = Staff.query.filter_by(name=event.created_by_name).first()
                            creator_admin = SuperAdmin.query.filter_by(name=event.created_by_name).first()
                            
                            if creator_staff:
                                notified_users.add(('staff', creator_staff.id))
                            if creator_admin:
                                notified_users.add(('superadmin', creator_admin.id))
                                
                            # B. If linked to a board, notify all board members
                            if event.board_id:
                                members = BoardAccessMember.query.filter_by(board_id=event.board_id).all()
                                for member in members:
                                    if member.staff_id:
                                        notified_users.add(('staff', member.staff_id))
                                    if member.super_admin_id:
                                        notified_users.add(('superadmin', member.super_admin_id))
                                        
                            # C. If linked to a task, notify task assignees
                            if event.linked_task:
                                for assignee in event.linked_task.assignees:
                                    if assignee.staff_id:
                                        notified_users.add(('staff', assignee.staff_id))
                                    if assignee.super_admin_id:
                                        notified_users.add(('superadmin', assignee.super_admin_id))
                            
                            # Save notifications in DB if user preferences allow it
                            for user_type, user_id in notified_users:
                                should_notify = True
                                try:
                                    import json
                                    if user_type == 'staff':
                                        u = Staff.query.get(user_id)
                                        if u and u.notification_preferences:
                                            prefs = json.loads(u.notification_preferences)
                                            if prefs.get('reminders') is False:
                                                should_notify = False
                                    else:
                                        u = SuperAdmin.query.get(user_id)
                                        if u and u.notification_preferences:
                                            prefs = json.loads(u.notification_preferences)
                                            if prefs.get('reminders') is False:
                                                should_notify = False
                                except Exception as pref_err:
                                    print(f"[Reminder Daemon Preferences Error] {pref_err}")

                                if should_notify:
                                    from app.utils.notifications import enqueue_user_notification
                                    import hashlib
                                    # Idempotency key for event reminder (recipient + event + day)
                                    raw_key = f"reminder:{user_type}:{user_id}:{event.id}"
                                    idempotency_key = hashlib.md5(raw_key.encode('utf-8')).hexdigest()
                                    
                                    enqueue_user_notification(
                                        user_id=user_id,
                                        user_role=user_type,
                                        message=msg,
                                        category='reminder',
                                        target_type='CalendarEvent',
                                        target_id=event.id,
                                        target_link=f"/admin/boards/{event.board_id}?tab=calendar" if event.board_id else "/admin/boards",
                                        idempotency_key=idempotency_key
                                    )
                            
                            # Mark as sent
                            event.reminder_sent = True
                            db.session.flush()
                            
                    db.session.commit()
                except Exception as e:
                    db.session.rollback()
                    print(f"[Reminder Daemon Error] {e}")
                
                # Sleep for 60 seconds
                time.sleep(60)

    # Spawn thread in daemon mode so it exits with the main process
    t = threading.Thread(target=run_scheduler, daemon=True)
    t.start()


def start_notification_processor(app):
    """Starts a background thread that processes pending NotificationRequest queue items."""
    def run_processor():
        with app.app_context():
            from app.models.notification_request_model import NotificationRequest
            from app.models.notification_model import Notification
            from app.models.staff_model import Staff
            from app.models.super_admin_model import SuperAdmin
            from app.utils.notifications import send_push_notification
            from app.scheduled_tasks import send_batch_digests, check_due_date_reminders
            from app import socketio
            from datetime import datetime, timedelta
            import time

            print("=== Notification Queue Processor Thread Started ===")
            
            # Clean up stale pending notifications on startup to prevent flooding
            try:
                one_hour_ago = datetime.utcnow() - timedelta(hours=1)
                stale_requests = NotificationRequest.query.filter(
                    NotificationRequest.status == 'PENDING',
                    NotificationRequest.created_at < one_hour_ago
                ).all()
                if stale_requests:
                    for r in stale_requests:
                        r.status = 'SKIPPED'
                        r.error_message = "Skipped to prevent startup backlog flood."
                    db.session.commit()
                    print(f"=== [Queue Processor Startup] Cleared {len(stale_requests)} stale backlog notifications ===")
            except Exception as clean_err:
                db.session.rollback()
                print(f"[Queue Processor Startup] Failed to clear backlog: {clean_err}")

            MAX_NOTIFICATIONS_PER_MINUTE = 5
            last_checked_reminders = None

            while True:
                try:
                    # Run due date reminders check every 10 minutes (600s)
                    now_time = datetime.utcnow()
                    if last_checked_reminders is None or (now_time - last_checked_reminders).total_seconds() > 600:
                        last_checked_reminders = now_time
                        try:
                            check_due_date_reminders()
                        except Exception as ex_rem:
                            db.session.rollback()
                            print(f"[Queue Processor] Error checking due date reminders: {ex_rem}")

                    # 1. Fetch a batch of pending requests
                    query = NotificationRequest.query.filter(
                        NotificationRequest.status == 'PENDING',
                        NotificationRequest.retry_count < 3,
                        NotificationRequest.created_at <= datetime.utcnow()
                    ).order_by(NotificationRequest.created_at.asc()).limit(50)
                    
                    batch = query.all()
                    if batch:
                        # Mark claimed items as PROCESSING immediately
                        for req in batch:
                            req.status = 'PROCESSING'
                        try:
                            db.session.commit()
                        except Exception as e:
                            db.session.rollback()
                            time.sleep(3)
                            continue

                        one_minute_ago = datetime.utcnow() - timedelta(seconds=60)
                        for req in batch:
                            recipient = None
                            try:
                                if req.recipient_role == 'staff':
                                    recipient = Staff.query.get(req.recipient_id)
                                else:
                                    recipient = SuperAdmin.query.get(req.recipient_id)
                                    
                                if not recipient:
                                    raise ValueError(f"Recipient not found (id: {req.recipient_id}, role: {req.recipient_role})")

                                # Check Rate Limit
                                count = Notification.query.filter(
                                    (Notification.staff_id == req.recipient_id) if req.recipient_role == 'staff' else (Notification.super_admin_id == req.recipient_id),
                                    Notification.created_at >= one_minute_ago
                                ).count()

                                if count >= MAX_NOTIFICATIONS_PER_MINUTE:
                                    req.status = 'PENDING'
                                    req.created_at = datetime.utcnow() + timedelta(seconds=30)
                                    db.session.commit()
                                    continue

                                channels = req.channels.split(',')

                                # A. Deliver In-App
                                if 'in_app' in channels:
                                    in_app_notif = Notification(
                                        staff_id=req.recipient_id if req.recipient_role == 'staff' else None,
                                        super_admin_id=req.recipient_id if req.recipient_role != 'staff' else None,
                                        message=req.message,
                                        category=req.category,
                                        target_type=req.target_type,
                                        target_id=req.target_id,
                                        target_link=req.target_link,
                                        created_at=datetime.utcnow()
                                    )
                                    db.session.add(in_app_notif)
                                    db.session.flush()

                                    # Real-time socket broadcast
                                    try:
                                        user_room = f"user_{req.recipient_role}_{req.recipient_id}"
                                        socketio.emit('new_inapp_notification', in_app_notif.to_dict(), room=user_room)
                                    except Exception as ex:
                                        print(f"[Queue Processor] Broadcast failed: {ex}")

                                # B. Deliver Email
                                if 'email' in channels:
                                    from app.models.notification_model import PendingEmailNotification
                                    pending_email = PendingEmailNotification(
                                        recipient_id=req.recipient_id,
                                        recipient_role=req.recipient_role,
                                        message=req.message,
                                        target_link=req.target_link
                                    )
                                    db.session.add(pending_email)

                                # C. Deliver Push
                                if 'push' in channels:
                                    push_payload = {
                                        "title": "ELA Academy Notification",
                                        "body": req.message,
                                        "url": req.target_link or '/'
                                    }
                                    send_push_notification(recipient, push_payload)

                                req.status = 'COMPLETED'
                            except Exception as e:
                                req.retry_count += 1
                                req.error_message = str(e)
                                if req.retry_count >= 3:
                                    req.status = 'FAILED'
                                else:
                                    req.status = 'PENDING'
                            db.session.commit()

                    # Send batched digests
                    try:
                        send_batch_digests()
                    except Exception as e:
                        db.session.rollback()
                        print(f"[Queue Processor] Digest error: {e}")

                except Exception as loop_err:
                    db.session.rollback()
                    print(f"[Queue Processor Loop Error] {loop_err}")
                
                time.sleep(3)

    t = threading.Thread(target=run_processor, daemon=True)
    t.start()
