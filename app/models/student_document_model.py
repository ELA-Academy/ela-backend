from app.models import db
from datetime import datetime

class StudentDocument(db.Model):
    __tablename__ = 'student_documents'

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('students.id', ondelete='CASCADE'), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    file_path = db.Column(db.String(500), nullable=False)
    expiry_date = db.Column(db.Date, nullable=True)
    document_type = db.Column(db.String(100), default="Document")  # 'Document' or 'Immunization'
    status = db.Column(db.String(50), default="UPLOADED")  # 'UPLOADED', 'EXPIRED'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    student = db.relationship('Student', backref=db.backref('documents', cascade="all, delete-orphan"))

    def to_dict(self):
        return {
            'id': self.id,
            'student_id': self.student_id,
            'name': self.name,
            'file_path': self.file_path,
            'expiry_date': self.expiry_date.isoformat() if self.expiry_date else None,
            'document_type': self.document_type,
            'status': self.status,
            'created_at': self.created_at.isoformat() + 'Z'
        }
