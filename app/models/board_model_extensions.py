import json
from app.models import db
from datetime import datetime

# ==========================================
# 1. Custom Fields Models
# ==========================================

class BoardCustomField(db.Model):
    __tablename__ = 'board_custom_fields'

    id = db.Column(db.Integer, primary_key=True)
    board_id = db.Column(db.Integer, db.ForeignKey('boards.id', ondelete='CASCADE'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    type = db.Column(db.String(50), nullable=False)  # 'text', 'number', 'date', 'dropdown', 'multi_select', 'currency', 'formula', 'rating'
    config_json = db.Column(db.Text, nullable=True) # options for dropdowns, formulas, etc.
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    values = db.relationship('TaskCustomFieldValue', backref='field', cascade='all, delete-orphan', lazy=True)

    def to_dict(self):
        config_val = None
        if self.config_json:
            try:
                config_val = json.loads(self.config_json)
            except:
                config_val = self.config_json
        from app.models.board_model import Board
        board = Board.query.get(self.board_id)
        board_uuid = board.public_id if board else self.board_id
        return {
            'id': self.id,
            'board_id': board_uuid,
            'name': self.name,
            'type': self.type,
            'config': config_val,
            'created_at': self.created_at.isoformat() + 'Z'
        }

class TaskCustomFieldValue(db.Model):
    __tablename__ = 'task_custom_field_values'

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('board_tasks.id', ondelete='CASCADE'), nullable=False)
    field_id = db.Column(db.Integer, db.ForeignKey('board_custom_fields.id', ondelete='CASCADE'), nullable=False)
    value_json = db.Column(db.Text, nullable=True) # Stored as json string to handle lists, dates, numbers, strings
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        val = None
        if self.value_json:
            try:
                val = json.loads(self.value_json)
            except:
                val = self.value_json
        return {
            'id': self.id,
            'task_id': self.task_id,
            'field_id': self.field_id,
            'value': val,
            'updated_at': self.updated_at.isoformat() + 'Z'
        }


# ==========================================
# 2. Workspace Forms Models
# ==========================================

class BoardFormConfig(db.Model):
    __tablename__ = 'board_form_configs'

    id = db.Column(db.Integer, primary_key=True)
    board_id = db.Column(db.Integer, db.ForeignKey('boards.id', ondelete='CASCADE'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=True)
    form_structure_json = db.Column(db.Text, nullable=False) # Questions list with mappings to task fields or custom fields
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    header_image_url = db.Column(db.String(500), nullable=True)
    creator_staff_id = db.Column(db.Integer, db.ForeignKey('staff.id', ondelete='SET NULL'), nullable=True)
    creator_super_admin_id = db.Column(db.Integer, db.ForeignKey('super_admins.id', ondelete='SET NULL'), nullable=True)
    target_department_id = db.Column(db.Integer, db.ForeignKey('departments.id', ondelete='SET NULL'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    target_department = db.relationship('Department')

    def to_dict(self):
        struct = []
        if self.form_structure_json:
            try:
                struct = json.loads(self.form_structure_json)
            except:
                pass
        from app.models.board_model import Board
        board = Board.query.get(self.board_id)
        board_uuid = board.public_id if board else self.board_id
        return {
            'id': self.id,
            'board_id': board_uuid,
            'name': self.name,
            'description': self.description,
            'form_structure': struct,
            'is_active': self.is_active,
            'header_image_url': self.header_image_url,
            'creator_staff_id': self.creator_staff_id,
            'creator_super_admin_id': self.creator_super_admin_id,
            'target_department_id': self.target_department_id,
            'target_department_name': self.target_department.name if self.target_department else None,
            'created_at': self.created_at.isoformat() + 'Z'
        }

class BoardFormResponse(db.Model):
    __tablename__ = 'board_form_responses'

    id = db.Column(db.Integer, primary_key=True)
    form_id = db.Column(db.Integer, db.ForeignKey('board_form_configs.id', ondelete='CASCADE'), nullable=False)
    response_json = db.Column(db.Text, nullable=False) # Answers matching question ids
    created_task_id = db.Column(db.Integer, db.ForeignKey('board_tasks.id', ondelete='SET NULL'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    form = db.relationship('BoardFormConfig')
    created_task = db.relationship('BoardTask')

    def to_dict(self):
        resp = {}
        if self.response_json:
            try:
                resp = json.loads(self.response_json)
            except:
                pass
        return {
            'id': self.id,
            'form_id': self.form_id,
            'response': resp,
            'created_task_id': self.created_task_id,
            'created_at': self.created_at.isoformat() + 'Z'
        }


# ==========================================
# 3. Document Management Models
# ==========================================

class WorkspaceDocumentFolder(db.Model):
    __tablename__ = 'workspace_document_folders'

    id = db.Column(db.Integer, primary_key=True)
    board_id = db.Column(db.Integer, db.ForeignKey('boards.id', ondelete='CASCADE'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey('workspace_document_folders.id', ondelete='CASCADE'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    subfolders = db.relationship('WorkspaceDocumentFolder', backref=db.backref('parent', remote_side=[id]), cascade='all, delete-orphan')
    files = db.relationship('WorkspaceDocumentFile', backref='folder', cascade='all, delete-orphan')

    def to_dict(self):
        from app.models.board_model import Board
        board = Board.query.get(self.board_id)
        board_uuid = board.public_id if board else self.board_id
        return {
            'id': self.id,
            'board_id': board_uuid,
            'name': self.name,
            'parent_id': self.parent_id,
            'created_at': self.created_at.isoformat() + 'Z'
        }

class WorkspaceDocumentFile(db.Model):
    __tablename__ = 'workspace_document_files'

    id = db.Column(db.Integer, primary_key=True)
    board_id = db.Column(db.Integer, db.ForeignKey('boards.id', ondelete='CASCADE'), nullable=False)
    folder_id = db.Column(db.Integer, db.ForeignKey('workspace_document_folders.id', ondelete='CASCADE'), nullable=True)
    filename = db.Column(db.String(255), nullable=False)
    file_path = db.Column(db.String(500), nullable=False)
    file_type = db.Column(db.String(50), nullable=False) # 'pdf', 'word', 'excel', 'powerpoint', 'image', 'video', 'other'
    version = db.Column(db.Integer, default=1)
    is_shared = db.Column(db.Boolean, default=False)
    permissions_json = db.Column(db.Text, nullable=True) # Permissions details
    uploaded_by_name = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    versions = db.relationship('WorkspaceDocumentFileVersion', backref='file', cascade='all, delete-orphan')

    def to_dict(self):
        perms = {}
        if self.permissions_json:
            try:
                perms = json.loads(self.permissions_json)
            except:
                pass
        from app.models.board_model import Board
        board = Board.query.get(self.board_id)
        board_uuid = board.public_id if board else self.board_id
        return {
            'id': self.id,
            'board_id': board_uuid,
            'folder_id': self.folder_id,
            'filename': self.filename,
            'file_path': self.file_path,
            'file_type': self.file_type,
            'version': self.version,
            'is_shared': self.is_shared,
            'permissions': perms,
            'uploaded_by_name': self.uploaded_by_name,
            'created_at': self.created_at.isoformat() + 'Z'
        }

class WorkspaceDocumentFileVersion(db.Model):
    __tablename__ = 'workspace_document_file_versions'

    id = db.Column(db.Integer, primary_key=True)
    file_id = db.Column(db.Integer, db.ForeignKey('workspace_document_files.id', ondelete='CASCADE'), nullable=False)
    version_number = db.Column(db.Integer, nullable=False)
    file_path = db.Column(db.String(500), nullable=False)
    uploaded_by_name = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'file_id': self.file_id,
            'version_number': self.version_number,
            'file_path': self.file_path,
            'uploaded_by_name': self.uploaded_by_name,
            'created_at': self.created_at.isoformat() + 'Z'
        }
