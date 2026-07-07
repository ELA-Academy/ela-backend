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


        if sub.cycle == 'Monthly':
            sub.next_invoice_date += relativedelta(months=1)
        
        print(f"  - Invoice created. Next invoice date set to: {sub.next_invoice_date.isoformat()}")

    try:
        db.session.commit()
        print("Successfully committed all new invoices to the database.")
    except Exception as e:
        db.session.rollback()
        print(f"An error occurred. Rolling back changes. Error: {e}")

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
    
    while True:
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

                        # B. Deliver Email
                        if 'email' in channels:
                            frontend_url = os.getenv('FRONTEND_URL', 'http://localhost:5173')
                            full_action_link = f"{frontend_url}{req.target_link or '/'}"
                            email_data = { 'message': req.message, 'action_link': full_action_link }
                            send_email_in_background(
                                subject="You have a new notification",
                                recipients=[recipient.email],
                                template_data=email_data
                            )

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
            
            # Sleep 3 seconds between queue polls
            time.sleep(3)
            
        except Exception as err:
            print(f"Queue Worker Loop Exception: {err}")
            time.sleep(3)

def register_commands(app):
    app.cli.add_command(generate_invoices_command)
    app.cli.add_command(process_notifications_command)