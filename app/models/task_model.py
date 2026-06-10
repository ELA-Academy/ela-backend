from app.models import db
from datetime import datetime

# Many-to-Many association tables
task_department_assignments = db.Table('task_department_assignments',
    db.Column('task_id', db.Integer, db.ForeignKey('tasks.id'), primary_key=True),
    db.Column('department_id', db.Integer, db.ForeignKey('departments.id'), primary_key=True)
)

task_staff_assignments = db.Table('task_staff_assignments',
    db.Column('task_id', db.Integer, db.ForeignKey('tasks.id'), primary_key=True),
    db.Column('staff_id', db.Integer, db.ForeignKey('staff.id'), primary_key=True)
)

class Task(db.Model):
    __tablename__ = 'tasks'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    note = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(50), default='To-Do', nullable=False)
    
    due_date = db.Column(db.DateTime, nullable=True)
    
    lead_id = db.Column(db.Integer, db.ForeignKey('leads.id'), nullable=False)
    created_by_staff_id = db.Column(db.Integer, db.ForeignKey('staff.id'), nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    created_by_staff = db.relationship('Staff')

    assigned_departments = db.relationship('Department', secondary=task_department_assignments, lazy='subquery',
        backref=db.backref('tasks', lazy=True))
        
    assigned_staff = db.relationship('Staff', secondary=task_staff_assignments, lazy='subquery',
        backref=db.backref('tasks', lazy=True))

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'note': self.note,
            'status': self.status,
            'due_date': self.due_date.isoformat() + 'Z' if self.due_date else None,
            'lead_id': self.lead_id,
            'lead_status': self.lead.status if self.lead else None,
            'assigned_department_ids': [d.id for d in self.assigned_departments],
            'assigned_department_names': [d.name for d in self.assigned_departments],
            'assigned_staff_ids': [s.id for s in self.assigned_staff],
            'assigned_staff_names': [s.name for s in self.assigned_staff],
            'created_by_staff_name': self.created_by_staff.name if self.created_by_staff else None,
            'created_at': self.created_at.isoformat() + 'Z',
            'lead_secure_token': self.lead.secure_token if self.lead else None
        }