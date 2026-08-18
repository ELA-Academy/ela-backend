from app.models import db
from datetime import datetime, timezone

class ConversationParticipant(db.Model):
    __tablename__ = 'conversation_participants'
    
    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey('conversations.id'), nullable=False)
    staff_id = db.Column(db.Integer, db.ForeignKey('staff.id', ondelete='CASCADE'), nullable=True)
    super_admin_id = db.Column(db.Integer, db.ForeignKey('super_admins.id', ondelete='CASCADE'), nullable=True)
    last_read_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    
    # New field to track the last time an email/push notification was sent
    last_notified_at = db.Column(db.DateTime, nullable=True)
    is_following = db.Column(db.Boolean, nullable=False, default=True)

    staff = db.relationship('Staff', back_populates='conversation_associations')
    super_admin = db.relationship('SuperAdmin', back_populates='conversation_associations')
    conversation = db.relationship('Conversation', back_populates='participants')


class Conversation(db.Model):
    __tablename__ = 'conversations'

    id = db.Column(db.Integer, primary_key=True)
    conversation_type = db.Column(db.String(50), default='direct', nullable=False)
    name = db.Column(db.String(150), nullable=True)
    department_id = db.Column(db.Integer, db.ForeignKey('departments.id', ondelete='SET NULL'), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    messages = db.relationship('Message', backref='conversation', lazy='dynamic', cascade="all, delete-orphan", order_by='Message.created_at')
    participants = db.relationship('ConversationParticipant', back_populates='conversation', cascade="all, delete-orphan")
    department = db.relationship('Department')

    def get_participants(self):
        all_participants = []
        for p in self.participants:
            if p.staff:
                all_participants.append(p.staff)
            if p.super_admin:
                all_participants.append(p.super_admin)
        return all_participants

    def display_name(self):
        if self.name:
            return self.name
        participants = self.get_participants()
        return ", ".join([participant.name for participant in participants]) or "Untitled conversation"


class Message(db.Model):
    __tablename__ = 'messages'

    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    conversation_id = db.Column(db.Integer, db.ForeignKey('conversations.id'), nullable=False)
    sender_type = db.Column(db.String(50))
    sender_id = db.Column(db.Integer)
    file_path = db.Column(db.String(500), nullable=True)
    filename = db.Column(db.String(255), nullable=True)
    reply_to_message_id = db.Column(db.Integer, db.ForeignKey('messages.id', ondelete='SET NULL'), nullable=True)
    is_edited = db.Column(db.Boolean, default=False, nullable=True)

    reply_to_message = db.relationship('Message', remote_side=[id], lazy='joined')

    __mapper_args__ = {'polymorphic_on': sender_type}
    
    reactions = db.relationship('MessageReaction', backref='message', cascade='all, delete-orphan', lazy=True)

    def to_dict(self):
        from app.models.staff_model import Staff
        from app.models.super_admin_model import SuperAdmin

        sender_name = "Unknown"
        sender_model = None
        if self.sender_type == 'staff':
            sender_model = Staff.query.get(self.sender_id)
        elif self.sender_type == 'superadmin':
            sender_model = SuperAdmin.query.get(self.sender_id)
        
        if sender_model:
            sender_name = sender_model.name

        reply_to_details = None
        if self.reply_to_message:
            parent = self.reply_to_message
            parent_sender_name = "Unknown"
            if parent.sender_type == 'staff':
                sm = Staff.query.get(parent.sender_id)
                if sm:
                    parent_sender_name = sm.name
            elif parent.sender_type == 'superadmin':
                sa = SuperAdmin.query.get(parent.sender_id)
                if sa:
                    parent_sender_name = sa.name
            
            reply_to_details = {
                'id': parent.id,
                'content': parent.content,
                'sender_name': parent_sender_name
            }

        return {
            'id': self.id,
            'content': self.content,
            'created_at': self.created_at.isoformat() + 'Z',
            'sender_id': self.sender_id,
            'sender_type': self.sender_type,
            'sender_name': sender_name,
            'file_path': self.file_path,
            'filename': self.filename,
            'reply_to_message_id': self.reply_to_message_id,
            'reply_to_details': reply_to_details,
            'reactions': [r.to_dict() for r in self.reactions],
            'is_edited': getattr(self, 'is_edited', False) or False
        }

class MessageReaction(db.Model):
    __tablename__ = 'message_reactions'
    
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey('messages.id', ondelete='CASCADE'), nullable=False)
    emoji = db.Column(db.String(50), nullable=False)
    staff_id = db.Column(db.Integer, db.ForeignKey('staff.id', ondelete='CASCADE'), nullable=True)
    super_admin_id = db.Column(db.Integer, db.ForeignKey('super_admins.id', ondelete='CASCADE'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    staff = db.relationship('Staff')
    super_admin = db.relationship('SuperAdmin')

    def to_dict(self):
        user_name = "Unknown"
        if self.super_admin:
            user_name = self.super_admin.name
        elif self.staff:
            user_name = self.staff.name
        return {
            'id': self.id,
            'emoji': self.emoji,
            'user_id': self.super_admin_id if self.super_admin_id else self.staff_id,
            'user_role': 'superadmin' if self.super_admin_id else 'staff',
            'user_name': user_name
        }

class StaffMessage(Message):
    __mapper_args__ = {'polymorphic_identity': 'staff'}

class SuperAdminMessage(Message):
    __mapper_args__ = {'polymorphic_identity': 'superadmin'}
