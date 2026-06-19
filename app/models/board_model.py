import json
from app.models import db
from datetime import datetime

class Board(db.Model):
    __tablename__ = 'boards'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(255), nullable=True)
    is_private = db.Column(db.Boolean, default=False, nullable=False)
    custom_statuses = db.Column(db.Text, nullable=True)
    parent_id = db.Column(db.Integer, db.ForeignKey('boards.id', ondelete='CASCADE'), nullable=True)
    is_folder = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    groups = db.relationship('BoardGroup', backref='board', cascade='all, delete-orphan', lazy=True)
    access_members = db.relationship('BoardAccessMember', backref='board', cascade='all, delete-orphan', lazy=True)

    def to_dict(self):
        custom_statuses_val = None
        if self.custom_statuses:
            try:
                custom_statuses_val = json.loads(self.custom_statuses)
            except:
                pass
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'is_private': self.is_private,
            'custom_statuses': custom_statuses_val,
            'parent_id': self.parent_id,
            'is_folder': self.is_folder,
            'access_members': [member.to_dict() for member in self.access_members],
            'groups': [group.to_dict() for group in self.groups],
            'created_at': self.created_at.isoformat() + 'Z'
        }

class BoardAccessMember(db.Model):
    __tablename__ = 'board_access_members'

    id = db.Column(db.Integer, primary_key=True)
    board_id = db.Column(db.Integer, db.ForeignKey('boards.id', ondelete='CASCADE'), nullable=False)
    staff_id = db.Column(db.Integer, db.ForeignKey('staff.id', ondelete='CASCADE'), nullable=True)
    super_admin_id = db.Column(db.Integer, db.ForeignKey('super_admins.id', ondelete='CASCADE'), nullable=True)

    staff = db.relationship('Staff')
    super_admin = db.relationship('SuperAdmin')

    def to_dict(self):
        if self.super_admin:
            return {
                'id': self.super_admin.id,
                'name': self.super_admin.name,
                'email': self.super_admin.email,
                'role': 'superadmin'
            }

        if self.staff:
            return {
                'id': self.staff.id,
                'name': self.staff.name,
                'email': self.staff.email,
                'role': 'staff'
            }

        return {
            'id': None,
            'name': '',
            'email': '',
            'role': ''
        }

class BoardGroup(db.Model):
    __tablename__ = 'board_groups'

    id = db.Column(db.Integer, primary_key=True)
    board_id = db.Column(db.Integer, db.ForeignKey('boards.id', ondelete='CASCADE'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    color = db.Column(db.String(20), default='#673de6', nullable=False)
    position = db.Column(db.Integer, default=0)

    tasks = db.relationship('BoardTask', backref='group', cascade='all, delete-orphan', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'board_id': self.board_id,
            'name': self.name,
            'color': self.color,
            'position': self.position
        }

class BoardTask(db.Model):
    __tablename__ = 'board_tasks'

    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('board_groups.id', ondelete='CASCADE'), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    
    responsible_staff_id = db.Column(db.Integer, db.ForeignKey('staff.id', ondelete='SET NULL'), nullable=True)
    responsible_super_admin_id = db.Column(db.Integer, db.ForeignKey('super_admins.id', ondelete='SET NULL'), nullable=True)
    
    status = db.Column(db.String(50), default='Not Started', nullable=False) # 'Not Started', 'In Progress', 'Done'
    priority = db.Column(db.String(50), default='Normal', nullable=False) # 'Urgent', 'High', 'Normal', 'Low'
    
    start_date = db.Column(db.Date, nullable=True)
    due_date = db.Column(db.Date, nullable=True)
    category = db.Column(db.String(100), nullable=True)
    recurring_settings = db.Column(db.String(255), nullable=True)
    dependency_task_id = db.Column(db.Integer, db.ForeignKey('board_tasks.id', ondelete='SET NULL'), nullable=True)
    parent_task_id = db.Column(db.Integer, db.ForeignKey('board_tasks.id', ondelete='CASCADE'), nullable=True)
    tags = db.Column(db.Text, nullable=True)
    description_html = db.Column(db.Text, nullable=True)
    time_estimate_minutes = db.Column(db.Integer, nullable=True)
    
    notes = db.Column(db.String(500), nullable=True)
    position = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    responsible_staff = db.relationship('Staff')
    responsible_super_admin = db.relationship('SuperAdmin')
    assignees = db.relationship('BoardTaskAssignee', backref='task', cascade='all, delete-orphan', lazy=True)
    
    subtasks = db.relationship('BoardTask', backref=db.backref('parent', remote_side=[id]), cascade='all, delete-orphan', foreign_keys=[parent_task_id], lazy=True)
    dependency_task = db.relationship('BoardTask', remote_side=[id], foreign_keys=[dependency_task_id])
    
    updates = db.relationship('TaskUpdate', backref='task', cascade='all, delete-orphan', lazy=True)
    checklist_items = db.relationship('BoardTaskChecklistItem', backref='task', cascade='all, delete-orphan', order_by='BoardTaskChecklistItem.position', lazy=True)
    watchers = db.relationship('BoardTaskWatcher', backref='task', cascade='all, delete-orphan', lazy=True)
    attachments = db.relationship('BoardTaskAttachment', backref='task', cascade='all, delete-orphan', lazy=True)
    history_logs = db.relationship('BoardTaskHistory', backref='task', cascade='all, delete-orphan', order_by='BoardTaskHistory.created_at.desc()', lazy=True)
    time_entries = db.relationship('TaskTimeEntry', backref='task', cascade='all, delete-orphan', lazy=True)

    def to_dict(self):
        assignee_items = [assignee.to_dict() for assignee in self.assignees]
        if not assignee_items:
            if self.responsible_super_admin:
                assignee_items.append({
                    'id': self.responsible_super_admin.id,
                    'name': self.responsible_super_admin.name,
                    'email': self.responsible_super_admin.email,
                    'role': 'superadmin'
                })
            elif self.responsible_staff:
                assignee_items.append({
                    'id': self.responsible_staff.id,
                    'name': self.responsible_staff.name,
                    'email': self.responsible_staff.email,
                    'role': 'staff'
                })

        primary_assignee = assignee_items[0] if assignee_items else {}
        assignee_name = primary_assignee.get('name', "")
        assignee_email = primary_assignee.get('email', "")
        assignee_role = primary_assignee.get('role', "")
        assignee_id = primary_assignee.get('id')
        
        return {
            'id': self.id,
            'group_id': self.group_id,
            'title': self.title,
            'status': self.status,
            'priority': self.priority,
            'start_date': self.start_date.isoformat() if self.start_date else None,
            'due_date': self.due_date.isoformat() if self.due_date else None,
            'category': self.category,
            'recurring_settings': self.recurring_settings,
            'dependency_task_id': self.dependency_task_id,
            'parent_task_id': self.parent_task_id,
            'tags': self.tags,
            'description_html': self.description_html,
            'notes': self.notes,
            'position': self.position,
            'assignee_id': assignee_id,
            'assignee_name': assignee_name,
            'assignee_email': assignee_email,
            'assignee_role': assignee_role,
            'assignees': assignee_items,
            'assignee_ids': [item['id'] for item in assignee_items],
            'assignee_names': [item['name'] for item in assignee_items],
            'updates_count': len(self.updates),
            'time_estimate_minutes': self.time_estimate_minutes,
            'time_spent_seconds': sum(entry.duration_seconds for entry in self.time_entries) if hasattr(self, 'time_entries') else 0,
            'time_entries': [entry.to_dict() for entry in self.time_entries] if hasattr(self, 'time_entries') else [],
            'checklist': [item.to_dict() for item in self.checklist_items],
            'watchers': [watcher.to_dict() for watcher in self.watchers],
            'attachments': [attachment.to_dict() for attachment in self.attachments],
            'subtasks': [{
                'id': sub.id,
                'title': sub.title,
                'status': sub.status,
                'priority': sub.priority,
                'due_date': sub.due_date.isoformat() if sub.due_date else None,
                'assignees': [assignee.to_dict() for assignee in sub.assignees]
            } for sub in self.subtasks],
            'created_at': self.created_at.isoformat() + 'Z'
        }


class BoardTaskAssignee(db.Model):
    __tablename__ = 'board_task_assignees'

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('board_tasks.id', ondelete='CASCADE'), nullable=False)
    staff_id = db.Column(db.Integer, db.ForeignKey('staff.id', ondelete='CASCADE'), nullable=True)
    super_admin_id = db.Column(db.Integer, db.ForeignKey('super_admins.id', ondelete='CASCADE'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    staff = db.relationship('Staff')
    super_admin = db.relationship('SuperAdmin')

    def to_dict(self):
        if self.super_admin:
            return {
                'id': self.super_admin.id,
                'name': self.super_admin.name,
                'email': self.super_admin.email,
                'role': 'superadmin'
            }
        if self.staff:
            return {
                'id': self.staff.id,
                'name': self.staff.name,
                'email': self.staff.email,
                'role': 'staff'
            }
        return {'id': None, 'name': '', 'email': '', 'role': ''}

class BoardTaskChecklistItem(db.Model):
    __tablename__ = 'board_task_checklist_items'

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('board_tasks.id', ondelete='CASCADE'), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    is_checked = db.Column(db.Boolean, default=False, nullable=False)
    position = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'task_id': self.task_id,
            'title': self.title,
            'is_checked': self.is_checked,
            'position': self.position,
            'created_at': self.created_at.isoformat() + 'Z'
        }

class BoardTaskWatcher(db.Model):
    __tablename__ = 'board_task_watchers'

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('board_tasks.id', ondelete='CASCADE'), nullable=False)
    staff_id = db.Column(db.Integer, db.ForeignKey('staff.id', ondelete='CASCADE'), nullable=True)
    super_admin_id = db.Column(db.Integer, db.ForeignKey('super_admins.id', ondelete='CASCADE'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    staff = db.relationship('Staff')
    super_admin = db.relationship('SuperAdmin')

    def to_dict(self):
        if self.super_admin:
            return {
                'id': self.super_admin.id,
                'name': self.super_admin.name,
                'email': self.super_admin.email,
                'role': 'superadmin'
            }
        if self.staff:
            return {
                'id': self.staff.id,
                'name': self.staff.name,
                'email': self.staff.email,
                'role': 'staff'
            }
        return {
            'id': None,
            'name': '',
            'email': '',
            'role': ''
        }

class BoardTaskAttachment(db.Model):
    __tablename__ = 'board_task_attachments'

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('board_tasks.id', ondelete='CASCADE'), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    file_path = db.Column(db.String(500), nullable=False)
    uploaded_by_name = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'task_id': self.task_id,
            'filename': self.filename,
            'file_path': self.file_path,
            'uploaded_by_name': self.uploaded_by_name,
            'created_at': self.created_at.isoformat() + 'Z'
        }

class BoardTaskHistory(db.Model):
    __tablename__ = 'board_task_history'

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('board_tasks.id', ondelete='CASCADE'), nullable=False)
    actor_name = db.Column(db.String(100), nullable=False)
    action = db.Column(db.String(500), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'task_id': self.task_id,
            'actor_name': self.actor_name,
            'action': self.action,
            'created_at': self.created_at.isoformat() + 'Z'
        }

class BoardTaskTemplate(db.Model):
    __tablename__ = 'board_task_templates'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    notes = db.Column(db.Text, nullable=True)
    priority = db.Column(db.String(50), default='Normal', nullable=False)
    category = db.Column(db.String(100), nullable=True)
    tags = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'notes': self.notes,
            'priority': self.priority,
            'category': self.category,
            'tags': self.tags,
            'created_at': self.created_at.isoformat() + 'Z'
        }


class CalendarEvent(db.Model):
    __tablename__ = 'calendar_events'

    id = db.Column(db.Integer, primary_key=True)
    board_id = db.Column(db.Integer, db.ForeignKey('boards.id', ondelete='CASCADE'), nullable=True)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    start_datetime = db.Column(db.DateTime, nullable=False)
    end_datetime = db.Column(db.DateTime, nullable=True)
    all_day = db.Column(db.Boolean, default=False)
    color = db.Column(db.String(20), default='#673de6')
    recurring_rule = db.Column(db.String(255), nullable=True)
    reminder_minutes = db.Column(db.Integer, nullable=True)
    created_by_name = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    linked_task_id = db.Column(db.Integer, db.ForeignKey('board_tasks.id', ondelete='SET NULL'), nullable=True)

    board = db.relationship('Board')
    linked_task = db.relationship('BoardTask')

    def to_dict(self):
        return {
            'id': self.id,
            'board_id': self.board_id,
            'title': self.title,
            'description': self.description,
            'start_datetime': self.start_datetime.isoformat() + 'Z' if self.start_datetime else None,
            'end_datetime': self.end_datetime.isoformat() + 'Z' if self.end_datetime else None,
            'all_day': self.all_day,
            'color': self.color,
            'recurring_rule': self.recurring_rule,
            'reminder_minutes': self.reminder_minutes,
            'created_by_name': self.created_by_name,
            'linked_task_id': self.linked_task_id,
            'created_at': self.created_at.isoformat() + 'Z'
        }


class TaskTimeEntry(db.Model):
    __tablename__ = 'task_time_entries'

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('board_tasks.id', ondelete='CASCADE'), nullable=False)
    user_name = db.Column(db.String(100), nullable=False)
    user_role = db.Column(db.String(50), nullable=False)
    user_id = db.Column(db.Integer, nullable=False)
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime, nullable=True) # None if currently running
    duration_seconds = db.Column(db.Integer, default=0) # 0 if currently running
    description = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'task_id': self.task_id,
            'user_name': self.user_name,
            'user_role': self.user_role,
            'user_id': self.user_id,
            'start_time': self.start_time.isoformat() + 'Z' if self.start_time else None,
            'end_time': self.end_time.isoformat() + 'Z' if self.end_time else None,
            'duration_seconds': self.duration_seconds,
            'description': self.description,
            'created_at': self.created_at.isoformat() + 'Z'
        }


class WorkspaceDoc(db.Model):
    __tablename__ = 'workspace_docs'

    id = db.Column(db.Integer, primary_key=True)
    board_id = db.Column(db.Integer, db.ForeignKey('boards.id', ondelete='CASCADE'), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    content_html = db.Column(db.Text, default='', nullable=False)
    created_by_name = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    position = db.Column(db.Integer, default=0)

    board = db.relationship('Board')

    def to_dict(self):
        return {
            'id': self.id,
            'board_id': self.board_id,
            'title': self.title,
            'content_html': self.content_html,
            'created_by_name': self.created_by_name,
            'created_at': self.created_at.isoformat() + 'Z',
            'updated_at': self.updated_at.isoformat() + 'Z',
            'position': self.position
        }

