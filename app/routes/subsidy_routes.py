from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt
from app.models import db
from app.models.subsidy_model import Subsidy
from app.models.subsidy_transaction_model import SubsidyTransaction, SubsidyPaymentDistribution
from app.models.financial_model import InvoiceItem, StudentFinancialAccount, Invoice
from app.models.student_model import Student
from app.models.super_admin_model import SuperAdmin
from app.models.staff_model import Staff
from sqlalchemy import func, case
from datetime import datetime

subsidy_bp = Blueprint('subsidy', __name__)

def get_actor():
    claims = get_jwt()
    email = claims.get('sub')
    if claims.get('role') == 'superadmin':
        return SuperAdmin.query.filter_by(email=email).first()
    return Staff.query.filter_by(email=email).first()

@subsidy_bp.route('/', methods=['GET'])
@jwt_required()
def get_subsidies():
    subsidies = Subsidy.query.order_by(Subsidy.name).all()
    results = []

    for sub in subsidies:
        # Sum of all negative invoice items linked to this subsidy
        invoiced = db.session.query(func.sum(InvoiceItem.amount)).filter(InvoiceItem.subsidy_id == sub.id).scalar() or 0
        # Sum of all payment transactions for this subsidy
        received = db.session.query(func.sum(SubsidyTransaction.amount)).filter(
            SubsidyTransaction.subsidy_id == sub.id,
            SubsidyTransaction.transaction_type == 'Payment'
        ).scalar() or 0
        # Sum of all write-off transactions for this subsidy
        written_off = db.session.query(func.sum(SubsidyTransaction.amount)).filter(
            SubsidyTransaction.subsidy_id == sub.id,
            SubsidyTransaction.transaction_type == 'Write-Off'
        ).scalar() or 0
        
        # Invoiced is negative in InvoiceItem, so abs(invoiced) is total invoiced to subsidy
        balance = max(0.0, abs(invoiced) - (received + written_off))

        results.append({
            'id': sub.id,
            'name': sub.name,
            'is_active': sub.is_active,
            'invoiced': abs(invoiced),
            'received': received,
            'written_off': written_off,
            'balance': balance
        })

    return jsonify(results), 200

@subsidy_bp.route('/', methods=['POST'])
@jwt_required()
def create_subsidy():
    data = request.get_json()
    name = data.get('name')
    if not name:
        return jsonify({"error": "Subsidy name is required."}), 400
    
    if Subsidy.query.filter_by(name=name).first():
        return jsonify({"error": "A subsidy with this name already exists."}), 409

    new_subsidy = Subsidy(name=name)
    db.session.add(new_subsidy)
    db.session.commit()
    return jsonify(new_subsidy.to_dict()), 201


@subsidy_bp.route('/<int:subsidy_id>', methods=['GET'])
@jwt_required()
def get_subsidy_details(subsidy_id):
    subsidy = Subsidy.query.get_or_404(subsidy_id)

    # Overall Summary
    total_invoiced = db.session.query(func.sum(InvoiceItem.amount)).filter(InvoiceItem.subsidy_id == subsidy_id).scalar() or 0
    total_received = db.session.query(func.sum(SubsidyTransaction.amount)).filter(
        SubsidyTransaction.subsidy_id == subsidy_id, SubsidyTransaction.transaction_type == 'Payment'
    ).scalar() or 0
    total_written_off = db.session.query(func.sum(SubsidyTransaction.amount)).filter(
        SubsidyTransaction.subsidy_id == subsidy_id, SubsidyTransaction.transaction_type == 'Write-Off'
    ).scalar() or 0

    balance = max(0.0, abs(total_invoiced) - (total_received + total_written_off))

    # Student Summary
    student_summary_query = db.session.query(
        Student.id,
        Student.first_name,
        Student.last_name,
        func.sum(case((InvoiceItem.subsidy_id == subsidy_id, InvoiceItem.amount), else_=0)).label('invoiced'),
        func.sum(case((SubsidyPaymentDistribution.subsidy_transaction_id.isnot(None), SubsidyPaymentDistribution.amount), else_=0)).label('received')
    ).join(StudentFinancialAccount, Student.financial_account).outerjoin(Invoice, StudentFinancialAccount.invoices).outerjoin(InvoiceItem).outerjoin(SubsidyPaymentDistribution, StudentFinancialAccount.id == SubsidyPaymentDistribution.student_financial_account_id).outerjoin(SubsidyTransaction, SubsidyPaymentDistribution.transaction).filter(
        db.or_(InvoiceItem.subsidy_id == subsidy_id, SubsidyTransaction.subsidy_id == subsidy_id)
    ).group_by(Student.id).all()
    
    student_summary = [
        {
            'student_id': s.id,
            'student_name': f"{s.first_name} {s.last_name}",
            'invoiced': abs(s.invoiced or 0),
            'received': s.received or 0,
            'balance': max(0.0, abs(s.invoiced or 0) - (s.received or 0))
        } for s in student_summary_query
    ]

    # Transaction Detail
    transactions = SubsidyTransaction.query.filter_by(subsidy_id=subsidy_id).order_by(SubsidyTransaction.transaction_date.desc()).all()
    transaction_detail = [t.to_dict() for t in transactions]

    return jsonify({
        'id': subsidy.id,
        'name': subsidy.name,
        'total_invoiced': abs(total_invoiced),
        'total_received': total_received,
        'total_written_off': total_written_off,
        'balance': balance,
        'student_summary': student_summary,
        'transaction_detail': transaction_detail
    }), 200


@subsidy_bp.route('/<int:subsidy_id>/transactions', methods=['POST'])
@jwt_required()
def add_subsidy_transaction(subsidy_id):
    actor = get_actor()
    data = request.get_json() or {}
    transaction_type = data.get('transaction_type', 'Payment')
    if transaction_type not in ['Payment', 'Write-Off']:
        return jsonify({"error": "Only 'Payment' and 'Write-Off' transaction types are supported."}), 400

    try:
        total_amount = float(data.get('amount') or 0)
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid amount provided."}), 400

    if total_amount <= 0:
        return jsonify({"error": "Amount must be greater than 0."}), 400

    distributions = data.get('distributions', [])
    if distributions:
        distributed_amount = sum(float(d.get('amount', 0)) for d in distributions)
        if abs(total_amount - distributed_amount) > 0.01:
            return jsonify({"error": "Total amount must equal the sum of distributed amounts."}), 400

    tx_date_str = data.get('transaction_date')
    if tx_date_str:
        try:
            tx_date = datetime.strptime(tx_date_str, '%Y-%m-%d').date()
        except ValueError:
            tx_date = datetime.utcnow().date()
    else:
        tx_date = datetime.utcnow().date()

    try:
        new_transaction = SubsidyTransaction(
            subsidy_id=subsidy_id,
            transaction_type=transaction_type,
            amount=total_amount,
            transaction_date=tx_date,
            notes=data.get('notes'),
            reference_number=data.get('reference_number')
        )
        db.session.add(new_transaction)
        db.session.flush()

        if distributions:
            for dist_data in distributions:
                student = Student.query.get(dist_data['student_id'])
                if not student or not student.financial_account:
                    raise ValueError(f"Student with ID {dist_data['student_id']} not found or has no financial account.")
                
                dist = SubsidyPaymentDistribution(
                    subsidy_transaction_id=new_transaction.id,
                    student_financial_account_id=student.financial_account.id,
                    amount=dist_data['amount']
                )
                db.session.add(dist)
        
        db.session.commit()
        return jsonify(new_transaction.to_dict()), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500