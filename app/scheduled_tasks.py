import click
from flask.cli import with_appcontext
from datetime import date
from dateutil.relativedelta import relativedelta
from .models import db
from .models.financial_model import Subscription, Invoice, InvoiceItem
from .models.student_model import Parent
from .utils.notifications import send_email_in_background
import os

@click.command('generate-invoices', help='Checks for subscriptions due and generates invoices.')
@with_appcontext
def generate_invoices_command():
    """
    This is the scheduled task. 
    It finds all active subscriptions where the next_invoice_date is today or in the past
    and generates a new invoice for them.
    """
    today = date.today()
    due_subscriptions = Subscription.query.filter(
        Subscription.status == 'Active',
        Subscription.next_invoice_date <= today
    ).all()

    if not due_subscriptions:
        print("No invoices to generate today.")
        return

    print(f"Found {len(due_subscriptions)} subscription(s) due for invoicing.")

    for sub in due_subscriptions:
        print(f"Processing subscription for student: {sub.account.student.first_name} {sub.account.student.last_name}")
        
        # Calculate the due date for the new invoice
        due_date = today.replace(day=sub.due_day)
        if today.day > sub.due_day:
            due_date += relativedelta(months=1)

        # Create the new invoice
        new_invoice = Invoice(
            account_id=sub.account_id,
            status='Sent', # Automatically mark as 'Sent'
            due_date=due_date
        )
        
        # Add items from the subscription template
        for item_data in sub.items_json:
            # Treat an empty string for amount as 0
            amount = float(item_data.get('amount') or 0)
            item = InvoiceItem(
                description=item_data.get('description', ''),
                amount=amount
            )
            new_invoice.items.append(item)
        
        db.session.add(new_invoice)
        
        # --- Send Email Notification to Parent ---
        parent = sub.account.student.parents[0] if sub.account.student.parents else None
        if parent:
            student_name = f"{sub.account.student.first_name} {sub.account.student.last_name}"
            frontend_url = os.getenv('FRONTEND_URL', 'http://localhost:5173')
            # The parent portal link doesn't exist yet, so we'll link to a placeholder
            action_link = f"{frontend_url}/parent/billing"

            email_data = {
                'message': f"A new tuition invoice for {student_name} is ready. The total amount is ${new_invoice.total_amount:.2f} and is due on {due_date.strftime('%B %d, %Y')}.",
                'action_link': action_link
            }
            send_email_in_background(
                subject=f"New Tuition Invoice for {student_name}",
                recipients=[parent.email],
                template_data=email_data
            )
            print(f"  - Invoice email queued for {parent.email}")


        cycle_clean = (sub.cycle or 'Monthly').lower().replace('-', '').replace(' ', '')
        if cycle_clean == 'weekly':
            sub.next_invoice_date += relativedelta(weeks=1)
        elif cycle_clean == 'biweekly':
            sub.next_invoice_date += relativedelta(weeks=2)
        elif cycle_clean == 'quarterly':
            sub.next_invoice_date += relativedelta(months=3)
        else:
            sub.next_invoice_date += relativedelta(months=1)
        
        print(f"  - Invoice created. Cycle: {sub.cycle}. Next invoice date set to: {sub.next_invoice_date.isoformat()}")

    try:
        db.session.commit()
        print("Successfully committed all new invoices to the database.")
    except Exception as e:
        db.session.rollback()
        print(f"An error occurred. Rolling back changes. Error: {e}")

def check_due_date_reminders():
    from datetime import date, datetime
    from app.models.board_model import BoardTask
    from app.utils.notifications import enqueue_user_notification
    import hashlib

    # Find tasks that are due today or overdue, not marked completed ('Done'), and reminder not sent
    today = date.today()
    tasks_to_remind = BoardTask.query.filter(
        BoardTask.due_date <= today,
        BoardTask.status != 'Done',
        BoardTask.due_date_reminder_sent == False
    ).all()

    for task in tasks_to_remind:
        # Determine all assignees and watchers to notify
        recipients = []
        if task.responsible_staff_id:
            recipients.append(('staff', task.responsible_staff_id))
        if task.responsible_super_admin_id:
            recipients.append(('superadmin', task.responsible_super_admin_id))
        
        for ass in task.assignees:
            if ass.staff_id:
                recipients.append(('staff', ass.staff_id))
            elif ass.super_admin_id:
                recipients.append(('superadmin', ass.super_admin_id))
                
        for watcher in task.watchers:
            if watcher.staff_id:
                recipients.append(('staff', watcher.staff_id))
            elif watcher.super_admin_id:
                recipients.append(('superadmin', watcher.super_admin_id))

        # Deduplicate recipients
        unique_recipients = set(recipients)
        
        is_overdue = task.due_date < today
        if is_overdue:
            msg = f"Task '{task.title}' is overdue! It was due on {task.due_date}."
        else:
            msg = f"Task '{task.title}' is due today ({task.due_date})!"

        for role, user_id in unique_recipients:
            key_raw = f"reminder:{task.id}:{role}:{user_id}"
            idempotency_key = hashlib.md5(key_raw.encode('utf-8')).hexdigest()
            
            enqueue_user_notification(
                user_id=user_id,
                user_role=role,
                message=msg,
                category='general',
                target_type='Board',
                target_id=task.group.board_id,
                target_link=f"/admin/boards/{task.group.board_id}?task={task.id}",
                idempotency_key=idempotency_key
            )
            
        task.due_date_reminder_sent = True
        
    db.session.commit()

def check_calendar_event_reminders():
    from datetime import datetime, timedelta
    from app.models.board_model import CalendarEvent, BoardAccessMember
    from app.models.staff_model import Staff
    from app.models.super_admin_model import SuperAdmin
    from app.utils.notifications import enqueue_user_notification
    import hashlib
    import json

    print("[Reminder Worker] Checking calendar event reminders...")
    now = datetime.utcnow()
    events = CalendarEvent.query.filter(
        CalendarEvent.reminder_sent == False,
        CalendarEvent.reminder_minutes != None,
        CalendarEvent.start_datetime != None
    ).all()
    
    for event in events:
        reminder_time = event.start_datetime - timedelta(minutes=event.reminder_minutes)
        if now >= reminder_time:
            print(f"[Reminder Worker] Event '{event.title}' is due for notification.")
            time_left_str = f"starts in {event.reminder_minutes} minutes" if event.reminder_minutes > 0 else "starts now"
            msg = f"Reminder: '{event.title}' {time_left_str}!"
            
            notified_users = set()
            creator_staff = Staff.query.filter_by(name=event.created_by_name).first()
            creator_admin = SuperAdmin.query.filter_by(name=event.created_by_name).first()
            
            if creator_staff:
                notified_users.add(('staff', creator_staff.id))
            if creator_admin:
                notified_users.add(('superadmin', creator_admin.id))
                
            if event.board_id:
                members = BoardAccessMember.query.filter_by(board_id=event.board_id).all()
                for member in members:
                    if member.staff_id:
                        notified_users.add(('staff', member.staff_id))
                    if member.super_admin_id:
                        notified_users.add(('superadmin', member.super_admin_id))
                        
            if event.linked_task:
                for assignee in event.linked_task.assignees:
                    if assignee.staff_id:
                        notified_users.add(('staff', assignee.staff_id))
                    if assignee.super_admin_id:
                        notified_users.add(('superadmin', assignee.super_admin_id))
            
            for user_type, user_id in notified_users:
                should_notify = True
                try:
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
                    print(f"[Reminder Worker Preferences Error] {pref_err}")

                if should_notify:
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
            
            event.reminder_sent = True
            db.session.flush()
            
    db.session.commit()

@click.command('process-notifications', help='Processes pending notification requests in a polling queue.')
@with_appcontext
def process_notifications_command():
    """
    Asynchronous worker task implementing the Polling Queue pattern.
    Runs continuously as a daemon process, claiming pending batches concurrently.
    """
    import time
    from datetime import datetime, timedelta
    from .models.notification_request_model import NotificationRequest
    from .models.notification_model import Notification
    from .models.staff_model import Staff
    from .models.super_admin_model import SuperAdmin
    from .utils.notifications import send_push_notification, send_email_in_background
    import os

    print("=== Notification Queue Worker Daemon Started ===")
    
    MAX_NOTIFICATIONS_PER_MINUTE = 5
    last_checked_reminders = None
    last_checked_cal_reminders = None
    
    while True:
        try:
            # Check due date reminders every 10 minutes
            now_time = datetime.utcnow()
            if last_checked_reminders is None or (now_time - last_checked_reminders).total_seconds() > 600:
                last_checked_reminders = now_time
                try:
                    check_due_date_reminders()
                except Exception as ex_rem:
                    db.session.rollback()
                    print(f"Error checking due date reminders: {ex_rem}")
        except Exception as e_rem_outer:
            db.session.rollback()
            print(f"Outer error checking reminders: {e_rem_outer}")

        try:
            # Check calendar event reminders every 60 seconds
            now_time = datetime.utcnow()
            if last_checked_cal_reminders is None or (now_time - last_checked_cal_reminders).total_seconds() > 60:
                last_checked_cal_reminders = now_time
                try:
                    check_calendar_event_reminders()
                except Exception as ex_cal_rem:
                    db.session.rollback()
                    print(f"Error checking calendar event reminders: {ex_cal_rem}")
        except Exception as e_cal_rem_outer:
            db.session.rollback()
            print(f"Outer error checking calendar reminders: {e_cal_rem_outer}")

        try:
            one_minute_ago = datetime.utcnow() - timedelta(seconds=60)
            
            # 1. Fetch a batch of pending requests
            query = NotificationRequest.query.filter(
                NotificationRequest.status == 'PENDING',
                NotificationRequest.retry_count < 3,
                NotificationRequest.created_at <= datetime.utcnow()
            ).order_by(NotificationRequest.created_at.asc()).limit(50)
            
            if db.engine.name in ['mysql', 'postgresql']:
                query = query.with_for_update(skip_locked=True)
                
            batch = query.all()
            if batch:
                print(f"[{datetime.utcnow().isoformat()}] Claimed {len(batch)} pending notification(s) for processing.")

                # 2. Mark claimed items as PROCESSING immediately
                for req in batch:
                    req.status = 'PROCESSING'
                try:
                    db.session.commit()
                except Exception as e:
                    db.session.rollback()
                    print(f"Failed to claim batch: {e}")
                    time.sleep(3)
                    continue

                # 3. Deliver claimed requests
                for req in batch:
                    recipient = None
                    try:
                        # Resolve recipient object
                        if req.recipient_role == 'staff':
                            recipient = Staff.query.get(req.recipient_id)
                        else:
                            recipient = SuperAdmin.query.get(req.recipient_id)
                            
                        if not recipient:
                            raise ValueError(f"Recipient not found (id: {req.recipient_id}, role: {req.recipient_role})")

                        # Check Rate Limit: Count of in-app notifications created in last 60 seconds
                        count = Notification.query.filter(
                            (Notification.staff_id == req.recipient_id) if req.recipient_role == 'staff' else (Notification.super_admin_id == req.recipient_id),
                            Notification.created_at >= one_minute_ago
                        ).count()

                        if count >= MAX_NOTIFICATIONS_PER_MINUTE:
                            print(f"  - Rate limit exceeded for recipient {req.recipient_id} ({req.recipient_role}). Deferring.")
                            req.status = 'PENDING'
                            req.created_at = datetime.utcnow() + timedelta(seconds=30) # backoff 30 seconds
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
                            db.session.flush() # get ID

                            # Real-time socket broadcast
                            try:
                                from app import socketio
                                user_room = f"user_{req.recipient_role}_{req.recipient_id}"
                                socketio.emit('new_inapp_notification', in_app_notif.to_dict(), room=user_room)
                                print(f"  - Broadcasted in-app notification {in_app_notif.id} to room {user_room}")
                            except Exception as ex:
                                print(f"  - Failed to broadcast socket event: {ex}")

                        # B. Deliver Email (Queue for digest batching)
                        if 'email' in channels:
                            from .models.notification_model import PendingEmailNotification
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
                        print(f"  - Request {req.id} processed successfully.")

                    except Exception as e:
                        req.retry_count += 1
                        req.error_message = str(e)
                        if req.retry_count >= 3:
                            req.status = 'FAILED'
                            print(f"  - Request {req.id} failed permanently: {e}")
                        else:
                            req.status = 'PENDING'
                            print(f"  - Request {req.id} failed, retry queued: {e}")
                            
                    db.session.commit()
            
            # Send batched digests
            send_batch_digests()
            
            # Sleep 3 seconds between queue polls
            time.sleep(3)
            
        except Exception as err:
            db.session.rollback()
            print(f"Queue Worker Loop Exception: {err}")
            time.sleep(3)

def send_batch_digests():
    from .models.notification_model import PendingEmailNotification
    from .models.staff_model import Staff
    from .models.super_admin_model import SuperAdmin
    from .utils.notifications import send_email_in_background
    from datetime import datetime
    import os

    # Query all pending email notifications
    all_pending = PendingEmailNotification.query.all()
    if not all_pending:
        return

    # Group by recipient
    grouped = {}
    for item in all_pending:
        key = (item.recipient_role, item.recipient_id)
        if key not in grouped:
            grouped[key] = []
        grouped[key].append(item)

    now = datetime.utcnow()
    coalesce_window_seconds = 60  # Wait 60 seconds after the first notification to allow batching

    for (role, user_id), items in grouped.items():
        # Sort items by created_at asc
        items.sort(key=lambda x: x.created_at)
        oldest_item = items[0]

        # Only process if oldest item has waited at least coalesce_window_seconds
        if (now - oldest_item.created_at).total_seconds() < coalesce_window_seconds:
            continue

        # Resolve recipient
        recipient = Staff.query.get(user_id) if role == 'staff' else SuperAdmin.query.get(user_id)
        if not recipient or not recipient.email:
            # Delete if no email or recipient not found
            for item in items:
                db.session.delete(item)
            db.session.commit()
            continue

        # Prepare digest subject and content
        count = len(items)
        if count == 1:
            subject = "New notification on ELA Academy"
            email_body_text = items[0].message
            target_link = items[0].target_link or '/'
        else:
            subject = f"You have {count} new updates on ELA Academy"
            # Build list of updates
            bullets = []
            for item in items:
                link = item.target_link or '/'
                bullets.append(f"<li>{item.message} (<a href='{os.getenv('FRONTEND_URL', 'http://localhost:5173')}{link}'>View</a>)</li>")
            from markupsafe import Markup
            email_body_text = Markup(f"<p>Here is a summary of your recent updates:</p><ul>{''.join(bullets)}</ul>")
            target_link = '/'

        # Send email
        frontend_url = os.getenv('FRONTEND_URL', 'http://localhost:5173')
        full_action_link = f"{frontend_url}{target_link}"
        email_data = {
            'message': email_body_text,
            'action_link': full_action_link
        }
        try:
            send_email_in_background(
                subject=subject,
                recipients=[recipient.email],
                template_data=email_data
            )
            # Delete processed items
            for item in items:
                db.session.delete(item)
            db.session.commit()
            print(f"[Digest Worker] Sent digest email with {count} updates to {recipient.email}")
        except Exception as e:
            db.session.rollback()
            print(f"[Digest Worker] Failed to send digest email: {e}")

def register_commands(app):
    app.cli.add_command(generate_invoices_command)
    app.cli.add_command(process_notifications_command)