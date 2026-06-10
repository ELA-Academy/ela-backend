from app.models import db
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

staff_department_assignments = db.Table('staff_department_assignments',
    db.Column('staff_id', db.Integer, db.ForeignKey('staff.id'), primary_key=True),
    db.Column('department_id', db.Integer, db.ForeignKey('departments.id'), primary_key=True)
)

class Staff(db.Model):
    __tablename__ = 'staff'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    departments = db.relationship('Department', secondary=staff_department_assignments, lazy='subquery',
        backref=db.backref('staff_members', lazy=True))

    # Relationship to the association object
    conversation_associations = db.relationship('ConversationParticipant', back_populates='staff')
    
    @property
    def conversations(self):
        """A property to easily get the conversations a staff member is in."""
        return [assoc.conversation for assoc in self.conversation_associations]

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'email': self.email,
            'is_active': self.is_active,
            'department_ids': [d.id for d in self.departments],
            'department_names': [d.name for d in self.departments],
            'created_at': self.created_at.isoformat() + 'Z'
        }