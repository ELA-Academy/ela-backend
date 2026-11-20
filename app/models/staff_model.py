from app.models import db
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
import json

staff_department_assignments = db.Table('staff_department_assignments',
    db.Column('staff_id', db.Integer, db.ForeignKey('staff.id', ondelete='CASCADE'), primary_key=True),
    db.Column('department_id', db.Integer, db.ForeignKey('departments.id', ondelete='CASCADE'), primary_key=True)
)

class Staff(db.Model):
    __tablename__ = 'staff'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(50), default='staff', nullable=True) # 'staff' or 'teacher'
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # ELA TT & Payroll Extensions
    job_title = db.Column(db.String(100), default='Contractor', nullable=True)
    worker_type = db.Column(db.String(50), default='Contractor', nullable=True) # 'Contractor', 'Employee'
    hourly_rate = db.Column(db.Float, default=25.0, nullable=True)
    manager_name = db.Column(db.String(100), default='Marcus Cooper', nullable=True)
    work_email = db.Column(db.String(120), nullable=True)
    country = db.Column(db.String(100), default='United States of America', nullable=True)
    start_date = db.Column(db.String(50), default='Jan 22, 2023', nullable=True)
    
    preferred_name = db.Column(db.String(100), nullable=True)
    legal_name = db.Column(db.String(100), nullable=True)
    pronouns = db.Column(db.String(50), default='she/her/hers', nullable=True)
    ssn_last4 = db.Column(db.String(10), default='7600', nullable=True)
    birthday = db.Column(db.String(50), nullable=True)
    personal_email = db.Column(db.String(120), nullable=True)
    phone = db.Column(db.String(50), default='+1 (404) 555-0192', nullable=True)
    
    address = db.Column(db.Text, nullable=True) # JSON: street, city, state, zip, country
    emergency_contact = db.Column(db.Text, nullable=True) # JSON: name, relationship, phone, email
    compensation_history = db.Column(db.Text, nullable=True) # JSON array of historical adjustments
    
    payment_method = db.Column(db.String(50), default='Direct deposit', nullable=True)
    bank_name = db.Column(db.String(100), default='Chase Bank', nullable=True)
    bank_account_last4 = db.Column(db.String(10), default='7600', nullable=True)
    bank_routing = db.Column(db.String(50), default='021000021', nullable=True)
    bank_account_type = db.Column(db.String(50), default='Checking', nullable=True)
    worker_status = db.Column(db.String(50), default='Active', nullable=True) # 'Active', 'Onboarding', 'Offboarding', 'Dismissed'
    receives_tips = db.Column(db.Boolean, default=False, nullable=True)
    supervisor_name = db.Column(db.String(100), nullable=True)
    dismissed_effective_date = db.Column(db.String(50), nullable=True)
    dismissal_reason = db.Column(db.Text, nullable=True)
    checkin_schedule = db.Column(db.String(50), nullable=True)  # e.g. '30,60,90'

    # Payment Enumeration Fields (Spreadsheet columns)
    expected_hours = db.Column(db.String(50), nullable=True)
    substituting_pay = db.Column(db.Float, nullable=True)
    tutoring_pay = db.Column(db.Float, nullable=True)
    training_pay = db.Column(db.Float, nullable=True)
    us_holiday_bonuses = db.Column(db.String(100), nullable=True)
    winter_break_bonuses = db.Column(db.String(100), nullable=True)
    spring_break_bonuses = db.Column(db.String(100), nullable=True)

    departments = db.relationship('Department', secondary=staff_department_assignments, lazy='subquery',
        backref=db.backref('staff_members', lazy=True))

    # Relationship to the association object
    conversation_associations = db.relationship('ConversationParticipant', back_populates='staff')
    
    notification_preferences = db.Column(db.Text, nullable=True)

    @property
    def conversations(self):
        """A property to easily get the conversations a staff member is in."""
        return [assoc.conversation for assoc in self.conversation_associations]

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def to_dict(self):
        try:
            prefs = json.loads(self.notification_preferences) if self.notification_preferences else None
        except Exception:
            prefs = None
            
        try:
            addr = json.loads(self.address) if self.address else {
                "street": "1420 Peachtree St NE, Apt 4B",
                "city": "Atlanta",
                "state": "GA",
                "zip": "30309",
                "country": "United States of America"
            }
        except Exception:
            addr = {"street": "1420 Peachtree St NE, Apt 4B", "city": "Atlanta", "state": "GA", "zip": "30309", "country": "United States"}
            
        try:
            emg = json.loads(self.emergency_contact) if self.emergency_contact else {
                "name": "David Rigsby",
                "relationship": "Spouse",
                "phone": "+1 (404) 555-0188",
                "email": "david.rigsby@example.com"
            }
        except Exception:
            emg = {"name": "David Rigsby", "relationship": "Spouse", "phone": "+1 (404) 555-0188", "email": "david.rigsby@example.com"}

        try:
            comp_hist = json.loads(self.compensation_history) if self.compensation_history else [
                {"rate": self.hourly_rate or 30.0, "effective_date": "10/6/2026", "reason": "Annual rate review"}
            ]
        except Exception:
            comp_hist = []

        primary_dept = self.departments[0].name if self.departments else "Teacher Department"

        return {
            'id': self.id,
            'name': self.name,
            'preferred_name': self.preferred_name or self.name,
            'legal_name': self.legal_name or self.name,
            'email': self.email,
            'work_email': self.work_email if self.work_email is not None else (self.email or ''),
            'role': getattr(self, 'role', 'staff') or 'staff',
            'is_active': self.is_active,
            'department_ids': [d.id for d in self.departments],
            'department_names': [d.name for d in self.departments],
            'department': primary_dept,
            'job_title': self.job_title or (f"{primary_dept} Specialist" if self.role != 'teacher' else "Teacher"),
            'worker_type': self.worker_type or 'Contractor',
            'worker_status': self.worker_status or 'Active',
            'hourly_rate': self.hourly_rate if self.hourly_rate is not None else 30.0,
            'rate_formatted': f"${(self.hourly_rate if self.hourly_rate is not None else 30.0):.2f} per hour",
            'manager_name': self.manager_name or 'Marcus Cooper',
            'country': self.country or 'United States of America',
            'start_date': self.start_date or 'Jan 22, 2023',
            'pronouns': self.pronouns or 'she/her/hers',
            'ssn_last4': self.ssn_last4 or '7600',
            'birthday': self.birthday or 'October 14, 1992',
            'personal_email': self.personal_email or '',
            'phone': self.phone or '+1 (404) 555-0192',
            'address': addr,
            'emergency_contact': emg,
            'compensation_history': comp_hist,
            'payment_method': self.payment_method or 'Direct deposit',
            'bank_name': self.bank_name or 'Chase Bank',
            'bank_account_last4': self.bank_account_last4 or '7600',
            'bank_routing': self.bank_routing or '021000021',
            'bank_account_type': self.bank_account_type or 'Checking',
            'receives_tips': bool(self.receives_tips),
            'supervisor_name': self.supervisor_name or self.manager_name or 'Marcus Cooper',
            'dismissed_effective_date': self.dismissed_effective_date,
            'dismissal_reason': self.dismissal_reason,
            'checkin_schedule': self.checkin_schedule,
            'expected_hours': self.expected_hours or '',
            'substituting_pay': self.substituting_pay,
            'tutoring_pay': self.tutoring_pay,
            'training_pay': self.training_pay,
            'us_holiday_bonuses': self.us_holiday_bonuses or '',
            'winter_break_bonuses': self.winter_break_bonuses or '',
            'spring_break_bonuses': self.spring_break_bonuses or '',
            'notification_preferences': prefs,
            'created_at': self.created_at.isoformat() + 'Z'
        }
