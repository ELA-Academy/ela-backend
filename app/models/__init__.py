from sqlalchemy import inspect, text
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def ensure_runtime_schema_updates():
    def add_column_if_missing(table_name, column_name, sql_definition):
        inspector = inspect(db.engine)
        existing_columns = {column['name'] for column in inspector.get_columns(table_name)}
        if column_name in existing_columns:
            return
        db.session.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {sql_definition}"))
        db.session.commit()

    inspector = inspect(db.engine)
    if inspector.has_table('super_admins'):
        add_column_if_missing('super_admins', 'is_active', 'BOOLEAN NOT NULL DEFAULT TRUE')

    if inspector.has_table('conversations'):
        add_column_if_missing('conversations', 'conversation_type', "VARCHAR(50) NOT NULL DEFAULT 'direct'")
        add_column_if_missing('conversations', 'name', 'VARCHAR(150) NULL')
        add_column_if_missing('conversations', 'department_id', 'INTEGER NULL')

    if inspector.has_table('boards'):
        add_column_if_missing('boards', 'is_private', 'BOOLEAN NOT NULL DEFAULT FALSE')

def init_db(app):
    """Initialize the SQLAlchemy database with the Flask app."""
    db.init_app(app)

    # Import models here to ensure they're registered before creating tables
    with app.app_context():
        from app.models.super_admin_model import SuperAdmin
        from app.models.department_model import Department
        from app.models.staff_model import Staff
        from app.models.lead_model import Lead, LeadStudent, LeadParent
        from app.models.task_model import Task
        from app.models.student_model import Student, Parent
        from app.models.activity_log_model import ActivityLog
        from app.models.notification_model import Notification
        from app.models.conversation_model import Conversation, Message, ConversationParticipant
        from app.models.push_subscription_model import PushSubscription
        from app.models.enrollment_form_model import EnrollmentForm
        from app.models.enrollment_submission_model import EnrollmentSubmission
        from app.models.financial_model import (
            StudentFinancialAccount, PresetChargeItem, Invoice, 
            InvoiceItem, Payment, Credit, BillingPlan, Subscription,
            PresetDiscount
        )
        from app.models.subsidy_model import Subsidy
        from app.models.message_log_model import MessageLog
        from app.models.subsidy_transaction_model import SubsidyTransaction, SubsidyPaymentDistribution
        
        # Workspace Collaboration Models
        from app.models.board_model import Board, BoardAccessMember, BoardGroup, BoardTask
        from app.models.task_update_model import TaskUpdate, TaskUpdateReply, TaskUpdateLike
        
        db.create_all()
        ensure_runtime_schema_updates()
