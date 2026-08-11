"""add notification_preferences column

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
