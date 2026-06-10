from app.models import db
from datetime import datetime

class SubsidyTransaction(db.Model):
    __tablename__ = 'subsidy_transactions'

    id = db.Column(db.Integer, primary_key=True)
    subsidy_id = db.Column(db.Integer, db.ForeignKey('subsidies.id'), nullable=False)
    transaction_type = db.Column(db.String(50), nullable=False)  # 'Invoice' or 'Payment'
    amount = db.Column(db.Float, nullable=False)
    transaction_date = db.Column(db.Date, nullable=False)
    notes = db.Column(db.Text, nullable=True)
    reference_number = db.Column(db.String(100), nullable=True) # e.g., Check #
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    subsidy = db.relationship('Subsidy', back_populates='transactions')
    distributions = db.relationship('SubsidyPaymentDistribution', back_populates='transaction', cascade="all, delete-orphan")

    def to_dict(self):
        student_names = [dist.student_account.student.first_name + " " + dist.student_account.student.last_name for dist in self.distributions]
        return {
            'id': self.id,
            'subsidy_id': self.subsidy_id,
            'transaction_type': self.transaction_type,
            'amount': self.amount,
            'transaction_date': self.transaction_date.isoformat(),
            'notes': self.notes,
            'reference_number': self.reference_number,
            'created_at': self.created_at.isoformat() + 'Z',
            'student_names': student_names
        }

class SubsidyPaymentDistribution(db.Model):
    __tablename__ = 'subsidy_payment_distributions'

    id = db.Column(db.Integer, primary_key=True)
    subsidy_transaction_id = db.Column(db.Integer, db.ForeignKey('subsidy_transactions.id'), nullable=False)
    student_financial_account_id = db.Column(db.Integer, db.ForeignKey('student_financial_accounts.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)

    transaction = db.relationship('SubsidyTransaction', back_populates='distributions')
    student_account = db.relationship('StudentFinancialAccount')