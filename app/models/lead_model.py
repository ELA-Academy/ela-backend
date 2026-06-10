from app.models import db
from datetime import datetime
import uuid

class Lead(db.Model):
    __tablename__ = 'leads'

    id = db.Column(db.Integer, primary_key=True)
    secure_token = db.Column(db.String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    
    status = db.Column(db.String(50), default='Waitlisted', nullable=False)
    payment_status = db.Column(db.String(50), default='Unpaid', nullable=True)
    expected_start_date = db.Column(db.Date, nullable=True)
    amount = db.Column(db.Float, nullable=True)
    policy_agreed = db.Column(db.Boolean, default=False, nullable=False)
    internal_notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships to temporary lead-specific data
    students = db.relationship('LeadStudent', backref='lead', lazy=True, cascade="all, delete-orphan")
    parents = db.relationship('LeadParent', backref='lead', lazy=True, cascade="all, delete-orphan")
    tasks = db.relationship('Task', backref='lead', lazy=True, cascade="all, delete-orphan")

    def to_dict(self):
        return {
            'id': self.id,
            'secure_token': self.secure_token,
            'status': self.status,
            'created_at': self.created_at.isoformat() + 'Z',
            'students': [student.to_dict() for student in self.students],
            'parents': [parent.to_dict() for parent in self.parents],
        }

class LeadStudent(db.Model):
    __tablename__ = 'lead_students'
    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    date_of_birth = db.Column(db.Date, nullable=False)
    city_state = db.Column(db.String(150), nullable=True)
    grade_level = db.Column(db.String(50), nullable=False)
    lead_id = db.Column(db.Integer, db.ForeignKey('leads.id'), nullable=False)

    def to_dict(self):
        return {'id': self.id, 'first_name': self.first_name, 'last_name': self.last_name, 'date_of_birth': self.date_of_birth.isoformat(), 'city_state': self.city_state, 'grade_level': self.grade_level}

class LeadParent(db.Model):
    __tablename__ = 'lead_parents'
    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    lead_id = db.Column(db.Integer, db.ForeignKey('leads.id'), nullable=False)

    def to_dict(self):
        return {'id': self.id, 'first_name': self.first_name, 'last_name': self.last_name, 'email': self.email, 'phone': self.phone}