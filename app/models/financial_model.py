from app.models import db
from datetime import datetime

class StudentFinancialAccount(db.Model):
    __tablename__ = 'student_financial_accounts'
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('students.id'), nullable=False, unique=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    invoices = db.relationship('Invoice', backref='account', lazy='dynamic', cascade="all, delete-orphan")
    payments = db.relationship('Payment', backref='account', lazy='dynamic', cascade="all, delete-orphan")
    credits = db.relationship('Credit', backref='account', lazy='dynamic', cascade="all, delete-orphan")
    subscriptions = db.relationship('Subscription', backref='account', lazy='dynamic', cascade="all, delete-orphan")

    def to_dict(self):
        return { 'id': self.id, 'student_id': self.student_id, 'student_name': f"{self.student.first_name} {self.student.last_name}" }

class PresetChargeItem(db.Model):
    __tablename__ = 'preset_charge_items'
    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String(255), unique=True, nullable=False)
    amount = db.Column(db.Float, nullable=False)
    is_active = db.Column(db.Boolean, default=True)

    def to_dict(self):
        return { 'id': self.id, 'description': self.description, 'amount': self.amount, 'is_active': self.is_active }

class PresetDiscount(db.Model):
    __tablename__ = 'preset_discounts'
    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String(255), unique=True, nullable=False)
    is_active = db.Column(db.Boolean, default=True)

    def to_dict(self):
        return { 'id': self.id, 'description': self.description, 'is_active': self.is_active }

class Invoice(db.Model):
    __tablename__ = 'invoices'
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey('student_financial_accounts.id'), nullable=False)
    status = db.Column(db.String(50), default='Draft', nullable=False) # Draft, Sent, Paid, Overdue, Void
    due_date = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    items = db.relationship('InvoiceItem', backref='invoice', cascade="all, delete-orphan")
    payments = db.relationship('Payment', backref='invoice', lazy='dynamic')
    
    @property
    def total_amount(self):
        return sum(float(item.amount or 0) for item in self.items)

    def to_dict(self):
        return {
            'id': self.id, 'account_id': self.account_id, 'status': self.status,
            'due_date': self.due_date.isoformat() if self.due_date else None,
            'created_at': self.created_at.isoformat() + 'Z',
            'items': [item.to_dict() for item in self.items],
            'total_amount': self.total_amount
        }

class InvoiceItem(db.Model):
    __tablename__ = 'invoice_items'
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoices.id'), nullable=False)
    description = db.Column(db.String(255), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    subsidy_id = db.Column(db.Integer, db.ForeignKey('subsidies.id'), nullable=True)
    
    subsidy = db.relationship('Subsidy')

    def to_dict(self):
        return { 'id': self.id, 'description': self.description, 'amount': self.amount }

class Payment(db.Model):
    __tablename__ = 'payments'
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey('student_financial_accounts.id'), nullable=False)
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoices.id'), nullable=True)
    amount = db.Column(db.Float, nullable=False)
    method = db.Column(db.String(50), nullable=False)
    notes = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(50), default='Success', nullable=False) # Success, Failed, In Process, Action Required
    transaction_date = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Stripe security & idempotency fields
    stripe_payment_intent_id = db.Column(db.String(100), unique=True, nullable=True, index=True)
    stripe_charge_id = db.Column(db.String(100), nullable=True)
    idempotency_key = db.Column(db.String(100), unique=True, nullable=True, index=True)
    is_refunded = db.Column(db.Boolean, default=False, nullable=False)
    refund_amount = db.Column(db.Float, default=0.0, nullable=False)
    
    def to_dict(self):
        return {
            'id': self.id,
            'amount': self.amount,
            'method': self.method,
            'notes': self.notes,
            'status': self.status,
            'stripe_payment_intent_id': self.stripe_payment_intent_id,
            'is_refunded': self.is_refunded,
            'refund_amount': self.refund_amount,
            'transaction_date': self.transaction_date.isoformat() + 'Z'
        }

class ProcessedStripeEvent(db.Model):
    __tablename__ = 'processed_stripe_events'
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.String(100), unique=True, nullable=False, index=True)
    event_type = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return { 'id': self.id, 'event_id': self.event_id, 'event_type': self.event_type, 'created_at': self.created_at.isoformat() + 'Z' }

class Credit(db.Model):
    __tablename__ = 'credits'
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey('student_financial_accounts.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    reason = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return { 'id': self.id, 'amount': self.amount, 'reason': self.reason, 'created_at': self.created_at.isoformat() + 'Z' }

class BillingPlan(db.Model):
    __tablename__ = 'billing_plans'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    items_json = db.Column(db.JSON, nullable=False)
    is_active = db.Column(db.Boolean, default=True)

    def to_dict(self):
        return { 'id': self.id, 'name': self.name, 'items_json': self.items_json, 'is_active': self.is_active }

class Subscription(db.Model):
    __tablename__ = 'subscriptions'
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey('student_financial_accounts.id'), nullable=False)
    plan_name = db.Column(db.String(100), nullable=False)
    status = db.Column(db.String(50), default='Active', nullable=False)
    cycle = db.Column(db.String(50), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=True)
    invoice_generation_day = db.Column(db.Integer, nullable=False)
    due_day = db.Column(db.Integer, nullable=False)
    next_invoice_date = db.Column(db.Date, nullable=False)
    items_json = db.Column(db.JSON, nullable=False)

    def to_dict(self):
        student_name = "N/A (Student Not Found)"
        if self.account and self.account.student:
            student_name = f"{self.account.student.first_name} {self.account.student.last_name}"

        return {
            'id': self.id,
            'account_id': self.account_id,
            'student_name': student_name,
            'plan_name': self.plan_name,
            'status': self.status,
            'cycle': self.cycle,
            'start_date': self.start_date.isoformat(),
            'end_date': self.end_date.isoformat() if self.end_date else None,
            'next_invoice_date': self.next_invoice_date.isoformat(),
            'total_amount': sum(float(item.get('amount') or 0) for item in self.items_json)
        }

class FinancialAuditLog(db.Model):
    __tablename__ = 'financial_audit_logs'
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey('student_financial_accounts.id'), nullable=False)
    transaction_type = db.Column(db.String(50), nullable=False) # Invoice, Payment, Credit, Refund
    transaction_id = db.Column(db.String(50), nullable=True) # ID or reference
    action = db.Column(db.String(50), nullable=False) # Create, Update, Void, Refund, Send, Receive
    amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(50), nullable=False) # Success, Failed, Pending, Voided
    actor_name = db.Column(db.String(100), nullable=True)
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    account = db.relationship('StudentFinancialAccount', backref=db.backref('audit_logs', lazy='dynamic'))

    def to_dict(self):
        return {
            'id': self.id,
            'account_id': self.account_id,
            'transaction_type': self.transaction_type,
            'transaction_id': self.transaction_id,
            'action': self.action,
            'amount': self.amount,
            'status': self.status,
            'actor_name': self.actor_name,
            'description': self.description,
            'created_at': self.created_at.isoformat() + 'Z'
        }