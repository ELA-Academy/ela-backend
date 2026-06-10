from app.models import db
from datetime import datetime

class MessageLog(db.Model):
    __tablename__ = 'message_logs'

    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey('conversations.id'), nullable=False)
    
    sender_id = db.Column(db.Integer)
    sender_type = db.Column(db.String(50))
    sender_name = db.Column(db.String(100))
    
    recipient_names = db.Column(db.Text)
    
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    conversation = db.relationship('Conversation')