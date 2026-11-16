from app.models import db
from datetime import datetime, date
import json


class TimeTrackingSettings(db.Model):
    __tablename__ = 'time_tracking_settings'

    id = db.Column(db.Integer, primary_key=True)
    workweek_start = db.Column(db.String(20), default='Sunday', nullable=False)
    allowed_days = db.Column(db.Text, default='["Mon","Tue","Wed","Thu","Fri"]', nullable=False)
    require_whole_hours = db.Column(db.Boolean, default=False, nullable=False)
    overtime_weekly_threshold = db.Column(db.Float, default=40.0, nullable=False)
    overtime_daily_double = db.Column(db.Float, nullable=True)
    geolocation_enabled = db.Column(db.Boolean, default=False, nullable=False)
    kiosk_enabled = db.Column(db.Boolean, default=False, nullable=False)
    non_us_tracking_enabled = db.Column(db.Boolean, default=True, nullable=False)
    last_synced_at = db.Column(db.DateTime, nullable=True)
    next_invoice_number = db.Column(db.Integer, default=72, nullable=False)
    bank_account_label = db.Column(db.String(150), default='Cooper Galvis Checking •••• 4821', nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        try:
            days = json.loads(self.allowed_days) if self.allowed_days else ["Mon", "Tue", "Wed", "Thu", "Fri"]
        except Exception:
            days = ["Mon", "Tue", "Wed", "Thu", "Fri"]
        return {
            'id': self.id,
            'workweek_start': self.workweek_start,
            'allowed_days': days,
            'require_whole_hours': self.require_whole_hours,
            'overtime_weekly_threshold': self.overtime_weekly_threshold,
            'overtime_daily_double': self.overtime_daily_double,
            'geolocation_enabled': self.geolocation_enabled,
            'kiosk_enabled': self.kiosk_enabled,
            'non_us_tracking_enabled': self.non_us_tracking_enabled,
            'last_synced_at': self.last_synced_at.isoformat() if self.last_synced_at else None,
            'next_invoice_number': self.next_invoice_number,
            'bank_account_label': self.bank_account_label,
        }


class StaffTimeTrackingAssignment(db.Model):
    __tablename__ = 'staff_time_tracking_assignments'

    id = db.Column(db.Integer, primary_key=True)
    staff_id = db.Column(db.Integer, db.ForeignKey('staff.id', ondelete='CASCADE'), unique=True, nullable=False)
    is_assigned = db.Column(db.Boolean, default=True, nullable=False)
    overtime_policy = db.Column(db.String(100), default='Default', nullable=False)
    break_policy = db.Column(db.String(100), nullable=True)
    timezone = db.Column(db.String(120), default='Eastern Time (US & Canada) (EDT, GMT-4)', nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    staff = db.relationship('Staff', backref=db.backref('time_tracking_assignment', uselist=False, cascade='all, delete-orphan'))

    def to_dict(self):
        return {
            'id': self.id,
            'staff_id': self.staff_id,
            'is_assigned': self.is_assigned,
            'overtime_policy': self.overtime_policy,
            'break_policy': self.break_policy,
            'timezone': self.timezone,
        }


class TimeEntry(db.Model):
    __tablename__ = 'time_entries'

    id = db.Column(db.Integer, primary_key=True)
    staff_id = db.Column(db.Integer, db.ForeignKey('staff.id', ondelete='CASCADE'), index=True, nullable=False)
    work_date = db.Column(db.Date, index=True, nullable=False)
    start_time = db.Column(db.String(10), nullable=False)  # "09:00"
    end_time = db.Column(db.String(10), nullable=False)    # "10:00"
    duration_minutes = db.Column(db.Integer, nullable=False, default=60)
    note = db.Column(db.Text, nullable=True)
    timezone = db.Column(db.String(120), nullable=True)
    status = db.Column(db.String(20), default='draft', nullable=False)  # 'draft', 'synced', 'paid'
    pay_period_start = db.Column(db.Date, nullable=True)
    pay_period_end = db.Column(db.Date, nullable=True)
    payroll_run_id = db.Column(db.Integer, nullable=True)
    admin_override_note = db.Column(db.Text, nullable=True)
    updated_by_admin = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    staff = db.relationship('Staff', backref=db.backref('time_entries', lazy=True, cascade='all, delete-orphan'))

    def to_dict(self):
        return {
            'id': self.id,
            'staff_id': self.staff_id,
            'work_date': self.work_date.isoformat() if self.work_date else None,
            'start_time': self.start_time,
            'end_time': self.end_time,
            'duration_minutes': self.duration_minutes,
            'hours': round(self.duration_minutes / 60.0, 2),
            'note': self.note or '',
            'timezone': self.timezone,
            'status': self.status,
            'pay_period_start': self.pay_period_start.isoformat() if self.pay_period_start else None,
            'pay_period_end': self.pay_period_end.isoformat() if self.pay_period_end else None,
            'payroll_run_id': self.payroll_run_id,
            'admin_override_note': self.admin_override_note,
            'updated_by_admin': self.updated_by_admin,
        }


class PayrollRun(db.Model):
    __tablename__ = 'payroll_runs'

    id = db.Column(db.Integer, primary_key=True)
    run_type = db.Column(db.String(40), default='us_contractor', nullable=False)  # 'us_contractor', 'non_us_contractor', 'employee', 'off_cycle'
    pay_period_start = db.Column(db.Date, nullable=True)
    pay_period_end = db.Column(db.Date, nullable=True)
    pay_date = db.Column(db.Date, nullable=False, default=date.today)
    status = db.Column(db.String(20), default='paid', nullable=False)
    total_amount = db.Column(db.Float, default=0.0, nullable=False)
    paid_from = db.Column(db.String(150), default='Cooper Galvis Checking •••• 4821', nullable=False)
    created_by = db.Column(db.String(120), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    items = db.relationship('PayrollPaymentItem', backref='payroll_run', lazy=True, cascade='all, delete-orphan')

    def to_dict(self, include_items=False):
        data = {
            'id': self.id,
            'run_type': self.run_type,
            'pay_period_start': self.pay_period_start.isoformat() if self.pay_period_start else None,
            'pay_period_end': self.pay_period_end.isoformat() if self.pay_period_end else None,
            'pay_date': self.pay_date.isoformat() if self.pay_date else None,
            'status': self.status,
            'total_amount': round(self.total_amount, 2),
            'paid_from': self.paid_from,
            'created_by': self.created_by,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'item_count': len(self.items) if self.items else 0,
        }
        if include_items:
            data['items'] = [item.to_dict() for item in self.items]
        return data


class PayrollPaymentItem(db.Model):
    __tablename__ = 'payroll_payment_items'

    id = db.Column(db.Integer, primary_key=True)
    payroll_run_id = db.Column(db.Integer, db.ForeignKey('payroll_runs.id', ondelete='CASCADE'), nullable=False)
    staff_id = db.Column(db.Integer, db.ForeignKey('staff.id', ondelete='CASCADE'), nullable=False)
    staff_name = db.Column(db.String(120), nullable=False)
    invoice_number = db.Column(db.String(40), nullable=True)
    payment_method = db.Column(db.String(50), default='Direct deposit', nullable=False)
    worker_type = db.Column(db.String(50), default='Contractor', nullable=False)
    country = db.Column(db.String(100), default='United States of America', nullable=False)
    hourly_rate = db.Column(db.Float, default=0.0, nullable=False)
    hours_worked = db.Column(db.Float, default=0.0, nullable=False)
    regular_pay = db.Column(db.Float, default=0.0, nullable=False)
    bonus = db.Column(db.Float, default=0.0, nullable=False)
    reimbursement = db.Column(db.Float, default=0.0, nullable=False)
    memo = db.Column(db.Text, nullable=True)
    net_pay = db.Column(db.Float, default=0.0, nullable=False)
    status = db.Column(db.String(20), default='paid', nullable=False)
    paid_at = db.Column(db.DateTime, default=datetime.utcnow)

    staff = db.relationship('Staff', backref=db.backref('payroll_payments', lazy=True))

    def to_dict(self):
        return {
            'id': self.id,
            'payroll_run_id': self.payroll_run_id,
            'staff_id': self.staff_id,
            'staff_name': self.staff_name,
            'invoice_number': self.invoice_number,
            'payment_method': self.payment_method,
            'worker_type': self.worker_type,
            'country': self.country,
            'hourly_rate': round(self.hourly_rate, 2),
            'hours_worked': round(self.hours_worked, 2),
            'regular_pay': round(self.regular_pay, 2),
            'bonus': round(self.bonus, 2),
            'reimbursement': round(self.reimbursement, 2),
            'memo': self.memo or '',
            'net_pay': round(self.net_pay, 2),
            'status': self.status,
            'paid_at': self.paid_at.isoformat() if self.paid_at else None,
            'pay_date': self.payroll_run.pay_date.isoformat() if self.payroll_run and self.payroll_run.pay_date else None,
            'pay_period_start': self.payroll_run.pay_period_start.isoformat() if self.payroll_run and self.payroll_run.pay_period_start else None,
            'pay_period_end': self.payroll_run.pay_period_end.isoformat() if self.payroll_run and self.payroll_run.pay_period_end else None,
            'paid_from': self.payroll_run.paid_from if self.payroll_run else None,
        }


class HRDocument(db.Model):
    __tablename__ = 'hr_documents'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(80), default='Company Policies', nullable=False)
    description = db.Column(db.Text, nullable=True)
    doc_type = db.Column(db.String(30), default='shared', nullable=False)  # 'shared' | 'template'
    status = db.Column(db.String(30), default='active', nullable=False)  # 'active' | 'draft' | 'archived'
    file_name = db.Column(db.String(255), nullable=True)
    content_body = db.Column(db.Text, nullable=True)
    requires_signature = db.Column(db.Boolean, default=True, nullable=False)
    is_fillable = db.Column(db.Boolean, default=False, nullable=False)
    fillable_fields = db.Column(db.Text, nullable=True)  # JSON array of field specs
    future_hires_rule = db.Column(db.String(30), default='none', nullable=False)  # 'none' | 'all' | 'us' | 'non_us'
    target_department_ids = db.Column(db.Text, nullable=True)  # JSON array of dept IDs
    uploaded_by = db.Column(db.String(150), default='Cooper Galvis Enterprises LLC', nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    assignments = db.relationship('StaffDocumentAssignment', backref='document', lazy=True, cascade='all, delete-orphan')

    def to_dict(self):
        try:
            fields = json.loads(self.fillable_fields) if self.fillable_fields else []
        except Exception:
            fields = []
        try:
            dept_ids = json.loads(self.target_department_ids) if self.target_department_ids else []
        except Exception:
            dept_ids = []
        return {
            'id': self.id,
            'title': self.title,
            'category': self.category,
            'description': self.description or '',
            'doc_type': self.doc_type,
            'status': self.status,
            'file_name': self.file_name,
            'content_body': self.content_body or '',
            'requires_signature': self.requires_signature,
            'is_fillable': self.is_fillable,
            'fillable_fields': fields,
            'future_hires_rule': self.future_hires_rule,
            'target_department_ids': dept_ids,
            'uploaded_by': self.uploaded_by,
            'recipient_count': len(self.assignments) if self.assignments else 0,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class StaffDocumentAssignment(db.Model):
    __tablename__ = 'staff_document_assignments'

    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.Integer, db.ForeignKey('hr_documents.id', ondelete='CASCADE'), nullable=False)
    staff_id = db.Column(db.Integer, db.ForeignKey('staff.id', ondelete='CASCADE'), nullable=False)
    status = db.Column(db.String(40), default='Needs acknowledgement', nullable=False)
    assigned_at = db.Column(db.DateTime, default=datetime.utcnow)
    viewed_at = db.Column(db.DateTime, nullable=True)
    signed_at = db.Column(db.DateTime, nullable=True)
    signature_name = db.Column(db.String(150), nullable=True)
    filled_data = db.Column(db.Text, nullable=True)
    last_reminded_at = db.Column(db.DateTime, nullable=True)

    staff = db.relationship('Staff', backref=db.backref('document_assignments', lazy=True, cascade='all, delete-orphan'))

    def to_dict(self):
        try:
            filled = json.loads(self.filled_data) if self.filled_data else {}
        except Exception:
            filled = {}
        return {
            'id': self.id,
            'document_id': self.document_id,
            'document_title': self.document.title if self.document else '',
            'document_category': self.document.category if self.document else '',
            'document_description': self.document.description if self.document else '',
            'content_body': self.document.content_body if self.document else '',
            'requires_signature': self.document.requires_signature if self.document else True,
            'is_fillable': self.document.is_fillable if self.document else False,
            'fillable_fields': json.loads(self.document.fillable_fields) if (self.document and self.document.fillable_fields) else [],
            'staff_id': self.staff_id,
            'staff_name': self.staff.name if self.staff else '',
            'staff_email': self.staff.email if self.staff else '',
            'status': self.status,
            'assigned_at': self.assigned_at.isoformat() if self.assigned_at else None,
            'viewed_at': self.viewed_at.isoformat() if self.viewed_at else None,
            'signed_at': self.signed_at.isoformat() if self.signed_at else None,
            'signature_name': self.signature_name,
            'filled_data': filled,
            'last_reminded_at': self.last_reminded_at.isoformat() if self.last_reminded_at else None,
        }


class PerformanceGoal(db.Model):
    __tablename__ = 'performance_goals'

    id = db.Column(db.Integer, primary_key=True)
    staff_id = db.Column(db.Integer, db.ForeignKey('staff.id', ondelete='CASCADE'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    due_date = db.Column(db.String(40), nullable=True)
    progress = db.Column(db.Integer, default=0, nullable=False)
    status = db.Column(db.String(30), default='In Progress', nullable=False)
    created_by = db.Column(db.String(120), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    staff = db.relationship('Staff', backref=db.backref('performance_goals', lazy=True, cascade='all, delete-orphan'))

    def to_dict(self):
        return {
            'id': self.id,
            'staff_id': self.staff_id,
            'staff_name': self.staff.name if self.staff else '',
            'title': self.title,
            'description': self.description or '',
            'due_date': self.due_date,
            'progress': self.progress,
            'status': self.status,
            'created_by': self.created_by,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class PerformanceReview(db.Model):
    __tablename__ = 'performance_reviews'

    id = db.Column(db.Integer, primary_key=True)
    staff_id = db.Column(db.Integer, db.ForeignKey('staff.id', ondelete='CASCADE'), nullable=False)
    review_type = db.Column(db.String(30), default='self', nullable=False)  # 'self' | 'supervisor'
    title = db.Column(db.String(200), nullable=False)
    period = db.Column(db.String(80), nullable=True)
    questions_json = db.Column(db.Text, nullable=True)
    responses_json = db.Column(db.Text, nullable=True)
    rating = db.Column(db.Integer, nullable=True)
    status = db.Column(db.String(30), default='Pending', nullable=False)  # 'Pending' | 'Completed'
    reviewer_name = db.Column(db.String(120), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    submitted_at = db.Column(db.DateTime, nullable=True)

    staff = db.relationship('Staff', backref=db.backref('performance_reviews', lazy=True, cascade='all, delete-orphan'))

    def to_dict(self):
        try:
            questions = json.loads(self.questions_json) if self.questions_json else []
        except Exception:
            questions = []
        try:
            responses = json.loads(self.responses_json) if self.responses_json else {}
        except Exception:
            responses = {}
        return {
            'id': self.id,
            'staff_id': self.staff_id,
            'staff_name': self.staff.name if self.staff else '',
            'review_type': self.review_type,
            'title': self.title,
            'period': self.period,
            'questions': questions,
            'responses': responses,
            'rating': self.rating,
            'status': self.status,
            'reviewer_name': self.reviewer_name,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'submitted_at': self.submitted_at.isoformat() if self.submitted_at else None,
        }
