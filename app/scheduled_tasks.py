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

def register_commands(app):
    app.cli.add_command(generate_invoices_command)