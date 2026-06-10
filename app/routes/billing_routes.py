from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt
from app.models import db
from app.models.staff_model import Staff
from app.models.super_admin_model import SuperAdmin
from app.models.student_model import Student
from app.models.financial_model import StudentFinancialAccount, Invoice, InvoiceItem, Payment, Credit, BillingPlan, Subscription, PresetChargeItem, PresetDiscount
from app.models.activity_log_model import log_activity
from app.models.subsidy_transaction_model import SubsidyTransaction
from sqlalchemy import func
from datetime import datetime, date
from dateutil.relativedelta import relativedelta

billing_bp = Blueprint('billing', __name__)

def get_actor():
    claims = get_jwt()
    email = claims.get('sub')
    if claims.get('role') == 'superadmin':
        return SuperAdmin.query.filter_by(email=email).first()
    return Staff.query.filter_by(email=email).first()

# === Recurring Plan Endpoints ===

@billing_bp.route('/plans', methods=['GET'])
@jwt_required()
def get_billing_plans():
    plans = BillingPlan.query.filter_by(is_active=True).order_by(BillingPlan.name).all()
    return jsonify([p.to_dict() for p in plans]), 200

@billing_bp.route('/plans', methods=['POST'])
@jwt_required()
def create_billing_plan():
    actor = get_actor()
    data = request.get_json()
    name = data.get('name')
    items_json = data.get('items_json')

    if not name or not items_json:
        return jsonify({"error": "Plan name and items are required."}), 400
    
    plan = BillingPlan.query.filter_by(name=name).first()
    
    if plan:
        plan.items_json = items_json
        log_activity(actor, f"Updated billing plan template: '{plan.name}'", plan)
    else:
        plan = BillingPlan(name=name, items_json=items_json)
        db.session.add(plan)
        log_activity(actor, f"Created billing plan template: '{plan.name}'", plan)
        
    db.session.commit()
    return jsonify(plan.to_dict()), 201

# === Preset Items CRUD ===

@billing_bp.route('/preset-items', methods=['GET'])
@jwt_required()
def get_preset_items():
    items = PresetChargeItem.query.filter_by(is_active=True).order_by(PresetChargeItem.description).all()
    return jsonify([i.to_dict() for i in items]), 200

@billing_bp.route('/preset-items', methods=['POST'])
@jwt_required()
def create_preset_item():
    actor = get_actor()
    data = request.get_json()
    if not data.get('description') or 'amount' not in data:
        return jsonify({"error": "Description and amount are required."}), 400
    
    new_item = PresetChargeItem(
        description=data['description'],
        amount=float(data['amount'])
    )
    db.session.add(new_item)
    log_activity(actor, f"Created preset charge: '{new_item.description}'", new_item)
    db.session.commit()
    return jsonify(new_item.to_dict()), 201

@billing_bp.route('/preset-items/<int:item_id>', methods=['PUT'])
@jwt_required()
def update_preset_item(item_id):
    actor = get_actor()
    item = PresetChargeItem.query.get_or_404(item_id)
    data = request.get_json()
    
    item.description = data.get('description', item.description)
    item.amount = float(data.get('amount', item.amount))
    item.is_active = data.get('is_active', item.is_active)
    
    log_activity(actor, f"Updated preset charge: '{item.description}'", item)
    db.session.commit()
    return jsonify(item.to_dict()), 200

@billing_bp.route('/preset-items/<int:item_id>', methods=['DELETE'])
@jwt_required()
def delete_preset_item(item_id):
    actor = get_actor()
    item = PresetChargeItem.query.get_or_404(item_id)
    
    log_activity(actor, f"Deleted preset charge: '{item.description}'", item)
    db.session.delete(item)
    db.session.commit()
    return jsonify({"message": "Preset item deleted successfully."}), 200

# === Preset Discounts CRUD ===

@billing_bp.route('/discounts', methods=['GET'])
@jwt_required()
def get_preset_discounts():
    discounts = PresetDiscount.query.filter_by(is_active=True).order_by(PresetDiscount.description).all()
    return jsonify([d.to_dict() for d in discounts]), 200

@billing_bp.route('/discounts', methods=['POST'])
@jwt_required()
def create_preset_discount():
    actor = get_actor()
    data = request.get_json()
    if not data.get('description'):
        return jsonify({"error": "Description is required."}), 400
    
    new_discount = PresetDiscount(description=data['description'])
    db.session.add(new_discount)
    log_activity(actor, f"Created preset discount: '{new_discount.description}'", new_discount)
    db.session.commit()
    return jsonify(new_discount.to_dict()), 201

@billing_bp.route('/discounts/<int:discount_id>', methods=['PUT'])
@jwt_required()
def update_preset_discount(discount_id):
    actor = get_actor()
    discount = PresetDiscount.query.get_or_404(discount_id)
    data = request.get_json()
    
    discount.description = data.get('description', discount.description)
    discount.is_active = data.get('is_active', discount.is_active)
    
    log_activity(actor, f"Updated preset discount: '{discount.description}'", discount)
    db.session.commit()
    return jsonify(discount.to_dict()), 200

@billing_bp.route('/discounts/<int:discount_id>', methods=['DELETE'])
@jwt_required()
def delete_preset_discount(discount_id):
    actor = get_actor()
    discount = PresetDiscount.query.get_or_404(discount_id)
    
    log_activity(actor, f"Deleted preset discount: '{discount.description}'", discount)
    db.session.delete(discount)
    db.session.commit()
    return jsonify({"message": "Preset discount deleted successfully."}), 200

@billing_bp.route('/subscriptions', methods=['GET'])
@jwt_required()
def get_subscriptions():
    subs = Subscription.query.filter_by(status='Active').all()
    return jsonify([s.to_dict() for s in subs]), 200

@billing_bp.route('/subscriptions', methods=['POST'])
@jwt_required()
def create_subscriptions():
    actor = get_actor()
    data = request.get_json()
    student_ids = data.get('student_ids', [])
    plan_data = data.get('plan_data', {})
    if not all([student_ids, plan_data]):
        return jsonify({"error": "Student IDs and plan data are required."}), 400

    start_date = datetime.strptime(plan_data['start_date'], '%Y-%m-%dT%H:%M:%S.%fZ').date()
    
    for student_id in student_ids:
        student = Student.query.get(student_id)
        if not student or not student.financial_account: continue

        # Calculate the first invoice date
        invoice_day = int(plan_data['invoice_generation_day'])
        next_invoice_date = start_date.replace(day=invoice_day)
        if start_date.day > invoice_day:
            next_invoice_date += relativedelta(months=1)

        sub = Subscription(
            account_id=student.financial_account.id,
            plan_name=plan_data['plan_name'],
            cycle=plan_data['cycle'],
            start_date=start_date,
            end_date=datetime.strptime(plan_data['end_date'], '%Y-%m-%dT%H:%M:%S.%fZ').date() if plan_data.get('end_date') else None,
            invoice_generation_day=invoice_day,
            due_day=int(plan_data['due_day']),
            next_invoice_date=next_invoice_date,
            items_json=plan_data['items_json']
        )
        db.session.add(sub)
    
    log_activity(actor, f"Created recurring plan '{plan_data['plan_name']}' for {len(student_ids)} student(s)")
    db.session.commit()
    return jsonify({"message": "Recurring plans created successfully."}), 201


@billing_bp.route('/accounts/<int:student_id>/invoices', methods=['POST'])
@jwt_required()
def create_invoice(student_id):
    actor = get_actor()
    student = Student.query.get_or_404(student_id)
    account = student.financial_account
    if not account:
        return jsonify({"error": "Financial account not found for this student."}), 404
        
    data = request.get_json()
    items = data.get('items', [])
    if not items:
        return jsonify({"error": "Invoice must have at least one item."}), 400

    new_invoice = Invoice(
        account_id=account.id,
        status=data.get('status', 'Draft'),
        due_date=datetime.strptime(data['due_date'], '%Y-%m-%d').date() if data.get('due_date') else None
    )
    
    for item in items:
        new_item = InvoiceItem(
            description=item['description'], 
            amount=item['amount'],
            subsidy_id=item.get('subsidy_id')
        )
        if item.get('subsidy_id'):
            subsidy_invoice_transaction = SubsidyTransaction(
                subsidy_id=item.get('subsidy_id'),
                transaction_type='Invoice',
                amount=-item.get('amount'), # Store as positive value
                transaction_date=new_invoice.due_date or date.today(),
                notes=f"Applied to invoice for {student.first_name} {student.last_name}"
            )
            db.session.add(subsidy_invoice_transaction)

        new_invoice.items.append(new_item)
    
    db.session.add(new_invoice)
    log_activity(actor, f"Created invoice for {student.first_name} {student.last_name}", new_invoice)
    db.session.commit()
    return jsonify(new_invoice.to_dict()), 201

@billing_bp.route('/accounts/<int:student_id>/payments', methods=['POST'])
@jwt_required()
def receive_payment(student_id):
    actor = get_actor()
    student = Student.query.get_or_404(student_id)
    account = student.financial_account
    if not account: return jsonify({"error": "Financial account not found."}), 404
    data = request.get_json()
    amount = data.get('amount')
    if not amount or float(amount) <= 0: return jsonify({"error": "Invalid payment amount."}), 400

    new_payment = Payment(account_id=account.id, invoice_id=data.get('invoice_id'), amount=float(amount), method=data.get('method', 'Cash'), notes=data.get('notes'), transaction_date=datetime.utcnow())
    db.session.add(new_payment)
    
    if data.get('invoice_id'):
        invoice = Invoice.query.get(data.get('invoice_id'))
        if invoice:
            total_paid_for_invoice = db.session.query(func.sum(Payment.amount)).filter_by(invoice_id=invoice.id).scalar() or 0
            if total_paid_for_invoice >= invoice.total_amount:
                invoice.status = 'Paid'

    log_activity(actor, f"Recorded payment of ${amount} for {student.first_name} {student.last_name}", new_payment)
    db.session.commit()
    return jsonify(new_payment.to_dict()), 201

@billing_bp.route('/accounts/<int:student_id>/credits', methods=['POST'])
@jwt_required()
def add_credit(student_id):
    actor = get_actor()
    student = Student.query.get_or_404(student_id)
    account = student.financial_account
    if not account: return jsonify({"error": "Financial account not found."}), 404
    data = request.get_json()
    amount = data.get('amount')
    reason = data.get('reason')
    if not amount or float(amount) <= 0 or not reason: return jsonify({"error": "Amount and reason are required."}), 400

    new_credit = Credit(account_id=account.id, amount=float(amount), reason=reason)
    db.session.add(new_credit)
    log_activity(actor, f"Added credit of ${amount} for {student.first_name} {student.last_name}", new_credit)
    db.session.commit()
    return jsonify(new_credit.to_dict()), 201

@billing_bp.route('/accounts', methods=['GET'])
@jwt_required()
def get_all_accounts():
    students = Student.query.filter_by(status='Active').order_by(Student.last_name, Student.first_name).all()
    results = []
    for student in students:
        if not student.financial_account:
            account = StudentFinancialAccount(student=student)
            db.session.add(account)
            db.session.commit()
        else:
            account = student.financial_account

        total_invoiced = db.session.query(func.sum(InvoiceItem.amount)).join(Invoice).filter(Invoice.account_id == account.id).scalar() or 0
        total_paid = db.session.query(func.sum(Payment.amount)).filter(Payment.account_id == account.id).scalar() or 0
        total_credited = db.session.query(func.sum(Credit.amount)).filter(Credit.account_id == account.id).scalar() or 0
        balance = total_invoiced - (total_paid + total_credited)
        last_invoice = Invoice.query.filter_by(account_id=account.id).order_by(Invoice.created_at.desc()).first()
        last_payment = Payment.query.filter_by(account_id=account.id).order_by(Payment.transaction_date.desc()).first()
        
        results.append({
            'student_id': student.id, 'student_name': f"{student.first_name} {student.last_name}",
            'open_balance': balance, 'last_invoice_date': last_invoice.created_at.isoformat() if last_invoice else None,
            'last_invoice_amount': last_invoice.total_amount if last_invoice else None,
            'last_payment_date': last_payment.transaction_date.isoformat() if last_payment else None,
            'last_payment_amount': last_payment.amount if last_payment else None
        })
    return jsonify(results), 200

@billing_bp.route('/accounts/<int:student_id>', methods=['GET'])
@jwt_required()
def get_student_ledger(student_id):
    student = Student.query.get_or_404(student_id)
    account = student.financial_account
    if not account: return jsonify({"transactions": [], "summary": {}}), 200

    invoices = Invoice.query.filter_by(account_id=account.id).all()
    payments = Payment.query.filter_by(account_id=account.id).all()
    credits = Credit.query.filter_by(account_id=account.id).all()

    transactions = []
    balance = 0
    all_tx = []
    for inv in invoices: all_tx.append({'type': 'Invoice', 'date': inv.created_at, 'obj': inv})
    for p in payments: all_tx.append({'type': 'Payment', 'date': p.transaction_date, 'obj': p})
    for c in credits: all_tx.append({'type': 'Credit', 'date': c.created_at, 'obj': c})
    all_tx.sort(key=lambda x: x['date'], reverse=True)
    
    for tx_item in reversed(all_tx):
        if tx_item['type'] == 'Invoice': balance += tx_item['obj'].total_amount
        else: balance -= tx_item['obj'].amount
        tx_item['balance'] = balance

    for tx_item in all_tx:
        obj = tx_item['obj']
        if tx_item['type'] == 'Invoice': transactions.append({'type': 'Invoice', 'date': obj.created_at.isoformat() + 'Z', 'description': ", ".join([i.description for i in obj.items]), 'amount': obj.total_amount, 'status': obj.status, 'balance': tx_item['balance']})
        elif tx_item['type'] == 'Payment': transactions.append({'type': 'Payment', 'date': obj.transaction_date.isoformat() + 'Z', 'description': f"Payment via {obj.method}", 'amount': -obj.amount, 'status': 'Success', 'balance': tx_item['balance']})
        elif tx_item['type'] == 'Credit': transactions.append({'type': 'Credit', 'date': obj.created_at.isoformat() + 'Z', 'description': obj.reason, 'amount': -obj.amount, 'status': 'Applied', 'balance': tx_item['balance']})

    total_invoiced = sum(inv.total_amount for inv in invoices)
    total_paid = sum(p.amount for p in payments)
    total_credited = sum(c.amount for c in credits)
    final_balance = total_invoiced - (total_paid + total_credited)

    summary = {"paid": total_paid, "credited": total_credited, "unpaid": final_balance}
    return jsonify({"transactions": transactions, "summary": summary, "student_name": f"{student.first_name} {student.last_name}"}), 200