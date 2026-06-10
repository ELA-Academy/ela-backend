from app.models import db
from datetime import datetime

class Notification(db.Model):
    __tablename__ = 'notifications'

    id = db.Column(db.Integer, primary_key=True)
    message = db.Column(db.String(500), nullable=False)
    is_read = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Who is the notification for?
    staff_id = db.Column(db.Integer, db.ForeignKey('staff.id', ondelete='CASCADE'), nullable=True)
    super_admin_id = db.Column(db.Integer, db.ForeignKey('super_admins.id', ondelete='CASCADE'), nullable=True)
    
    # Category for 3 tabs filtering: 'general', 'mention', 'assignment'
    category = db.Column(db.String(50), default='general', nullable=False)

    # What is the notification about? (Makes it clickable)
    target_type = db.Column(db.String(50), nullable=True) # e.g., "Lead", "Board"
    target_id = db.Column(db.Integer, nullable=True)
    target_link = db.Column(db.String(255), nullable=True) # e.g., "/admin/admissions/leads/<token>" or "/admin/boards/1"

    staff = db.relationship('Staff')
    super_admin = db.relationship('SuperAdmin')

    def to_dict(self):
        return {
            'id': self.id,
            'message': self.message,
            'is_read': self.is_read,
            'category': self.category,
            'created_at': self.created_at.isoformat() + 'Z',
            'target_link': self.target_link,
            'target_type': self.target_type,
            'target_id': self.target_id
        }