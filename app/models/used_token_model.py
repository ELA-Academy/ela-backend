from app.models import db
from datetime import datetime

class UsedToken(db.Model):
    __tablename__ = 'used_tokens'

    id = db.Column(db.Integer, primary_key=True)
    token_jti = db.Column(db.String(255), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
