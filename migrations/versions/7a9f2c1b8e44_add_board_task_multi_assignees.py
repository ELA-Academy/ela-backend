"""Add multi assignees for board tasks

Revision ID: 7a9f2c1b8e44
Revises: 0130e4cadfcc
Create Date: 2026-06-18 04:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = '7a9f2c1b8e44'
down_revision = '0130e4cadfcc'
branch_labels = None
depends_on = None


def upgrade():
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
    op.create_index('ix_board_task_assignees_task_id', 'board_task_assignees', ['task_id'])

    op.execute("""
        INSERT INTO board_task_assignees (task_id, staff_id, super_admin_id, created_at)
        SELECT id, responsible_staff_id, NULL, created_at
        FROM board_tasks
        WHERE responsible_staff_id IS NOT NULL
    """)
    op.execute("""
        INSERT INTO board_task_assignees (task_id, staff_id, super_admin_id, created_at)
        SELECT id, NULL, responsible_super_admin_id, created_at
        FROM board_tasks
        WHERE responsible_super_admin_id IS NOT NULL
    """)


def downgrade():
    op.drop_index('ix_board_task_assignees_task_id', table_name='board_task_assignees')
    op.drop_table('board_task_assignees')
