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
            'parent_names': [f"{p.first_name} {p.last_name}" for p in self.parents],
            'parents': [p.to_dict() for p in self.parents]
        }

from werkzeug.security import generate_password_hash, check_password_hash

class Parent(db.Model):
    __tablename__ = 'parents'

    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    stripe_customer_id = db.Column(db.String(100), nullable=True)
    password_hash = db.Column(db.String(200), nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    sign_in_pin = db.Column(db.String(10), default="2963", nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    children = db.relationship('Student', secondary=parent_student_association, back_populates='parents')
    payment_methods = db.relationship('ParentPaymentMethod', backref='parent', cascade="all, delete-orphan", lazy='dynamic')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

    @property
    def name(self):
        return f"{self.first_name} {self.last_name}"

    def to_dict(self):
        return {
            'id': self.id,
            'first_name': self.first_name,
            'last_name': self.last_name,
            'email': self.email,
            'phone': self.phone,
            'stripe_customer_id': self.stripe_customer_id,
            'is_active': self.is_active,
            'sign_in_pin': self.sign_in_pin or "2963",
            'children_names': [f"{c.first_name} {c.last_name}" for c in self.children],
            'children': [{'id': c.id, 'first_name': c.first_name, 'last_name': c.last_name, 'grade_level': c.grade_level, 'status': c.status} for c in self.children]
        }

class ParentPaymentMethod(db.Model):
    __tablename__ = 'parent_payment_methods'

    id = db.Column(db.Integer, primary_key=True)
    parent_id = db.Column(db.Integer, db.ForeignKey('parents.id', ondelete='CASCADE'), nullable=False)
    method_type = db.Column(db.String(50), default='bank_account', nullable=False) # 'bank_account' or 'card'
    card_brand = db.Column(db.String(50), nullable=True) # Visa, Mastercard, Amex, Discover, etc.
    last4 = db.Column(db.String(10), nullable=False)
    exp_month = db.Column(db.Integer, nullable=True)
    exp_year = db.Column(db.Integer, nullable=True)
    bank_name = db.Column(db.String(100), nullable=True) # e.g. "Chase", "Wells Fargo", "Verified Bank"
    account_type = db.Column(db.String(50), default='checking', nullable=True) # checking, savings
    account_holder_name = db.Column(db.String(150), nullable=True)
    is_default = db.Column(db.Boolean, default=False, nullable=False)
    stripe_payment_method_id = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'parent_id': self.parent_id,
            'method_type': self.method_type,
            'card_brand': self.card_brand,
            'last4': self.last4,
            'exp_month': self.exp_month,
            'exp_year': self.exp_year,
            'bank_name': self.bank_name or "Verified Bank",
            'account_type': self.account_type,
            'account_holder_name': self.account_holder_name,
            'is_default': self.is_default,
            'created_at': self.created_at.isoformat() + 'Z'
        }