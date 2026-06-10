from app.models import db
from datetime import datetime

class EnrollmentForm(db.Model):
    __tablename__ = 'enrollment_forms'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(50), nullable=False, default='Draft') # Draft, Active
    
    # This will store the structure of the form, including sections and fields
    # Example: {'title': '...', 'sections': [{'name': 'Student Info', 'fields': [...]}]}
    form_structure_json = db.Column(db.JSON, nullable=False)

    # Payment settings
    collect_fee = db.Column(db.Boolean, default=False)
    fee_amount = db.Column(db.Float, nullable=True)
    
    # Recipient type helps filter the student list on the frontend
    recipient_type = db.Column(db.String(50), default='New Students') # 'New Students', 'Returning Students'

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    submissions = db.relationship('EnrollmentSubmission', backref='form', lazy=True, cascade="all, delete-orphan")

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'status': self.status,
            'form_structure_json': self.form_structure_json,
            'collect_fee': self.collect_fee,
            'fee_amount': self.fee_amount,
            'recipient_type': self.recipient_type,
            'created_at': self.created_at.isoformat() + 'Z',
            'updated_at': self.updated_at.isoformat() + 'Z',
        }