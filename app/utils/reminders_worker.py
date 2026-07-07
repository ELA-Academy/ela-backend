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
