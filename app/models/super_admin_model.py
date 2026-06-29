from app.models import db
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

class SuperAdmin(db.Model):
    __tablename__ = 'super_admins'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationship to the association object
    conversation_associations = db.relationship('ConversationParticipant', back_populates='super_admin')

    notification_preferences = db.Column(db.Text, nullable=True)

    @property
    def conversations(self):
        """A property to easily get the conversations a super admin is in."""
        return [assoc.conversation for assoc in self.conversation_associations]

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def to_dict(self):
        import json
        try:
            prefs = json.loads(self.notification_preferences) if self.notification_preferences else None
        except:
            prefs = None
            
        return {
            'id': self.id,
            'name': self.name,
            'email': self.email,
            'is_active': self.is_active,
            'notification_preferences': prefs,
            'created_at': self.created_at.isoformat() + 'Z'
        }
