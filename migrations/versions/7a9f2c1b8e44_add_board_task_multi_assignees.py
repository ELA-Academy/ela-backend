"""Add multi assignees for board tasks

Revision ID: 7a9f2c1b8e44
Revises: 0130e4cadfcc
Create Date: 2026-06-18 04:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = '7a9f2c1b8e44'
down_revision = '0130e4cadfcc'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    if not inspector.has_table('board_task_assignees'):
        op.create_table(
            'board_task_assignees',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('task_id', sa.Integer(), nullable=False),
            sa.Column('staff_id', sa.Integer(), nullable=True),
            sa.Column('super_admin_id', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['staff_id'], ['staff.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['super_admin_id'], ['super_admins.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['task_id'], ['board_tasks.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )

    existing_indexes = {
        index['name'] for index in inspector.get_indexes('board_task_assignees')
    }
    if 'ix_board_task_assignees_task_id' not in existing_indexes:
        op.create_index('ix_board_task_assignees_task_id', 'board_task_assignees', ['task_id'])

    op.execute("""
        INSERT INTO board_task_assignees (task_id, staff_id, super_admin_id, created_at)
        SELECT id, responsible_staff_id, NULL, created_at
        FROM board_tasks
        WHERE responsible_staff_id IS NOT NULL
        AND NOT EXISTS (
            SELECT 1 FROM board_task_assignees
            WHERE board_task_assignees.task_id = board_tasks.id
            AND board_task_assignees.staff_id = board_tasks.responsible_staff_id
        )
    """)
    op.execute("""
        INSERT INTO board_task_assignees (task_id, staff_id, super_admin_id, created_at)
        SELECT id, NULL, responsible_super_admin_id, created_at
        FROM board_tasks
        WHERE responsible_super_admin_id IS NOT NULL
        AND NOT EXISTS (
            SELECT 1 FROM board_task_assignees
            WHERE board_task_assignees.task_id = board_tasks.id
            AND board_task_assignees.super_admin_id = board_tasks.responsible_super_admin_id
        )
    """)


def downgrade():
    op.drop_index('ix_board_task_assignees_task_id', table_name='board_task_assignees')
    op.drop_table('board_task_assignees')
