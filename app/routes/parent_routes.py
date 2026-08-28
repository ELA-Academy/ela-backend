from flask import Blueprint, jsonify, request, current_app, send_from_directory
from flask_jwt_extended import jwt_required, get_jwt
from app.models import db
from app.models.student_model import Student, Parent, ParentPaymentMethod
from app.models.financial_model import StudentFinancialAccount, Invoice, InvoiceItem, Payment, Credit, FinancialAuditLog
from app.models.student_document_model import StudentDocument
from app.models.activity_log_model import log_activity
from sqlalchemy import func
from datetime import datetime, date
import os
import uuid
from werkzeug.utils import secure_filename

parent_bp = Blueprint('parent', __name__)

def get_current_parent():
    claims = get_jwt()
    email = claims.get('sub')
    role = claims.get('role')
    if role != 'parent':
        return None
    return Parent.query.filter(db.func.lower(Parent.email) == db.func.lower(email)).first()

@parent_bp.route('/me', methods=['GET'])
@jwt_required()
def get_parent_profile():
    parent = get_current_parent()
    if not parent:
        return jsonify({"error": "Parent not found or unauthorized"}), 403
    
    children_data = []
    for child in parent.children:
        account = child.financial_account
        open_bal = 0.0
        if account:
            total_inv = db.session.query(func.sum(InvoiceItem.amount)).join(Invoice).filter(Invoice.account_id == account.id).scalar() or 0.0
            total_pay = db.session.query(func.sum(Payment.amount)).filter(Payment.account_id == account.id).scalar() or 0.0
            total_cred = db.session.query(func.sum(Credit.amount)).filter(Credit.account_id == account.id).scalar() or 0.0
            open_bal = total_inv - (total_pay + total_cred)
            
        children_data.append({
            'id': child.id,
            'student_id_number': child.student_id_number,
            'first_name': child.first_name,
            'last_name': child.last_name,
            'grade_level': child.grade_level,
            'status': child.status,
            'date_of_birth': child.date_of_birth.isoformat() if child.date_of_birth else None,
            'open_balance': open_bal
        })

    return jsonify({
        'id': parent.id,
        'first_name': parent.first_name,
        'last_name': parent.last_name,
        'email': parent.email,
        'phone': parent.phone,
        'sign_in_pin': parent.sign_in_pin or "2963",
        'children': children_data
    }), 200

@parent_bp.route('/dashboard', methods=['GET'])
@jwt_required()
def get_parent_dashboard():
    parent = get_current_parent()
    if not parent:
        return jsonify({"error": "Unauthorized"}), 403

    student_ids = [c.id for c in parent.children]
    accounts = StudentFinancialAccount.query.filter(StudentFinancialAccount.student_id.in_(student_ids)).all() if student_ids else []
    
    total_balance = 0.0
    amount_in_process = 0.0
    
    for acc in accounts:
        total_inv = db.session.query(func.sum(InvoiceItem.amount)).join(Invoice).filter(Invoice.account_id == acc.id).scalar() or 0.0
        total_pay = db.session.query(func.sum(Payment.amount)).filter(Payment.account_id == acc.id).scalar() or 0.0
        total_cred = db.session.query(func.sum(Credit.amount)).filter(Credit.account_id == acc.id).scalar() or 0.0
        bal = total_inv - (total_pay + total_cred)
        total_balance += bal
        
        in_process = db.session.query(func.sum(Payment.amount)).filter(
            Payment.account_id == acc.id,
            Payment.status == 'In Process'
        ).scalar() or 0.0
        amount_in_process += in_process

    # Check default payment method
    default_pm = parent.payment_methods.filter_by(is_default=True).first()

    # Activities list for children
    activities = []
    # Fetch recent invoices and payments
    for child in parent.children:
        if child.financial_account:
            recent_invoices = Invoice.query.filter_by(account_id=child.financial_account.id).order_by(Invoice.created_at.desc()).limit(3).all()
            for inv in recent_invoices:
                activities.append({
                    'id': f"inv_{inv.id}",
                    'title': f"Invoice Issued for {child.first_name}",
                    'description': f"Total: ${inv.total_amount:.2f} - Status: {inv.status}",
                    'timestamp': inv.created_at.isoformat() + 'Z',
                    'category': 'billing',
                    'student_name': f"{child.first_name} {child.last_name}"
                })
            recent_payments = Payment.query.filter_by(account_id=child.financial_account.id).order_by(Payment.transaction_date.desc()).limit(3).all()
            for p in recent_payments:
                activities.append({
                    'id': f"pay_{p.id}",
                    'title': f"Payment of ${abs(p.amount):.2f} Recorded",
                    'description': f"Method: {p.method} - {child.first_name} {child.last_name}",
                    'timestamp': p.transaction_date.isoformat() + 'Z',
                    'category': 'payment',
                    'student_name': f"{child.first_name} {child.last_name}"
                })

    activities.sort(key=lambda x: x['timestamp'], reverse=True)

    return jsonify({
        'parent_name': f"{parent.first_name} {parent.last_name}",
        'sign_in_pin': parent.sign_in_pin or "2963",
        'current_balance': total_balance,
        'amount_in_process': amount_in_process,
        'auto_pay_enabled': bool(default_pm),
        'default_payment_method': default_pm.to_dict() if default_pm else None,
        'children_count': len(parent.children),
        'children': [{'id': c.id, 'name': f"{c.first_name} {c.last_name}", 'grade': c.grade_level} for c in parent.children],
        'activities': activities[:10]
    }), 200

@parent_bp.route('/payments', methods=['GET'])
@jwt_required()
def get_parent_payments_summary():
    parent = get_current_parent()
    if not parent:
        return jsonify({"error": "Unauthorized"}), 403

    student_id_filter = request.args.get('student_id', type=int)

    children = parent.children
    if student_id_filter:
        children = [c for c in children if c.id == student_id_filter]

    student_ids = [c.id for c in children]
    accounts = StudentFinancialAccount.query.filter(StudentFinancialAccount.student_id.in_(student_ids)).all() if student_ids else []
    account_ids = [a.id for a in accounts]

    # Map account to student
    acc_to_student = {a.id: a.student for a in accounts}

    total_invoiced = 0.0
    total_paid = 0.0
    total_credited = 0.0
    amount_in_process = 0.0

    all_tx = []

    if account_ids:
        invoices = Invoice.query.filter(Invoice.account_id.in_(account_ids)).all()
        payments = Payment.query.filter(Payment.account_id.in_(account_ids)).all()
        credits = Credit.query.filter(Credit.account_id.in_(account_ids)).all()

        for inv in invoices:
            student = acc_to_student.get(inv.account_id)
            student_name = f"{student.first_name} {student.last_name}" if student else "Student"
            desc = ", ".join([item.description for item in inv.items]) if inv.items else "Tuition & Fees"
            all_tx.append({
                'id': f"inv_{inv.id}",
                'raw_id': inv.id,
                'type': 'Invoice',
                'date': inv.created_at,
                'due_date': inv.due_date.isoformat() if inv.due_date else None,
                'student_id': student.id if student else None,
                'student_name': student_name,
                'description': desc,
                'amount': inv.total_amount,
                'status': inv.status,
                'method': None,
                'notes': None
            })

        for p in payments:
            student = acc_to_student.get(p.account_id)
            student_name = f"{student.first_name} {student.last_name}" if student else "Student"
            all_tx.append({
                'id': f"pay_{p.id}",
                'raw_id': p.id,
                'type': 'Payment',
                'date': p.transaction_date,
                'due_date': None,
                'student_id': student.id if student else None,
                'student_name': student_name,
                'description': f"Payment via {p.method}" + (f" - {p.notes}" if p.notes else ""),
                'amount': -p.amount,
                'status': p.status,
                'method': p.method,
                'notes': p.notes
            })
            if p.status == 'In Process':
                amount_in_process += p.amount

        for c in credits:
            student = acc_to_student.get(c.account_id)
            student_name = f"{student.first_name} {student.last_name}" if student else "Student"
            all_tx.append({
                'id': f"cred_{c.id}",
                'raw_id': c.id,
                'type': 'Credit',
                'date': c.created_at,
                'due_date': None,
                'student_id': student.id if student else None,
                'student_name': student_name,
                'description': c.reason or "Credit Adjustment",
                'amount': -c.amount,
                'status': 'Applied',
                'method': None,
                'notes': c.reason
            })

    # Sort chronologically to compute running balance
    all_tx.sort(key=lambda x: x['date'])
    running_balance = 0.0
    for tx in all_tx:
        if tx['type'] == 'Invoice':
            running_balance += tx['amount']
            total_invoiced += tx['amount']
        else:
            running_balance -= abs(tx['amount'])
            if tx['type'] == 'Payment':
                total_paid += abs(tx['amount'])
            else:
                total_credited += abs(tx['amount'])
        tx['balance'] = running_balance

    # Format for reverse chronological return (newest first)
    all_tx.sort(key=lambda x: x['date'], reverse=True)
    formatted_transactions = []
    for tx in all_tx:
        formatted_transactions.append({
            'id': tx['id'],
            'raw_id': tx['raw_id'],
            'type': tx['type'],
            'date': tx['date'].isoformat() + 'Z' if isinstance(tx['date'], datetime) else tx['date'].isoformat(),
            'due_date': tx['due_date'],
            'student_id': tx['student_id'],
            'student_name': tx['student_name'],
            'description': tx['description'],
            'amount': tx['amount'],
            'balance': tx['balance'],
            'status': tx['status'],
            'method': tx['method'],
            'notes': tx['notes']
        })

    current_balance = total_invoiced - (total_paid + total_credited)
    default_pm = parent.payment_methods.filter_by(is_default=True).first()

    return jsonify({
        'summary': {
            'current_balance': current_balance,
            'amount_in_process': amount_in_process,
            'total_invoiced': total_invoiced,
            'total_paid': total_paid,
            'total_credited': total_credited,
            'auto_pay_enabled': bool(default_pm)
        },
        'children': [{'id': c.id, 'name': f"{c.first_name} {c.last_name}", 'grade': c.grade_level} for c in parent.children],
        'default_payment_method': default_pm.to_dict() if default_pm else None,
        'transactions': formatted_transactions,
        'total_count': len(formatted_transactions)
    }), 200

# === Payment Methods Endpoints ===

@parent_bp.route('/payment-methods', methods=['GET'])
@jwt_required()
def get_payment_methods():
    parent = get_current_parent()
    if not parent:
        return jsonify({"error": "Unauthorized"}), 403

    methods = parent.payment_methods.order_by(ParentPaymentMethod.is_default.desc(), ParentPaymentMethod.created_at.desc()).all()
    return jsonify([m.to_dict() for m in methods]), 200

@parent_bp.route('/payment-methods', methods=['POST'])
@jwt_required()
def add_payment_method():
    parent = get_current_parent()
    if not parent:
        return jsonify({"error": "Unauthorized"}), 403

    data = request.get_json() or {}
    method_type = data.get('method_type', 'card') # 'card' or 'bank_account'
    last4 = (data.get('last4') or '').strip()
    is_default = bool(data.get('is_default', False))

    if not last4 or len(last4) < 4:
        return jsonify({"error": "Valid 4-digit card or account number ending is required."}), 400

    last4 = last4[-4:]

    # Check if this is the first payment method; if so, make it default automatically
    existing_count = parent.payment_methods.count()
    if existing_count == 0 or is_default:
        is_default = True
        ParentPaymentMethod.query.filter_by(parent_id=parent.id).update({'is_default': False})

    new_pm = ParentPaymentMethod(
        parent_id=parent.id,
        method_type=method_type,
        card_brand=data.get('card_brand') if method_type == 'card' else None,
        last4=last4,
        exp_month=int(data.get('exp_month')) if data.get('exp_month') else None,
        exp_year=int(data.get('exp_year')) if data.get('exp_year') else None,
        bank_name=data.get('bank_name', 'Verified Bank') if method_type == 'bank_account' else None,
        account_type=data.get('account_type', 'checking') if method_type == 'bank_account' else None,
        account_holder_name=data.get('account_holder_name', f"{parent.first_name} {parent.last_name}"),
        is_default=is_default
    )
    db.session.add(new_pm)
    db.session.commit()

    return jsonify(new_pm.to_dict()), 201

@parent_bp.route('/payment-methods/<int:pm_id>/default', methods=['PUT', 'POST'])
@jwt_required()
def set_default_payment_method(pm_id):
    parent = get_current_parent()
    if not parent:
        return jsonify({"error": "Unauthorized"}), 403

    pm = ParentPaymentMethod.query.filter_by(id=pm_id, parent_id=parent.id).first_or_404()
    
    ParentPaymentMethod.query.filter_by(parent_id=parent.id).update({'is_default': False})
    pm.is_default = True
    db.session.commit()

    return jsonify(pm.to_dict()), 200

@parent_bp.route('/payment-methods/<int:pm_id>', methods=['DELETE'])
@jwt_required()
def delete_payment_method(pm_id):
    parent = get_current_parent()
    if not parent:
        return jsonify({"error": "Unauthorized"}), 403

    pm = ParentPaymentMethod.query.filter_by(id=pm_id, parent_id=parent.id).first_or_404()
    was_default = pm.is_default
    db.session.delete(pm)
    db.session.commit()

    # If deleted was default, set next remaining as default
    if was_default:
        next_pm = parent.payment_methods.first()
        if next_pm:
            next_pm.is_default = True
            db.session.commit()

    return jsonify({"message": "Payment method removed successfully."}), 200

# === Submit Payment Endpoint ===

@parent_bp.route('/pay', methods=['POST'])
@jwt_required()
def submit_payment():
    parent = get_current_parent()
    if not parent:
        return jsonify({"error": "Unauthorized"}), 403

    data = request.get_json() or {}
    amount = data.get('amount')
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid payment amount."}), 400

    if amount <= 0:
        return jsonify({"error": "Payment amount must be greater than $0."}), 400

    student_id = data.get('student_id')
    invoice_id = data.get('invoice_id')
    payment_method_id = data.get('payment_method_id')
    custom_method_name = data.get('method_name')

    # Resolve target student
    target_student = None
    if student_id:
        target_student = Student.query.get(student_id)
        if not target_student or parent not in target_student.parents:
            return jsonify({"error": "Selected student is invalid."}), 400
    elif parent.children:
        target_student = parent.children[0]
    else:
        return jsonify({"error": "No student associated with this parent account."}), 400

    account = target_student.financial_account
    if not account:
        account = StudentFinancialAccount(student=target_student)
        db.session.add(account)
        db.session.commit()

    # Resolve payment method display label
    method_label = "Online Payment"
    if payment_method_id:
        pm = ParentPaymentMethod.query.filter_by(id=payment_method_id, parent_id=parent.id).first()
        if pm:
            if pm.method_type == 'bank_account':
                method_label = f"ACH Bank ({pm.bank_name or 'Bank'} - *{pm.last4})"
            else:
                method_label = f"Card ({pm.card_brand or 'Card'} - *{pm.last4})"
    elif custom_method_name:
        method_label = custom_method_name
    else:
        default_pm = parent.payment_methods.filter_by(is_default=True).first()
        if default_pm:
            if default_pm.method_type == 'bank_account':
                method_label = f"ACH Bank ({default_pm.bank_name or 'Bank'} - *{default_pm.last4})"
            else:
                method_label = f"Card ({default_pm.card_brand or 'Card'} - *{default_pm.last4})"

    # Create Payment record
    new_payment = Payment(
        account_id=account.id,
        invoice_id=invoice_id,
        amount=amount,
        method=method_label,
        notes=f"Paid by {parent.first_name} {parent.last_name} via Parent Portal",
        status='Success',
        transaction_date=datetime.utcnow()
    )
    db.session.add(new_payment)

    # Check and update invoice status if invoice_id provided
    if invoice_id:
        inv = Invoice.query.filter_by(id=invoice_id, account_id=account.id).first()
        if inv:
            total_paid_inv = db.session.query(func.sum(Payment.amount)).filter_by(invoice_id=inv.id).scalar() or 0.0
            if (total_paid_inv + amount) >= inv.total_amount:
                inv.status = 'Paid'

    # Financial audit log
    try:
        log = FinancialAuditLog(
            account_id=account.id,
            transaction_type='Payment',
            transaction_id=str(new_payment.id),
            action='Receive',
            amount=amount,
            status='Success',
            actor_name=f"{parent.first_name} {parent.last_name} (Parent)",
            description=f"Parent Portal payment of ${amount:.2f} processed via {method_label}"
        )
        db.session.add(log)
    except Exception as e:
        print(f"[FinancialAuditLog] Error logging parent payment: {e}")

    log_activity(parent, f"Submitted payment of ${amount:.2f} for {target_student.first_name} {target_student.last_name}", new_payment)
    db.session.commit()

    return jsonify({
        "message": "Payment submitted successfully!",
        "payment": new_payment.to_dict()
    }), 201

# === Parent Documents Endpoints ===

@parent_bp.route('/documents', methods=['GET'])
@jwt_required()
def get_parent_documents():
    parent = get_current_parent()
    if not parent:
        return jsonify({"error": "Unauthorized"}), 403

    student_id_filter = request.args.get('student_id', type=int)
    children = parent.children
    if student_id_filter:
        children = [c for c in children if c.id == student_id_filter]

    student_ids = [c.id for c in children]
    
    docs = StudentDocument.query.filter(StudentDocument.student_id.in_(student_ids)).order_by(StudentDocument.created_at.desc()).all() if student_ids else []

    # Format document list with student name
    doc_results = []
    for d in docs:
        doc_dict = d.to_dict()
        doc_dict['student_name'] = f"{d.student.first_name} {d.student.last_name}" if d.student else "Student"
        doc_results.append(doc_dict)

    # Standard document requests list
    document_requests = [
        {
            'id': 'req_immunization',
            'title': 'State Immunization & Health Form',
            'description': 'Required yearly immunization clearance from your pediatrician.',
            'category': 'Medical',
            'status': 'Completed' if any(d.document_type == 'Immunization' for d in docs) else 'Pending'
        },
        {
            'id': 'req_emergency_form',
            'title': 'Emergency Contact & Medical Release',
            'description': 'Authorization for emergency medical treatment and contact list.',
            'category': 'Enrollment',
            'status': 'Completed' if any('Emergency' in d.name for d in docs) else 'Pending'
        }
    ]

    return jsonify({
        'documents': doc_results,
        'document_requests': document_requests,
        'children': [{'id': c.id, 'name': f"{c.first_name} {c.last_name}"} for c in parent.children]
    }), 200

@parent_bp.route('/documents', methods=['POST'])
@jwt_required()
def upload_parent_document():
    parent = get_current_parent()
    if not parent:
        return jsonify({"error": "Unauthorized"}), 403

    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded."}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file."}), 400

    student_id = request.form.get('student_id', type=int)
    if not student_id:
        if parent.children:
            student_id = parent.children[0].id
        else:
            return jsonify({"error": "No student associated with this parent."}), 400

    student = Student.query.get(student_id)
    if not student or parent not in student.parents:
        return jsonify({"error": "Invalid student selected."}), 400

    name = request.form.get('name') or file.filename
    doc_type = request.form.get('document_type', 'Document')
    expiry_date_str = request.form.get('expiry_date')
    expiry_date = None
    if expiry_date_str:
        try:
            expiry_date = datetime.strptime(expiry_date_str, "%Y-%m-%d").date()
        except ValueError:
            pass

    filename = secure_filename(file.filename)
    unique_filename = f"{uuid.uuid4().hex}_{filename}"
    
    docs_dir = os.path.join(current_app.root_path, 'static', 'documents')
    os.makedirs(docs_dir, exist_ok=True)
    
    file.save(os.path.join(docs_dir, unique_filename))
    file_url = f"/api/students/documents/download/{unique_filename}"

    new_doc = StudentDocument(
        student_id=student.id,
        name=name,
        file_path=file_url,
        expiry_date=expiry_date,
        document_type=doc_type,
        status="UPLOADED"
    )
    db.session.add(new_doc)
    db.session.commit()

    doc_dict = new_doc.to_dict()
    doc_dict['student_name'] = f"{student.first_name} {student.last_name}"
    return jsonify(doc_dict), 201

# === Parent Family Overview ===

@parent_bp.route('/family', methods=['GET'])
@jwt_required()
def get_parent_family():
    parent = get_current_parent()
    if not parent:
        return jsonify({"error": "Unauthorized"}), 403

    try:
        from app.models.authorized_pickup_model import AuthorizedPickup
    except Exception:
        AuthorizedPickup = None

    children_details = []
    for child in parent.children:
        pickups_list = []
        if AuthorizedPickup and hasattr(child, 'lead_id') and child.lead_id:
            try:
                raw_pickups = AuthorizedPickup.query.filter_by(lead_id=child.lead_id).all()
                for p in raw_pickups:
                    pickups_list.append({
                        'id': p.id,
                        'name': p.name,
                        'relationship': p.relationship,
                        'phone': getattr(p, 'contact_number', getattr(p, 'phone', 'N/A'))
                    })
            except Exception as e:
                print(f"[FAMILY PICKUP ERROR] {e}")

        children_details.append({
            'id': child.id,
            'student_id_number': child.student_id_number,
            'first_name': child.first_name,
            'last_name': child.last_name,
            'date_of_birth': child.date_of_birth.isoformat() if child.date_of_birth else None,
            'grade_level': child.grade_level,
            'status': child.status,
            'enrollment_date': child.enrollment_date.isoformat() if child.enrollment_date else None,
            'authorized_pickups': pickups_list
        })

    return jsonify({
        'parent': {
            'id': parent.id,
            'first_name': parent.first_name,
            'last_name': parent.last_name,
            'email': parent.email,
            'phone': parent.phone,
            'sign_in_pin': parent.sign_in_pin or "2963"
        },
        'children': children_details
    }), 200

# ==============================================================================
# ADMINISTRATIVE PARENT MANAGEMENT (Super Admin, Administration & IT Dept)
# ==============================================================================

import re
from flask_jwt_extended import create_access_token
from datetime import timedelta
from app import mail
from app.utils.email_otp import send_parent_invite_email

def get_admin_actor():
    claims = get_jwt()
    email = claims.get('sub')
    role = claims.get('role')
    
    if role == 'superadmin':
        from app.models.super_admin_model import SuperAdmin
        return SuperAdmin.query.filter_by(email=email).first()
        
    if role == 'staff':
        from app.models.staff_model import Staff
        staff = Staff.query.filter_by(email=email).first()
        if staff and getattr(staff, 'is_active', True):
            for dept in staff.departments:
                clean = dept.name.strip().lower()
                if re.search(r'\b(it|information technology|info tech|tech|administration|admin)\b', clean):
                    return staff
    return None

@parent_bp.route('/admin/all', methods=['GET'])
@jwt_required()
def admin_get_all_parents():
    actor = get_admin_actor()
    if not actor:
        return jsonify({"error": "Unauthorized. Requires Super Admin, Administration or IT Department access."}), 403

    parents = Parent.query.order_by(Parent.created_at.desc()).all()
    results = []
    for p in parents:
        results.append({
            'id': p.id,
            'first_name': p.first_name,
            'last_name': p.last_name,
            'email': p.email,
            'phone': p.phone,
            'is_active': getattr(p, 'is_active', True),
            'has_password': bool(p.password_hash),
            'sign_in_pin': p.sign_in_pin or "2963",
            'created_at': p.created_at.isoformat() + 'Z' if p.created_at else None,
            'children': [{'id': c.id, 'name': f"{c.first_name} {c.last_name}", 'grade_level': c.grade_level, 'status': c.status} for c in p.children]
        })

    return jsonify(results), 200

@parent_bp.route('/admin/create', methods=['POST'])
@jwt_required()
def admin_create_parent():
    actor = get_admin_actor()
    if not actor:
        return jsonify({"error": "Unauthorized. Requires Super Admin, Administration or IT Department access."}), 403

    from app.utils.sanitizer import sanitize_dict
    data = sanitize_dict(request.get_json() or {})

    first_name = (data.get('first_name') or '').strip()
    last_name = (data.get('last_name') or '').strip()
    email = (data.get('email') or '').strip().lower()
    phone = (data.get('phone') or '').strip()
    password = data.get('password')
    student_ids = data.get('student_ids', [])
    send_invite = data.get('send_invite', True)

    if not first_name or not last_name or not email:
        return jsonify({"error": "First name, last name, and email are required."}), 400

    existing_parent = Parent.query.filter(db.func.lower(Parent.email) == email).first()
    if existing_parent:
        return jsonify({"error": f"A parent account with email '{email}' already exists."}), 409

    new_parent = Parent(
        first_name=first_name,
        last_name=last_name,
        email=email,
        phone=phone or "N/A",
        sign_in_pin=data.get('sign_in_pin') or "2963",
        is_active=bool(data.get('is_active', True))
    )

    if password:
        new_parent.set_password(password)

    # Link selected students
    for s_id in student_ids:
        st = Student.query.get(s_id)
        if st and st not in new_parent.children:
            new_parent.children.append(st)

    db.session.add(new_parent)
    db.session.commit()

    log_activity(actor, f"Created parent account: '{first_name} {last_name}' ({email})", new_parent)

    # Send invitation setup email if no password was supplied or send_invite is true
    if send_invite or not password:
        setup_token = create_access_token(
            identity=email,
            additional_claims={"purpose": "setup-password", "name": f"{first_name} {last_name}", "role": "parent"},
            expires_delta=timedelta(days=7)
        )
        frontend_url = current_app.config.get('FRONTEND_URL', 'http://localhost:5173')
        frontend_url = os.getenv('FRONTEND_URL', frontend_url)
        invite_link = f"{frontend_url}/setup-password?token={setup_token}"

        try:
            send_parent_invite_email(mail, email, f"{first_name} {last_name}", invite_link)
        except Exception as e:
            print(f"[PARENT INVITE ERROR] Failed to send email to {email}: {e}")

    return jsonify(new_parent.to_dict()), 201

@parent_bp.route('/admin/<int:parent_id>', methods=['PUT'])
@jwt_required()
def admin_update_parent(parent_id):
    actor = get_admin_actor()
    if not actor:
        return jsonify({"error": "Unauthorized. Requires Super Admin, Administration or IT Department access."}), 403

    parent = Parent.query.get_or_404(parent_id)
    from app.utils.sanitizer import sanitize_dict
    data = sanitize_dict(request.get_json() or {})

    parent.first_name = data.get('first_name', parent.first_name)
    parent.last_name = data.get('last_name', parent.last_name)
    parent.phone = data.get('phone', parent.phone)
    if 'is_active' in data:
        parent.is_active = bool(data['is_active'])
    if 'sign_in_pin' in data:
        parent.sign_in_pin = data['sign_in_pin']
    if data.get('password'):
        parent.set_password(data['password'])

    if 'student_ids' in data:
        parent.children.clear()
        for s_id in data['student_ids']:
            st = Student.query.get(s_id)
            if st:
                parent.children.append(st)

    log_activity(actor, f"Updated parent account '{parent.first_name} {parent.last_name}'", parent)
    db.session.commit()

    return jsonify(parent.to_dict()), 200

@parent_bp.route('/admin/<int:parent_id>/resend-invite', methods=['POST'])
@jwt_required()
def admin_resend_parent_invite(parent_id):
    actor = get_admin_actor()
    if not actor:
        return jsonify({"error": "Unauthorized. Requires Super Admin, Administration or IT Department access."}), 403

    parent = Parent.query.get_or_404(parent_id)

    setup_token = create_access_token(
        identity=parent.email,
        additional_claims={"purpose": "setup-password", "name": f"{parent.first_name} {parent.last_name}", "role": "parent"},
        expires_delta=timedelta(days=7)
    )
    frontend_url = current_app.config.get('FRONTEND_URL', 'http://localhost:5173')
    frontend_url = os.getenv('FRONTEND_URL', frontend_url)
    invite_link = f"{frontend_url}/setup-password?token={setup_token}"

    try:
        send_parent_invite_email(mail, parent.email, f"{parent.first_name} {parent.last_name}", invite_link)
        log_activity(actor, f"Resent account setup invitation to parent '{parent.email}'", parent)
        return jsonify({"message": f"Invitation link successfully sent to {parent.email}."}), 200
    except Exception as e:
        return jsonify({"error": f"Failed to send invite email: {e}"}), 500

@parent_bp.route('/admin/<int:parent_id>', methods=['DELETE'])
@jwt_required()
def admin_delete_parent(parent_id):
    actor = get_admin_actor()
    if not actor:
        return jsonify({"error": "Unauthorized. Requires Super Admin, Administration or IT Department access."}), 403

    parent = Parent.query.get_or_404(parent_id)
    log_activity(actor, f"Deleted parent account '{parent.first_name} {parent.last_name}' ({parent.email})", parent)

    db.session.delete(parent)
    db.session.commit()
    return jsonify({"message": "Parent account removed successfully."}), 200

