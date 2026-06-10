from app.models import db
from datetime import datetime

class Board(db.Model):
    __tablename__ = 'boards'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(255), nullable=True)
    is_private = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    groups = db.relationship('BoardGroup', backref='board', cascade='all, delete-orphan', lazy=True)
    access_members = db.relationship('BoardAccessMember', backref='board', cascade='all, delete-orphan', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'is_private': self.is_private,
            'access_members': [member.to_dict() for member in self.access_members],
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
    
    due_date = db.Column(db.Date, nullable=True)
    notes = db.Column(db.String(500), nullable=True)
    position = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    responsible_staff = db.relationship('Staff')
    responsible_super_admin = db.relationship('SuperAdmin')
    updates = db.relationship('TaskUpdate', backref='task', cascade='all, delete-orphan', lazy=True)

    def to_dict(self):
        assignee_name = ""
        assignee_email = ""
        assignee_role = ""
        assignee_id = None
        
        if self.responsible_super_admin:
            assignee_name = self.responsible_super_admin.name
            assignee_email = self.responsible_super_admin.email
            assignee_role = "superadmin"
            assignee_id = self.responsible_super_admin.id
        elif self.responsible_staff:
            assignee_name = self.responsible_staff.name
            assignee_email = self.responsible_staff.email
            assignee_role = "staff"
            assignee_id = self.responsible_staff.id
            
        return {
            'id': self.id,
            'group_id': self.group_id,
            'title': self.title,
            'status': self.status,
            'priority': self.priority,
            'due_date': self.due_date.isoformat() if self.due_date else None,
            'notes': self.notes,
            'position': self.position,
            'assignee_id': assignee_id,
            'assignee_name': assignee_name,
            'assignee_email': assignee_email,
            'assignee_role': assignee_role,
            'updates_count': len(self.updates),
            'created_at': self.created_at.isoformat() + 'Z'
        }
