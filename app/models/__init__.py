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
        add_column_if_missing('super_admins', 'notification_preferences', 'TEXT NULL')

    if inspector.has_table('staff'):
        add_column_if_missing('staff', 'notification_preferences', 'TEXT NULL')

    if inspector.has_table('conversations'):
        add_column_if_missing('conversations', 'conversation_type', "VARCHAR(50) NOT NULL DEFAULT 'direct'")
        add_column_if_missing('conversations', 'name', 'VARCHAR(150) NULL')
        add_column_if_missing('conversations', 'department_id', 'INTEGER NULL')

    if inspector.has_table('boards'):
        add_column_if_missing('boards', 'is_private', 'BOOLEAN NOT NULL DEFAULT FALSE')
        add_column_if_missing('boards', 'custom_statuses', 'TEXT NULL')
        add_column_if_missing('boards', 'color', 'VARCHAR(50) NULL')
        add_column_if_missing('boards', 'icon', 'VARCHAR(50) NULL')
        add_column_if_missing('boards', 'is_template', 'BOOLEAN NOT NULL DEFAULT FALSE')
        add_column_if_missing('boards', 'is_archived', 'BOOLEAN NOT NULL DEFAULT FALSE')
        add_column_if_missing('boards', 'status', "VARCHAR(50) NOT NULL DEFAULT 'Not Started'")
        add_column_if_missing('boards', 'priority', "VARCHAR(50) NOT NULL DEFAULT 'Normal'")
        add_column_if_missing('boards', 'category', 'VARCHAR(100) NULL')
        add_column_if_missing('boards', 'budget_amount', 'FLOAT NULL')
        add_column_if_missing('boards', 'is_personal', 'BOOLEAN NOT NULL DEFAULT FALSE')
        add_column_if_missing('boards', 'owner_staff_id', 'INTEGER NULL')
        add_column_if_missing('boards', 'owner_super_admin_id', 'INTEGER NULL')

    if inspector.has_table('calendar_events'):
        add_column_if_missing('calendar_events', 'reminder_sent', 'BOOLEAN NOT NULL DEFAULT FALSE')

    if inspector.has_table('board_tasks'):
        add_column_if_missing('board_tasks', 'start_date', 'DATE NULL')
        add_column_if_missing('board_tasks', 'category', 'VARCHAR(100) NULL')
        add_column_if_missing('board_tasks', 'recurring_settings', 'VARCHAR(255) NULL')
        add_column_if_missing('board_tasks', 'dependency_task_id', 'INTEGER NULL')
        add_column_if_missing('board_tasks', 'parent_task_id', 'INTEGER NULL')
        add_column_if_missing('board_tasks', 'tags', 'TEXT NULL')
        add_column_if_missing('board_tasks', 'description_html', 'TEXT NULL')
        add_column_if_missing('board_tasks', 'time_estimate_minutes', 'INTEGER NULL')

    if inspector.has_table('task_time_entries'):
        add_column_if_missing('task_time_entries', 'is_billable', 'BOOLEAN NOT NULL DEFAULT FALSE')

    if inspector.has_table('messages'):
        add_column_if_missing('messages', 'file_path', 'VARCHAR(500) NULL')
        add_column_if_missing('messages', 'filename', 'VARCHAR(255) NULL')
        add_column_if_missing('messages', 'reply_to_message_id', 'INTEGER NULL')

    if inspector.has_table('workspace_docs'):
        add_column_if_missing('workspace_docs', 'is_public', 'BOOLEAN NOT NULL DEFAULT TRUE')
        add_column_if_missing('workspace_docs', 'shared_user_ids', 'TEXT NULL')
        add_column_if_missing('workspace_docs', 'shared_dept_ids', 'TEXT NULL')

    if inspector.has_table('conversation_participants'):
        add_column_if_missing('conversation_participants', 'is_following', 'BOOLEAN NOT NULL DEFAULT TRUE')

def init_db(app):
    """Initialize the SQLAlchemy database with the Flask app."""
    db.init_app(app)

    # Import models here to ensure they're registered before creating tables
    with app.app_context():
        from app.models.super_admin_model import SuperAdmin
        from app.models.login_otp_model import LoginOTP
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
        from app.models.board_model import Board, BoardAccessMember, BoardGroup, BoardTask, CalendarEvent, TaskTimeEntry, WorkspaceDoc, WorkspaceDocComment, BoardMilestone
        from app.models.task_update_model import TaskUpdate, TaskUpdateReply, TaskUpdateLike
        from app.models.announcement_model import Announcement
        from app.models.board_model_extensions import (
            BoardCustomField, TaskCustomFieldValue,
            BoardFormConfig, BoardFormResponse,
            WorkspaceDocumentFolder, WorkspaceDocumentFile, WorkspaceDocumentFileVersion
        )
        
        db.create_all()
        ensure_runtime_schema_updates()
