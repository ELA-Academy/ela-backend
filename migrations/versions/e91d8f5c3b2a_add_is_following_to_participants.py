"""add is_following to participants

Revision ID: e91d8f5c3b2a
Revises: 7c9b1be6e0b6
Create Date: 2026-06-24 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = 'e91d8f5c3b2a'
down_revision = '7c9b1be6e0b6'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    
    columns = [col['name'] for col in inspector.get_columns('conversation_participants')]
    
    if 'is_following' not in columns:
        op.add_column('conversation_participants', sa.Column('is_following', sa.Boolean(), server_default='true', nullable=False))
        print("Successfully added is_following column to conversation_participants")


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    
    columns = [col['name'] for col in inspector.get_columns('conversation_participants')]
    
    if 'is_following' in columns:
        op.drop_column('conversation_participants', 'is_following')
