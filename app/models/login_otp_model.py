from app.models import db
from datetime import datetime
import json

class LoginOTP(db.Model):
    __tablename__ = 'login_otps'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), nullable=False)
    otp = db.Column(db.String(10), nullable=False)
    role = db.Column(db.String(50), nullable=False)
    _claims = db.Column(db.Text, nullable=True)  # JSON-serialized claims or dict data
    expiry = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def claims(self):
        if self._claims:
            try:
                return json.loads(self._claims)
            except Exception:
                return None
        return None

    @claims.setter
    def claims(self, value):
        if value is not None:
            self._claims = json.dumps(value)
        else:
            self._claims = None
