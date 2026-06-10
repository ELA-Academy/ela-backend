from app.models import db
from datetime import datetime

# Association table for the many-to-many relationship between Parents and Students
parent_student_association = db.Table('parent_student_association',
    db.Column('parent_id', db.Integer, db.ForeignKey('parents.id'), primary_key=True),
    db.Column('student_id', db.Integer, db.ForeignKey('students.id'), primary_key=True)
)

class Student(db.Model):
    __tablename__ = 'students'

    id = db.Column(db.Integer, primary_key=True)
    student_id_number = db.Column(db.String(50), unique=True, nullable=True) # Official school ID
    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    date_of_birth = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(50), nullable=False, default='Active') # Active, Inactive, Graduated
    enrollment_date = db.Column(db.Date, nullable=True)
    grade_level = db.Column(db.String(50), nullable=False)
    
    # Foreign key for the temporary lead this student came from
    lead_id = db.Column(db.Integer, db.ForeignKey('leads.id'), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    parents = db.relationship('Parent', secondary=parent_student_association, back_populates='children')
    financial_account = db.relationship('StudentFinancialAccount', backref='student', uselist=False, cascade="all, delete-orphan")

    def to_dict(self):
        return {
            'id': self.id,
            'student_id_number': self.student_id_number,
            'first_name': self.first_name,
            'last_name': self.last_name,
            'date_of_birth': self.date_of_birth.isoformat(),
            'status': self.status,
            'enrollment_date': self.enrollment_date.isoformat() if self.enrollment_date else None,
            'grade_level': self.grade_level,
            'parent_names': [f"{p.first_name} {p.last_name}" for p in self.parents]
        }

class Parent(db.Model):
    __tablename__ = 'parents'

    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    stripe_customer_id = db.Column(db.String(100), nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    children = db.relationship('Student', secondary=parent_student_association, back_populates='parents')

    def to_dict(self):
        return {
            'id': self.id,
            'first_name': self.first_name,
            'last_name': self.last_name,
            'email': self.email,
            'phone': self.phone,
            'stripe_customer_id': self.stripe_customer_id,
            'children_names': [f"{c.first_name} {c.last_name}" for c in self.children]
        }