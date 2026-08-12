"""add notification_preferences, payment status, generated_reports, and student_documents tables

Revision ID: f78e4d8a5c2b
Revises: e91d8f5c3b2a
Create Date: 2026-08-11 15:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = 'f78e4d8a5c2b'
down_revision = 'e91d8f5c3b2a'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    
    # Check super_admins table
    columns_sa = [col['name'] for col in inspector.get_columns('super_admins')]
    if 'notification_preferences' not in columns_sa:
        op.add_column('super_admins', sa.Column('notification_preferences', sa.Text(), nullable=True))
        print("Successfully added notification_preferences column to super_admins")

    # Check staff table
    columns_staff = [col['name'] for col in inspector.get_columns('staff')]
    if 'notification_preferences' not in columns_staff:
        op.add_column('staff', sa.Column('notification_preferences', sa.Text(), nullable=True))
        print("Successfully added notification_preferences column to staff")

    # Check payments table
    columns_payments = [col['name'] for col in inspector.get_columns('payments')]
    if 'status' not in columns_payments:
        op.add_column('payments', sa.Column('status', sa.String(length=50), server_default='Success', nullable=False))
        print("Successfully added status column to payments")

    # Create generated_reports table if missing
    if not inspector.has_table('generated_reports'):
        op.create_table(
            'generated_reports',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(length=150), nullable=False),
            sa.Column('category', sa.String(length=100), nullable=False),
            sa.Column('format', sa.String(length=50), nullable=False),
            sa.Column('file_path', sa.String(length=255), nullable=False),
            sa.Column('date_range', sa.String(length=100), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('created_by_id', sa.Integer(), nullable=True),
            sa.Column('created_by_role', sa.String(length=50), nullable=True),
            sa.PrimaryKeyConstraint('id')
        )
        print("Successfully created generated_reports table")

    # Create student_documents table if missing
    if not inspector.has_table('student_documents'):
        op.create_table(
            'student_documents',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('student_id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(length=255), nullable=False),
            sa.Column('file_path', sa.String(length=500), nullable=False),
            sa.Column('expiry_date', sa.Date(), nullable=True),
            sa.Column('document_type', sa.String(length=100), nullable=True),
            sa.Column('status', sa.String(length=50), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['student_id'], ['students.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id')
        )
        print("Successfully created student_documents table")


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    
    # super_admins table
    columns_sa = [col['name'] for col in inspector.get_columns('super_admins')]
    if 'notification_preferences' in columns_sa:
        op.drop_column('super_admins', 'notification_preferences')

    # staff table
    columns_staff = [col['name'] for col in inspector.get_columns('staff')]
    if 'notification_preferences' in columns_staff:
        op.drop_column('staff', 'notification_preferences')

    # payments table
    columns_payments = [col['name'] for col in inspector.get_columns('payments')]
    if 'status' in columns_payments:
        op.drop_column('payments', 'status')

    # generated_reports table
    if inspector.has_table('generated_reports'):
        op.drop_table('generated_reports')

    # student_documents table
    if inspector.has_table('student_documents'):
        op.drop_table('student_documents')
