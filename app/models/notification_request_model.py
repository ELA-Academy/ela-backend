from app.models import db
from datetime import datetime

class NotificationRequest(db.Model):
    __tablename__ = 'notification_requests'

    id = db.Column(db.Integer, primary_key=True)
    idempotency_key = db.Column(db.String(255), unique=True, nullable=True)
    recipient_id = db.Column(db.Integer, nullable=False)
    recipient_role = db.Column(db.String(50), nullable=False)  # 'staff' or 'superadmin'
    message = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(50), default='general', nullable=False)
    target_type = db.Column(db.String(50), nullable=True)
    target_id = db.Column(db.Integer, nullable=True)
    target_link = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(50), default='PENDING', nullable=False)  # PENDING, PROCESSING, COMPLETED, FAILED
    channels = db.Column(db.String(255), default='in_app,email,push', nullable=False) # comma-separated list of allowed channels
    retry_count = db.Column(db.Integer, default=0, nullable=False)
    error_message = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'idempotency_key': self.idempotency_key,
            'recipient_id': self.recipient_id,
            'recipient_role': self.recipient_role,
            'message': self.message,
            'category': self.category,
            'target_type': self.target_type,
            'target_id': self.target_id,
            'target_link': self.target_link,
            'status': self.status,
            'channels': self.channels,
            'retry_count': self.retry_count,
            'error_message': self.error_message,
            'created_at': self.created_at.isoformat() + 'Z'
        }

class UserNotificationPreference(db.Model):
    __tablename__ = 'user_notification_preferences'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False)
    user_role = db.Column(db.String(50), nullable=False)  # 'staff' or 'superadmin'
    channel = db.Column(db.String(50), nullable=False)  # 'in_app', 'email', 'push'
    category = db.Column(db.String(50), default='all', nullable=False)  # 'general', 'mention', 'assignment', 'all'
    enabled = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'user_role', 'channel', 'category', name='_user_preference_uc'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'user_role': self.user_role,
            'channel': self.channel,
            'category': self.category,
            'enabled': self.enabled
        }
