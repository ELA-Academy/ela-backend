from app.models import db
from datetime import datetime
import uuid

class EnrollmentSubmission(db.Model):
    __tablename__ = 'enrollment_submissions'

    id = db.Column(db.Integer, primary_key=True)
    secure_token = db.Column(db.String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    
    form_id = db.Column(db.Integer, db.ForeignKey('enrollment_forms.id'), nullable=False)
    lead_id = db.Column(db.Integer, db.ForeignKey('leads.id'), nullable=False)
    
    status = db.Column(db.String(50), nullable=False, default='Sent') # Sent, Opened, Submitted, Completed
    payment_status = db.Column(db.String(50), nullable=False, default='Pending') # Pending, Paid

    # Stores the parent's answers to the form fields
    responses_json = db.Column(db.JSON, nullable=True)

    sent_at = db.Column(db.DateTime, default=datetime.utcnow)
    submitted_at = db.Column(db.DateTime, nullable=True)

    lead = db.relationship('Lead')

    def to_dict(self):
        lead_student_name = "N/A"
        if self.lead and self.lead.students:
            lead_student_name = f"{self.lead.students[0].first_name} {self.lead.students[0].last_name}"

        return {
            'id': self.id,
            'secure_token': self.secure_token,
            'form_id': self.form_id,
            'form_name': self.form.name,
            'lead_id': self.lead_id,
            'lead_student_name': lead_student_name,
            'status': self.status,
            'payment_status': self.payment_status,
            'responses_json': self.responses_json,
            'sent_at': self.sent_at.isoformat() + 'Z',
            'submitted_at': self.submitted_at.isoformat() + 'Z' if self.submitted_at else None,
        }