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

# === Stripe Configuration & Payment Methods Endpoints ===

@parent_bp.route('/stripe-config', methods=['GET'])
@jwt_required()
def get_stripe_config():
    """Returns the Stripe Publishable Key securely from backend config."""
    pub_key = current_app.config.get('STRIPE_PUBLISHABLE_KEY') or os.getenv('STRIPE_PUBLISHABLE_KEY')
    return jsonify({
        "publishable_key": pub_key or ""
    }), 200

@parent_bp.route('/create-setup-intent', methods=['POST'])
@jwt_required()
def create_parent_setup_intent():
    """Creates a Stripe SetupIntent for adding a card/bank without sending raw PAN to server."""
    parent = get_current_parent()
    if not parent:
        return jsonify({"error": "Unauthorized"}), 403

    from app.services import stripe_service
    data = request.get_json() or {}
    idempotency_key = request.headers.get('Idempotency-Key') or data.get('idempotency_key') or f"setup_{parent.id}_{uuid.uuid4()}"

    try:
        setup_data = stripe_service.create_setup_intent(parent, idempotency_key=idempotency_key)
        return jsonify(setup_data), 200
    except Exception as e:
        current_app.logger.error(f"Error in create_parent_setup_intent: {e}", exc_info=True)
        return jsonify({"error": "Failed to initialize secure payment setup."}), 500

@parent_bp.route('/payment-methods', methods=['GET'])
@jwt_required()
def get_payment_methods():
    parent = get_current_parent()
    if not parent:
        return jsonify({"error": "Unauthorized"}), 403

    methods = parent.payment_methods.order_by(ParentPaymentMethod.is_default.desc(), ParentPaymentMethod.created_at.desc()).all()
    return jsonify([m.to_dict() for m in methods]), 200

@parent_bp.route('/payment-methods/save-stripe', methods=['POST'])
@jwt_required()
def save_stripe_payment_method():
    """
    Saves a verified Stripe PaymentMethod into the database.
    Zero raw card data is accepted—only the tokenized payment_method_id.
    """
    parent = get_current_parent()
    if not parent:
        return jsonify({"error": "Unauthorized"}), 403

    data = request.get_json() or {}
    stripe_pm_id = data.get('payment_method_id')
    is_default = bool(data.get('is_default', False))

    if not stripe_pm_id:
        return jsonify({"error": "payment_method_id is required."}), 400

    from app.services import stripe_service
    try:
        pm_details = stripe_service.retrieve_payment_method_details(stripe_pm_id)
    except Exception as e:
        current_app.logger.error(f"Failed to retrieve Stripe PaymentMethod {stripe_pm_id}: {e}")
        return jsonify({"error": "Could not verify payment method with Stripe."}), 400

    # If first method or explicitly set, make default
    existing_count = parent.payment_methods.count()
    if existing_count == 0 or is_default:
        is_default = True
        ParentPaymentMethod.query.filter_by(parent_id=parent.id).update({'is_default': False})

    # Check if already exists in DB
    existing_pm = ParentPaymentMethod.query.filter_by(stripe_payment_method_id=stripe_pm_id, parent_id=parent.id).first()
    if existing_pm:
        existing_pm.is_default = is_default
        db.session.commit()
        return jsonify(existing_pm.to_dict()), 200

    new_pm = ParentPaymentMethod(
        parent_id=parent.id,
        method_type=pm_details["type"],
        card_brand=pm_details["card_brand"],
        last4=pm_details["last4"] or "0000",
        exp_month=pm_details["exp_month"],
        exp_year=pm_details["exp_year"],
        bank_name=pm_details["bank_name"],
        account_type="checking",
        account_holder_name=pm_details["account_holder_name"] or f"{parent.first_name} {parent.last_name}",
        is_default=is_default,
        stripe_payment_method_id=stripe_pm_id
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

    # Detach from Stripe
    if pm.stripe_payment_method_id:
        from app.services import stripe_service
        try:
            stripe_service.detach_payment_method(pm.stripe_payment_method_id)
        except Exception as e:
            current_app.logger.warning(f"Failed to detach payment method {pm.stripe_payment_method_id}: {e}")

    db.session.delete(pm)
    db.session.commit()

    # If deleted was default, set next remaining as default
    if was_default:
        next_pm = parent.payment_methods.first()
        if next_pm:
            next_pm.is_default = True
            db.session.commit()

    return jsonify({"message": "Payment method removed successfully."}), 200

# === Stripe Payment Intent & Execution Endpoints ===

@parent_bp.route('/create-payment-intent', methods=['POST'])
@jwt_required()
def create_parent_payment_intent():
    """
    Creates a Stripe PaymentIntent with server-side amount calculation
    and ownership verification. Never accepts arbitrary amounts blindly.
    """
    parent = get_current_parent()
    if not parent:
        return jsonify({"error": "Unauthorized"}), 403

    from app.services import stripe_service
    data = request.get_json() or {}
    student_id = data.get('student_id')
    invoice_id = data.get('invoice_id')
    saved_pm_id = data.get('payment_method_id')
    idempotency_key = request.headers.get('Idempotency-Key') or data.get('idempotency_key') or f"pay_{parent.id}_{uuid.uuid4()}"

    # 1. Resolve Target Student & Verify Ownership
    target_student = None
    if student_id:
        target_student = Student.query.get(student_id)
        if not target_student or parent not in target_student.parents:
            return jsonify({"error": "Selected student is invalid or unauthorized."}), 403
    elif parent.children:
        target_student = parent.children[0]
    else:
        return jsonify({"error": "No student linked to this parent account."}), 400

    account = target_student.financial_account
    if not account:
        account = StudentFinancialAccount(student=target_student)
        db.session.add(account)
        db.session.commit()

    # 2. Server-side amount computation (Never trust client amount)
    charge_amount = 0.0
    if invoice_id:
        inv = Invoice.query.filter_by(id=invoice_id, account_id=account.id).first()
        if not inv:
            return jsonify({"error": "Invoice not found or unauthorized."}), 404
        
        total_paid_inv = db.session.query(func.sum(Payment.amount)).filter_by(invoice_id=inv.id, status='Success').scalar() or 0.0
        remaining_due = max(0.0, inv.total_amount - total_paid_inv)
        if remaining_due <= 0.0:
            return jsonify({"error": "This invoice is already paid in full."}), 400

        # Check requested partial amount if provided
        req_amount = data.get('requested_amount') or data.get('amount')
        if req_amount:
            try:
                req_amount = float(req_amount)
                if 0.0 < req_amount <= remaining_due:
                    charge_amount = req_amount
                else:
                    charge_amount = remaining_due
            except (ValueError, TypeError):
                charge_amount = remaining_due
        else:
            charge_amount = remaining_due
    else:
        # Calculate current total balance from DB
        total_invoiced = db.session.query(func.sum(InvoiceItem.amount)).join(Invoice).filter(Invoice.account_id == account.id).scalar() or 0.0
        total_paid = db.session.query(func.sum(Payment.amount)).filter(Payment.account_id == account.id, Payment.status == 'Success').scalar() or 0.0
        total_credited = db.session.query(func.sum(Credit.amount)).filter(Credit.account_id == account.id).scalar() or 0.0
        current_balance = max(0.0, total_invoiced - (total_paid + total_credited))

        req_amount = data.get('requested_amount') or data.get('amount')
        if req_amount:
            try:
                req_amount = float(req_amount)
                if req_amount > 0:
                    charge_amount = req_amount
                else:
                    charge_amount = current_balance
            except (ValueError, TypeError):
                charge_amount = current_balance
        else:
            charge_amount = current_balance

    if charge_amount <= 0.0:
        return jsonify({"error": "No outstanding balance due to charge."}), 400

    amount_cents = int(round(charge_amount * 100))

    # 3. Check for existing payment under this idempotency key
    existing_payment = Payment.query.filter_by(idempotency_key=idempotency_key).first()
    if existing_payment and existing_payment.status == 'Success':
        return jsonify({
            "message": "Payment already processed.",
            "payment": existing_payment.to_dict(),
            "status": "succeeded"
        }), 200

    # 4. Resolve Stripe Customer & Payment Method
    customer_id = stripe_service.get_or_create_stripe_customer(parent)
    stripe_pm_id = None
    if saved_pm_id:
        pm_record = ParentPaymentMethod.query.filter_by(id=saved_pm_id, parent_id=parent.id).first()
        if pm_record:
            stripe_pm_id = pm_record.stripe_payment_method_id

    # 5. Create Stripe PaymentIntent
    metadata = {
        "parent_id": str(parent.id),
        "student_id": str(target_student.id),
        "invoice_id": str(invoice_id) if invoice_id else "",
        "idempotency_key": idempotency_key
    }

    try:
        intent = stripe_service.create_payment_intent(
            amount_in_cents=amount_cents,
            customer_id=customer_id,
            payment_method_id=stripe_pm_id,
            description=f"Payment for {target_student.first_name} {target_student.last_name}",
            metadata=metadata,
            idempotency_key=idempotency_key,
            confirm=bool(stripe_pm_id)
        )

        if isinstance(intent, dict) and intent.get("error"):
            return jsonify({
                "error": intent.get("message", "Payment processing failed."),
                "code": intent.get("code")
            }), 400

        # If payment succeeded immediately (e.g. saved card)
        if intent.status == 'succeeded':
            payment_record = Payment(
                account_id=account.id,
                invoice_id=invoice_id,
                amount=charge_amount,
                method=f"Stripe Card (*{pm_record.last4})" if pm_record else "Stripe Online Payment",
                notes=f"Stripe PaymentIntent {intent.id}",
                status='Success',
                stripe_payment_intent_id=intent.id,
                idempotency_key=idempotency_key,
                transaction_date=datetime.utcnow()
            )
            db.session.add(payment_record)

            if invoice_id:
                inv = Invoice.query.get(invoice_id)
                if inv:
                    total_p = db.session.query(func.sum(Payment.amount)).filter_by(invoice_id=inv.id, status='Success').scalar() or 0.0
                    if (total_p + charge_amount) >= inv.total_amount:
                        inv.status = 'Paid'

            try:
                log = FinancialAuditLog(
                    account_id=account.id,
                    transaction_type='Payment',
                    transaction_id=str(intent.id),
                    action='Receive',
                    amount=charge_amount,
                    status='Success',
                    actor_name=f"{parent.first_name} {parent.last_name} (Parent)",
                    description=f"Online payment of ${charge_amount:.2f} processed via Stripe"
                )
                db.session.add(log)
            except Exception as e:
                current_app.logger.warning(f"Financial audit log error: {e}")

            db.session.commit()
            return jsonify({
                "status": "succeeded",
                "payment_intent_id": intent.id,
                "amount": charge_amount,
                "message": "Payment succeeded!"
            }), 200

        # If requires client confirmation / 3D Secure / Elements
        return jsonify({
            "status": intent.status,
            "client_secret": intent.client_secret,
            "payment_intent_id": intent.id,
            "amount": charge_amount
        }), 200

    except Exception as e:
        current_app.logger.error(f"PaymentIntent creation failed: {e}", exc_info=True)
        return jsonify({"error": "Unable to initialize Stripe payment. Please try again."}), 500

@parent_bp.route('/pay', methods=['POST'])
@jwt_required()
def confirm_and_reconcile_payment():
    """
    Confirms a completed Stripe PaymentIntent, reconciles invoices,
    and records the transaction in the ledger.
    """
    parent = get_current_parent()
    if not parent:
        return jsonify({"error": "Unauthorized"}), 403

    try:
        import stripe
        from app.services import stripe_service
        data = request.get_json() or {}
        payment_intent_id = data.get('payment_intent_id')
        idempotency_key = request.headers.get('Idempotency-Key') or data.get('idempotency_key') or payment_intent_id

        if not payment_intent_id:
            return jsonify({"error": "payment_intent_id is required."}), 400

        # 1. Retrieve PaymentIntent from Stripe
        stripe.api_key = current_app.config.get('STRIPE_SECRET_KEY') or os.getenv('STRIPE_SECRET_KEY')
        try:
            pi = stripe.PaymentIntent.retrieve(payment_intent_id)
        except Exception as e:
            current_app.logger.error(f"Error retrieving PaymentIntent {payment_intent_id}: {e}")
            return jsonify({"error": "Could not verify payment status with Stripe."}), 400

        if pi.status != 'succeeded':
            return jsonify({
                "error": f"Payment is not confirmed. Status: {pi.status}",
                "status": pi.status
            }), 400

        amount = float(getattr(pi, 'amount_received', None) or getattr(pi, 'amount', 0)) / 100.0
        
        pi_dict = pi.to_dict() if hasattr(pi, 'to_dict') else (dict(pi) if isinstance(pi, (dict, list)) else {})
        metadata = pi_dict.get('metadata') if isinstance(pi_dict, dict) else {}
        if not metadata and hasattr(pi, 'metadata'):
            metadata = pi.metadata.to_dict() if hasattr(pi.metadata, 'to_dict') else dict(pi.metadata)

        raw_invoice_id = metadata.get('invoice_id') if isinstance(metadata, dict) else data.get('invoice_id')
        raw_student_id = metadata.get('student_id') if isinstance(metadata, dict) else data.get('student_id')

        safe_invoice_id = None
        if raw_invoice_id and str(raw_invoice_id).strip().isdigit():
            safe_invoice_id = int(str(raw_invoice_id).strip())

        safe_student_id = None
        if raw_student_id and str(raw_student_id).strip().isdigit():
            safe_student_id = int(str(raw_student_id).strip())

        # Resolve Student & Account
        target_student = None
        if safe_student_id:
            target_student = Student.query.get(safe_student_id)
        elif parent.children:
            target_student = parent.children[0]

        if not target_student:
            return jsonify({"error": "Student account not found."}), 400

        account = target_student.financial_account
        if not account:
            account = StudentFinancialAccount(student=target_student)
            db.session.add(account)
            db.session.commit()

        # Check for existing Payment row to prevent duplicate insertion
        existing_payment = Payment.query.filter_by(stripe_payment_intent_id=pi.id).first()
        if existing_payment:
            return jsonify({
                "message": "Payment verified and recorded.",
                "payment": existing_payment.to_dict(),
                "status": "succeeded"
            }), 200

        # Create Payment record
        new_payment = Payment(
            account_id=account.id,
            invoice_id=safe_invoice_id,
            amount=amount,
            method="Stripe Online Payment",
            notes=f"Stripe PaymentIntent {pi.id}",
            status='Success',
            stripe_payment_intent_id=pi.id,
            idempotency_key=idempotency_key,
            transaction_date=datetime.utcnow()
        )
        db.session.add(new_payment)

        # Reconcile Invoice status
        if safe_invoice_id:
            inv = Invoice.query.filter_by(id=safe_invoice_id, account_id=account.id).first()
            if inv:
                total_paid = db.session.query(func.sum(Payment.amount)).filter_by(invoice_id=inv.id, status='Success').scalar() or 0.0
                if (total_paid + amount) >= inv.total_amount:
                    inv.status = 'Paid'

        # Financial Audit Log
        try:
            log = FinancialAuditLog(
                account_id=account.id,
                transaction_type='Payment',
                transaction_id=str(pi.id),
                action='Receive',
                amount=amount,
                status='Success',
                actor_name=f"{parent.first_name} {parent.last_name} (Parent)",
                description=f"Parent Portal payment of ${amount:.2f} confirmed via Stripe"
            )
            db.session.add(log)
        except Exception as e:
            current_app.logger.warning(f"Financial audit log error: {e}")

        # Activity Log
        try:
            log_activity(parent, f"Paid ${amount:.2f} via Stripe for {target_student.first_name} {target_student.last_name}", new_payment)
        except Exception as e:
            current_app.logger.warning(f"log_activity error: {e}")

        db.session.commit()

        return jsonify({
            "message": "Payment confirmed and recorded successfully!",
            "payment": new_payment.to_dict()
        }), 201

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Fatal error in confirm_and_reconcile_payment: {e}", exc_info=True)
        return jsonify({"error": f"Failed to record payment: {str(e)}"}), 500

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

