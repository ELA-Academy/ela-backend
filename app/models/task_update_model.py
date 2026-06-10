from app.models import db
from datetime import datetime

class TaskUpdate(db.Model):
    __tablename__ = 'task_updates'

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('board_tasks.id', ondelete='CASCADE'), nullable=False)
    
    sender_staff_id = db.Column(db.Integer, db.ForeignKey('staff.id', ondelete='SET NULL'), nullable=True)
    sender_super_admin_id = db.Column(db.Integer, db.ForeignKey('super_admins.id', ondelete='SET NULL'), nullable=True)
    sender_name = db.Column(db.String(100), nullable=False)
    
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    sender_staff = db.relationship('Staff')
    sender_super_admin = db.relationship('SuperAdmin')
    
    replies = db.relationship('TaskUpdateReply', backref='update', cascade='all, delete-orphan', order_by='TaskUpdateReply.created_at', lazy=True)
    likes = db.relationship('TaskUpdateLike', backref='update', cascade='all, delete-orphan', lazy=True)

    def to_dict(self):
        role = "superadmin" if self.sender_super_admin_id else "staff"
        email = self.sender_super_admin.email if self.sender_super_admin else (self.sender_staff.email if self.sender_staff else "")
        return {
            'id': self.id,
            'task_id': self.task_id,
            'sender_name': self.sender_name,
            'sender_role': role,
            'sender_email': email,
            'content': self.content,
            'created_at': self.created_at.isoformat() + 'Z',
            'replies': [r.to_dict() for r in self.replies],
            'likes_count': len(self.likes),
            'liked_by_ids': [l.get_user_key() for l in self.likes]
        }

class TaskUpdateReply(db.Model):
    __tablename__ = 'task_update_replies'

    id = db.Column(db.Integer, primary_key=True)
    update_id = db.Column(db.Integer, db.ForeignKey('task_updates.id', ondelete='CASCADE'), nullable=False)
    
    sender_staff_id = db.Column(db.Integer, db.ForeignKey('staff.id', ondelete='SET NULL'), nullable=True)
    sender_super_admin_id = db.Column(db.Integer, db.ForeignKey('super_admins.id', ondelete='SET NULL'), nullable=True)
    sender_name = db.Column(db.String(100), nullable=False)
    
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    sender_staff = db.relationship('Staff')
    sender_super_admin = db.relationship('SuperAdmin')

    def to_dict(self):
        role = "superadmin" if self.sender_super_admin_id else "staff"
        email = self.sender_super_admin.email if self.sender_super_admin else (self.sender_staff.email if self.sender_staff else "")
        return {
            'id': self.id,
            'update_id': self.update_id,
            'sender_name': self.sender_name,
            'sender_role': role,
            'sender_email': email,
            'content': self.content,
            'created_at': self.created_at.isoformat() + 'Z'
        }

class TaskUpdateLike(db.Model):
    __tablename__ = 'task_update_likes'

    id = db.Column(db.Integer, primary_key=True)
    update_id = db.Column(db.Integer, db.ForeignKey('task_updates.id', ondelete='CASCADE'), nullable=False)
    
    staff_id = db.Column(db.Integer, db.ForeignKey('staff.id', ondelete='CASCADE'), nullable=True)
    super_admin_id = db.Column(db.Integer, db.ForeignKey('super_admins.id', ondelete='CASCADE'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def get_user_key(self):
        if self.super_admin_id:
            return f"superadmin_{self.super_admin_id}"
        return f"staff_{self.staff_id}"
