"""add login_otps table

Revision ID: 7c9b1be6e0b6
Revises: 8b9d3c1e2f3a
Create Date: 2026-06-20 04:09:45.289687

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = '7c9b1be6e0b6'
down_revision = '8b9d3c1e2f3a'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = inspector.get_table_names()

    # Create login_otps table if it doesn't exist
    if 'login_otps' not in tables:
        op.create_table('login_otps',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('email', sa.String(length=120), nullable=False),
            sa.Column('otp', sa.String(length=10), nullable=False),
            sa.Column('role', sa.String(length=50), nullable=False),
            sa.Column('_claims', sa.Text(), nullable=True),
            sa.Column('expiry', sa.DateTime(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id')
        )
        print("Successfully created login_otps table")

    # Drop index if it exists in board_task_assignees table
    if 'board_task_assignees' in tables:
        # Check if the index exists
        indexes = [idx['name'] for idx in inspector.get_indexes('board_task_assignees')]
        if 'ix_board_task_assignees_task_id' in indexes:
            with op.batch_alter_table('board_task_assignees', schema=None) as batch_op:
                batch_op.drop_index('ix_board_task_assignees_task_id')
            print("Successfully dropped index ix_board_task_assignees_task_id")


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = inspector.get_table_names()

    if 'board_task_assignees' in tables:
        indexes = [idx['name'] for idx in inspector.get_indexes('board_task_assignees')]
        if 'ix_board_task_assignees_task_id' not in indexes:
            with op.batch_alter_table('board_task_assignees', schema=None) as batch_op:
                batch_op.create_index('ix_board_task_assignees_task_id', ['task_id'], unique=False)
            print("Successfully restored index ix_board_task_assignees_task_id")

    if 'login_otps' in tables:
        op.drop_table('login_otps')
        print("Successfully dropped login_otps table")
