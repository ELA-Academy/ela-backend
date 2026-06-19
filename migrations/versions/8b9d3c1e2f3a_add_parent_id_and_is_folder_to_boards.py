"""add parent_id and is_folder to boards

Revision ID: 8b9d3c1e2f3a
Revises: 7a9f2c1b8e44
Create Date: 2026-06-19 14:15:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = '8b9d3c1e2f3a'
down_revision = '7a9f2c1b8e44'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    
    columns = [col['name'] for col in inspector.get_columns('boards')]
    
    if 'parent_id' not in columns:
        op.add_column('boards', sa.Column('parent_id', sa.Integer(), nullable=True))
        op.create_foreign_key(
            'boards_parent_id_fkey', 
            'boards', 
            'boards', 
            ['parent_id'], 
            ['id'], 
            ondelete='CASCADE'
        )
        print("Successfully added parent_id column and foreign key constraint")
        
    if 'is_folder' not in columns:
        op.add_column('boards', sa.Column('is_folder', sa.Boolean(), server_default='false', nullable=False))
        print("Successfully added is_folder column")


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    
    columns = [col['name'] for col in inspector.get_columns('boards')]
    
    if 'parent_id' in columns:
        op.drop_constraint('boards_parent_id_fkey', 'boards', type_='foreignkey')
        op.drop_column('boards', 'parent_id')
        
    if 'is_folder' in columns:
        op.drop_column('boards', 'is_folder')
