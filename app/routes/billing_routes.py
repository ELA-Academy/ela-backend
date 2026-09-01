from flask import Blueprint, jsonify, request, send_file
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

    def parse_iso_date(dt_str):
        if not dt_str:
            return None
        dt_str = dt_str.replace('Z', '')
        for fmt in ('%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d'):
            try:
                return datetime.strptime(dt_str, fmt).date()
            except ValueError:
                pass
        return date.today()

    start_date = parse_iso_date(plan_data.get('start_date')) or date.today()
    end_date = parse_iso_date(plan_data.get('end_date'))
    cycle = plan_data.get('cycle', 'Monthly')
    cycle_clean = cycle.lower().replace('-', '').replace(' ', '')
    
    for student_id in student_ids:
        student = Student.query.get(student_id)
        if not student or not student.financial_account: continue

        # Calculate the first invoice date based on cycle
        invoice_day = int(plan_data.get('invoice_generation_day', 1))
        if cycle_clean == 'weekly':
            next_invoice_date = start_date + relativedelta(weeks=1)
        elif cycle_clean == 'biweekly':
            next_invoice_date = start_date + relativedelta(weeks=2)
        elif cycle_clean == 'quarterly':
            try:
                next_invoice_date = start_date.replace(day=invoice_day)
            except ValueError:
                next_invoice_date = start_date
            if start_date.day > invoice_day:
                next_invoice_date += relativedelta(months=3)
        else: # Monthly
            try:
                next_invoice_date = start_date.replace(day=invoice_day)
            except ValueError:
                next_invoice_date = start_date
            if start_date.day > invoice_day:
                next_invoice_date += relativedelta(months=1)

        sub = Subscription(
            account_id=student.financial_account.id,
            plan_name=plan_data.get('plan_name', 'Tuition Plan'),
            cycle=cycle,
            start_date=start_date,
            end_date=end_date,
            invoice_generation_day=invoice_day,
            due_day=int(plan_data.get('due_day', 15)),
            next_invoice_date=next_invoice_date,
            items_json=plan_data.get('items_json', [])
        )
        db.session.add(sub)
    
    log_activity(actor, f"Created recurring plan '{plan_data.get('plan_name')}' ({cycle}) for {len(student_ids)} student(s)")
    db.session.commit()
    return jsonify({"message": "Recurring plans created successfully."}), 201


def log_financial_event(account_id, transaction_type, transaction_id, action, amount, status, actor_name, description):
    try:
        from app.models.financial_model import FinancialAuditLog
        log = FinancialAuditLog(
            account_id=account_id,
            transaction_type=transaction_type,
            transaction_id=str(transaction_id) if transaction_id else None,
            action=action,
            amount=float(amount),
            status=status,
            actor_name=actor_name,
            description=description
        )
        db.session.add(log)
    except Exception as e:
        print(f"[FinancialAuditLog] Error logging event: {e}")


@billing_bp.route('/accounts/<int:student_id>/invoices', methods=['POST'])
@jwt_required()
def create_invoice(student_id):
    try:
        actor = get_actor()
        student = Student.query.get_or_404(student_id)
        account = student.financial_account
        if not account:
            return jsonify({"error": "Financial account not found for this student."}), 404
            
        data = request.get_json() or {}
        items = data.get('items', [])
        if not items:
            return jsonify({"error": "Invoice must have at least one item."}), 400

        due_date_val = None
        if data.get('due_date'):
            try:
                due_date_val = datetime.strptime(data['due_date'], '%Y-%m-%d').date()
            except Exception:
                try:
                    due_date_val = datetime.fromisoformat(str(data['due_date']).replace('Z', '')).date()
                except Exception:
                    due_date_val = date.today()

        new_invoice = Invoice(
            account_id=account.id,
            status=data.get('status', 'Draft'),
            due_date=due_date_val
        )
        
        for item in items:
            desc = (item.get('description') or 'Invoice Item').strip()
            amt = float(item.get('amount') or 0.0)
            subsidy_id = item.get('subsidy_id')

            new_item = InvoiceItem(
                description=desc, 
                amount=amt,
                subsidy_id=subsidy_id
            )
            if subsidy_id:
                subsidy_invoice_transaction = SubsidyTransaction(
                    subsidy_id=subsidy_id,
                    transaction_type='Invoice',
                    amount=-amt,
                    transaction_date=new_invoice.due_date or date.today(),
                    notes=f"Applied to invoice for {student.first_name} {student.last_name}"
                )
                db.session.add(subsidy_invoice_transaction)

            new_invoice.items.append(new_item)
        
        db.session.add(new_invoice)
        db.session.flush() # Ensure new_invoice.id is assigned before logging

        log_activity(actor, f"Created invoice for {student.first_name} {student.last_name}", new_invoice)
        actor_name = f"{actor.first_name} {actor.last_name}" if actor else "System"
        log_financial_event(
            account.id, 'Invoice', new_invoice.id, 'Create', new_invoice.total_amount, 
            'Success' if new_invoice.status == 'Sent' else 'Pending', actor_name, 
            f"Invoice created with status {new_invoice.status}"
        )
        db.session.commit()

        # Send email notification to parent if invoice status is 'Sent'
        if new_invoice.status == 'Sent' and student.parents:
            parent_emails = [p.email for p in student.parents if p.email]
            if parent_emails:
                try:
                    from app.utils.notifications import send_email_in_background
                    send_email_in_background(
                        subject=f"New Invoice Billed - Exceptional Learning and Arts Academy",
                        recipients=parent_emails,
                        template_data={
                            "message": f"Hello,\n\nA new invoice of ${new_invoice.total_amount:.2f} due on {new_invoice.due_date} has been posted for {student.first_name} {student.last_name}.\n\nYou can review and pay your invoice anytime on your Parent Dashboard."
                        }
                    )
                except Exception as ex:
                    print(f"Failed to send parent invoice email: {ex}")

        return jsonify(new_invoice.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        print(f"[create_invoice ERROR] {e}")
        return jsonify({"error": f"Failed to save invoice: {str(e)}"}), 500

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
    actor_name = f"{actor.first_name} {actor.last_name}" if actor else "System"
    log_financial_event(
        account.id, 'Payment', new_payment.id, 'Receive', new_payment.amount, 
        new_payment.status, actor_name, f"Payment recorded via {new_payment.method}"
    )
    db.session.commit()
    return jsonify(new_payment.to_dict()), 201

@billing_bp.route('/accounts/<int:student_id>/charge-saved-card', methods=['POST'])
@jwt_required()
def charge_saved_card(student_id):
    """
    Charges a student's parent's saved Stripe payment method off-session.
    Explicitly handles 3DS authentication_required without silent failure.
    """
    import uuid
    from app.services import stripe_service
    from app.models.student_model import ParentPaymentMethod
    from app.models.notification_model import Notification

    actor = get_actor()
    student = Student.query.get_or_404(student_id)
    account = student.financial_account
    if not account:
        return jsonify({"error": "Financial account not found."}), 404

    data = request.get_json() or {}
    invoice_id = data.get('invoice_id')
    payment_method_id = data.get('payment_method_id')
    idempotency_key = request.headers.get('Idempotency-Key') or data.get('idempotency_key') or f"admin_charge_{student.id}_{uuid.uuid4()}"

    # Verify Invoice
    inv = None
    if invoice_id:
        inv = Invoice.query.filter_by(id=invoice_id, account_id=account.id).first()
        if not inv:
            return jsonify({"error": "Invoice not found."}), 404
        total_paid_inv = db.session.query(func.sum(Payment.amount)).filter_by(invoice_id=inv.id, status='Success').scalar() or 0.0
        remaining_due = max(0.0, inv.total_amount - total_paid_inv)
        if remaining_due <= 0:
            return jsonify({"error": "Invoice is already paid in full."}), 400
        charge_amount = remaining_due
    else:
        charge_amount = float(data.get('amount', 0))
        if charge_amount <= 0:
            return jsonify({"error": "Invalid charge amount."}), 400

    # Resolve parent & payment method
    parent = None
    pm_record = None
    if payment_method_id:
        pm_record = ParentPaymentMethod.query.get(payment_method_id)
        if pm_record:
            parent = pm_record.parent
    elif student.parents:
        parent = student.parents[0]
        pm_record = parent.payment_methods.filter_by(is_default=True).first() or parent.payment_methods.first()

    if not parent or not pm_record or not pm_record.stripe_payment_method_id:
        return jsonify({"error": "No saved Stripe payment method found on file for this student."}), 400

    amount_cents = int(round(charge_amount * 100))
    customer_id = stripe_service.get_or_create_stripe_customer(parent)

    metadata = {
        "student_id": str(student.id),
        "invoice_id": str(invoice_id) if invoice_id else "",
        "parent_id": str(parent.id),
        "initiated_by_staff": str(actor.id if actor else "admin")
    }

    try:
        intent = stripe_service.create_payment_intent(
            amount_in_cents=amount_cents,
            customer_id=customer_id,
            payment_method_id=pm_record.stripe_payment_method_id,
            description=f"Tuition charge for {student.first_name} {student.last_name}",
            metadata=metadata,
            idempotency_key=idempotency_key,
            off_session=True,
            confirm=True
        )

        # Handle CardError / authentication_required
        if isinstance(intent, dict) and intent.get("error"):
            if intent.get("code") == "authentication_required" or intent.get("decline_code") == "authentication_required":
                if inv:
                    inv.status = 'Action Required'
                # Notify parent to complete 3DS
                try:
                    notif = Notification(
                        recipient_role="parent",
                        recipient_id=parent.id,
                        title="Payment Action Required",
                        message=f"A charge of ${charge_amount:.2f} for {student.first_name} requires your card confirmation. Please log into the Parent Portal to complete.",
                        notification_type="payment_action_required"
                    )
                    db.session.add(notif)
                except Exception as ne:
                    print(f"Failed to create notification: {ne}")

                db.session.commit()
                return jsonify({
                    "status": "requires_action",
                    "error": "Card requires 3D Secure / customer authentication. Parent has been notified to complete payment in portal."
                }), 402

            return jsonify({"error": intent.get("message", "Charge declined by bank.")}), 400

        if intent.status == 'succeeded':
            new_payment = Payment(
                account_id=account.id,
                invoice_id=invoice_id,
                amount=charge_amount,
                method=f"Stripe Card (*{pm_record.last4})",
                notes=f"Admin off-session charge (PaymentIntent {intent.id})",
                status='Success',
                stripe_payment_intent_id=intent.id,
                idempotency_key=idempotency_key,
                transaction_date=datetime.utcnow()
            )
            db.session.add(new_payment)

            if inv and (total_paid_inv + charge_amount) >= inv.total_amount:
                inv.status = 'Paid'

            actor_name = f"{actor.first_name} {actor.last_name}" if actor else "Staff"
            log_financial_event(
                account.id, 'Payment', new_payment.id, 'Receive', charge_amount,
                'Success', actor_name, f"Charged saved card (*{pm_record.last4}) via Stripe"
            )
            log_activity(actor, f"Charged parent card of ${charge_amount:.2f} for {student.first_name} {student.last_name}", new_payment)
            db.session.commit()

            return jsonify({
                "message": "Payment charged successfully!",
                "payment": new_payment.to_dict()
            }), 201

        return jsonify({"status": intent.status, "message": "Payment in progress."}), 200

    except Exception as e:
        current_app.logger.error(f"Error charging saved card: {e}", exc_info=True)
        return jsonify({"error": "Failed to process charge with Stripe."}), 500

@billing_bp.route('/payments/<int:payment_id>/refund', methods=['POST'])
@jwt_required()
def refund_payment_direct(payment_id):
    """
    Issues a refund on a Payment record via Stripe with idempotency.
    Guards against double-refunds.
    """
    import uuid
    from app.services import stripe_service

    actor = get_actor()
    payment = Payment.query.get_or_404(payment_id)

    if payment.is_refunded or payment.status == 'Refunded':
        return jsonify({"error": "This payment has already been refunded."}), 400

    data = request.get_json() or {}
    refund_amount = float(data.get('amount') or payment.amount)
    reason = data.get('reason', 'requested_by_customer')
    idempotency_key = request.headers.get('Idempotency-Key') or data.get('idempotency_key') or f"refund_{payment.id}_{uuid.uuid4()}"

    if refund_amount <= 0 or refund_amount > payment.amount:
        return jsonify({"error": f"Invalid refund amount. Maximum refundable is ${payment.amount:.2f}"}), 400

    # If Stripe payment, execute refund with Stripe
    if payment.stripe_payment_intent_id:
        amount_cents = int(round(refund_amount * 100))
        try:
            stripe_service.create_refund(
                charge_id_or_payment_intent_id=payment.stripe_payment_intent_id,
                amount_in_cents=amount_cents,
                reason=reason,
                idempotency_key=idempotency_key
            )
        except Exception as e:
            current_app.logger.error(f"Stripe Refund API error: {e}")
            return jsonify({"error": "Stripe refund processing failed."}), 500

    payment.is_refunded = True
    payment.refund_amount = (payment.refund_amount or 0.0) + refund_amount
    if payment.refund_amount >= payment.amount:
        payment.status = 'Refunded'

    actor_name = f"{actor.first_name} {actor.last_name}" if actor else "Staff"
    log_financial_event(
        payment.account_id, 'Refund', payment.id, 'Refund', refund_amount,
        'Success', actor_name, f"Refund of ${refund_amount:.2f} issued. Reason: {reason}"
    )
    log_activity(actor, f"Issued refund of ${refund_amount:.2f} for Payment #{payment.id}", payment)
    db.session.commit()

    return jsonify({
        "message": f"Refund of ${refund_amount:.2f} processed successfully.",
        "payment": payment.to_dict()
    }), 200

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
    actor_name = f"{actor.first_name} {actor.last_name}" if actor else "System"
    log_financial_event(
        account.id, 'Credit', new_credit.id, 'Create', new_credit.amount, 
        'Success', actor_name, f"Credit added: {new_credit.reason}"
    )
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
        total_paid = db.session.query(func.sum(Payment.amount)).filter(Payment.account_id == account.id, Payment.status == 'Success').scalar() or 0
        total_credited = db.session.query(func.sum(Credit.amount)).filter(Credit.account_id == account.id).scalar() or 0
        balance = total_invoiced - (total_paid + total_credited)
        last_invoice = Invoice.query.filter_by(account_id=account.id).order_by(Invoice.created_at.desc()).first()
        last_payment = Payment.query.filter_by(account_id=account.id, status='Success').order_by(Payment.transaction_date.desc()).first()
        
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
        if tx_item['type'] == 'Invoice': 
            transactions.append({
                'id': obj.id,
                'type': 'Invoice', 
                'date': obj.created_at.isoformat() + 'Z', 
                'due_date': obj.due_date.isoformat() if obj.due_date else None,
                'description': ", ".join([i.description for i in obj.items]), 
                'amount': obj.total_amount, 
                'status': obj.status, 
                'balance': tx_item['balance']
            })
        elif tx_item['type'] == 'Payment': 
            transactions.append({
                'id': obj.id,
                'type': 'Payment', 
                'date': obj.transaction_date.isoformat() + 'Z', 
                'description': f"Payment via {obj.method}" + (f" - {obj.notes}" if obj.notes else ""), 
                'amount': -obj.amount, 
                'status': obj.status, 
                'method': obj.method,
                'notes': obj.notes,
                'balance': tx_item['balance']
            })
        elif tx_item['type'] == 'Credit': 
            transactions.append({
                'id': obj.id,
                'type': 'Credit', 
                'date': obj.created_at.isoformat() + 'Z', 
                'description': obj.reason, 
                'amount': -obj.amount, 
                'status': 'Applied', 
                'balance': tx_item['balance']
            })

    total_invoiced = sum(inv.total_amount for inv in invoices)
    total_paid = sum(p.amount for p in payments)
    total_credited = sum(c.amount for c in credits)
    final_balance = total_invoiced - (total_paid + total_credited)

    summary = {"paid": total_paid, "credited": total_credited, "unpaid": final_balance}
    return jsonify({
        "transactions": transactions,
        "summary": summary,
        "student_name": f"{student.first_name} {student.last_name}",
        "parent_names": [f"{p.first_name} {p.last_name}" for p in student.parents],
        "parents": [p.to_dict() for p in student.parents],
        "grade_level": student.grade_level,
        "home_room": student.home_room if hasattr(student, 'home_room') else f"Home Room {student.grade_level}"
    }), 200

@billing_bp.route('/accounts/<int:student_id>/statement', methods=['GET'])
@jwt_required()
def get_student_statement(student_id):
    student = Student.query.get_or_404(student_id)
    account = student.financial_account
    if not account:
        return jsonify({"error": "No financial account found."}), 404
        
    start_str = request.args.get('start_date')
    end_str = request.args.get('end_date')
    
    if not start_str or not end_str:
        return jsonify({"error": "start_date and end_date are required parameters."}), 400
        
    try:
        start_date = datetime.strptime(start_str, '%Y-%m-%d')
        end_date = datetime.strptime(end_str, '%Y-%m-%d')
    except ValueError:
        return jsonify({"error": "Invalid date format. Use YYYY-MM-DD."}), 400
        
    invoices = Invoice.query.filter_by(account_id=account.id).all()
    payments = Payment.query.filter_by(account_id=account.id).all()
    credits = Credit.query.filter_by(account_id=account.id).all()
    
    all_tx = []
    for inv in invoices:
        all_tx.append({'type': 'Invoice', 'date': inv.created_at, 'amount': inv.total_amount, 'description': ", ".join([i.description for i in inv.items])})
    for p in payments:
        all_tx.append({'type': 'Payment', 'date': p.transaction_date, 'amount': -p.amount, 'description': f"Payment via {p.method}" + (f" - {p.notes}" if p.notes else "")})
    for c in credits:
        all_tx.append({'type': 'Credit', 'date': c.created_at, 'amount': -c.amount, 'description': c.reason})
        
    all_tx.sort(key=lambda x: x['date'])
    
    starting_balance = 0.0
    filtered_tx = []
    summary = {"invoiced": 0.0, "paid": 0.0, "credited": 0.0}
    
    for tx in all_tx:
        tx_date = tx['date']
        if tx_date.date() < start_date.date():
            starting_balance += tx['amount']
        elif start_date.date() <= tx_date.date() <= end_date.date():
            filtered_tx.append({
                'type': tx['type'],
                'date': tx_date.isoformat() + 'Z',
                'description': tx['description'],
                'amount': tx['amount']
            })
            if tx['type'] == 'Invoice':
                summary['invoiced'] += tx['amount']
            elif tx['type'] == 'Payment':
                summary['paid'] += abs(tx['amount'])
            elif tx['type'] == 'Credit':
                summary['credited'] += abs(tx['amount'])
                
    filtered_tx.sort(key=lambda x: x['date'], reverse=True)
    
    from app.utils.statement_generator import generate_statement_pdf
    pdf_buffer = generate_statement_pdf(student, filtered_tx, start_date, end_date, starting_balance, summary)
    
    filename = f"statement_{student.last_name}_{student.first_name}.pdf"
    return send_file(
        pdf_buffer,
        as_attachment=True,
        download_name=filename,
        mimetype='application/pdf'
    )

@billing_bp.route('/accounts/<int:student_id>/payments/<int:payment_id>/receipt', methods=['GET'])
@jwt_required()
def get_payment_receipt(student_id, payment_id):
    student = Student.query.get_or_404(student_id)
    account = student.financial_account
    if not account:
        return jsonify({"error": "No financial account found."}), 404
        
    payment = Payment.query.filter_by(id=payment_id, account_id=account.id).first_or_404()
    
    from app.utils.statement_generator import generate_receipt_pdf
    pdf_buffer = generate_receipt_pdf(student, payment)
    
    filename = f"receipt_{payment_id}.pdf"
    return send_file(
        pdf_buffer,
        as_attachment=True,
        download_name=filename,
        mimetype='application/pdf'
    )

@billing_bp.route('/accounts/<int:student_id>/payments/<int:payment_id>/refund', methods=['POST'])
@jwt_required()
def refund_payment(student_id, payment_id):
    actor = get_actor()
    student = Student.query.get_or_404(student_id)
    account = student.financial_account
    if not account:
        return jsonify({"error": "Financial account not found."}), 404
        
    payment = Payment.query.filter_by(id=payment_id, account_id=account.id).first_or_404()
    
    data = request.get_json()
    amount = data.get('amount')
    description = data.get('description', '')
    staff_note = data.get('staff_note', '')
    
    if not amount or float(amount) <= 0:
        return jsonify({"error": "Invalid refund amount."}), 400
        
    refund_amount = float(amount)
    
    # Calculate previously refunded amount
    refunds = Payment.query.filter_by(account_id=account.id, invoice_id=payment.invoice_id, method='Refund').all()
    total_refunded = sum(abs(r.amount) for r in refunds if r.notes and f"Refund for Payment #{payment.id}" in r.notes)
    
    max_refund = payment.amount - total_refunded
    if refund_amount > max_refund:
        return jsonify({"error": f"Refund amount exceeds the maximum refundable amount of ${max_refund:.2f}."}), 400
        
    refund_payment = Payment(
        account_id=account.id,
        invoice_id=payment.invoice_id,
        amount=-refund_amount, # Negative amount adds to balance
        method='Refund',
        notes=f"Refund for Payment #{payment.id}. Note: {description}" + (f" | Staff Note: {staff_note}" if staff_note else ""),
        status='Success',
        transaction_date=datetime.utcnow()
    )
    
    if payment.invoice_id:
        invoice = Invoice.query.get(payment.invoice_id)
        if invoice and invoice.status == 'Paid':
            invoice.status = 'Sent'
            
    db.session.add(refund_payment)
    log_activity(actor, f"Issued refund of ${refund_amount} for payment #{payment.id} for {student.first_name} {student.last_name}", refund_payment)
    actor_name = f"{actor.first_name} {actor.last_name}" if actor else "System"
    log_financial_event(
        account.id, 'Refund', refund_payment.id, 'Refund', refund_payment.amount, 
        'Success', actor_name, f"Refund issued for payment #{payment.id}. Note: {description}"
    )
    db.session.commit()
    return jsonify(refund_payment.to_dict()), 201

@billing_bp.route('/invoices/<int:invoice_id>', methods=['PUT'])
@jwt_required()
def edit_invoice(invoice_id):
    actor = get_actor()
    invoice = Invoice.query.get_or_404(invoice_id)
    account = invoice.account
    if not account:
        return jsonify({"error": "Invoice financial account not found."}), 404
        
    student = account.student
    data = request.get_json()
    
    # Update due date
    due_date_str = data.get('due_date')
    if due_date_str:
        invoice.due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date()
        
    # Update items list
    items_data = data.get('items', [])
    if items_data:
        # Delete old items
        for item in invoice.items:
            db.session.delete(item)
        invoice.items = []
        
        # Delete old subsidy transactions for this student invoice to prevent duplicates
        old_subsidy_txs = SubsidyTransaction.query.filter(
            SubsidyTransaction.transaction_type == 'Invoice',
            SubsidyTransaction.notes.like(f"%Applied to invoice for {student.first_name} {student.last_name}%")
        ).all()
        for s_tx in old_subsidy_txs:
            db.session.delete(s_tx)
            
        # Add new items
        for item in items_data:
            new_item = InvoiceItem(
                description=item['description'],
                amount=item['amount'],
                subsidy_id=item.get('subsidy_id')
            )
            if item.get('subsidy_id'):
                subsidy_invoice_transaction = SubsidyTransaction(
                    subsidy_id=item.get('subsidy_id'),
                    transaction_type='Invoice',
                    amount=-item.get('amount'),
                    transaction_date=invoice.due_date or date.today(),
                    notes=f"Applied to invoice for {student.first_name} {student.last_name}"
                )
                db.session.add(subsidy_invoice_transaction)
            invoice.items.append(new_item)
            
    actor_name = f"{actor.first_name} {actor.last_name}" if actor else "System"
    log_financial_event(
        account.id, 'Invoice', invoice.id, 'Update', invoice.total_amount, 
        'Success' if invoice.status == 'Sent' else 'Pending', actor_name, 
        f"Invoice updated. New Total: ${invoice.total_amount:.2f}"
    )
    db.session.commit()
    return jsonify(invoice.to_dict()), 200

@billing_bp.route('/invoices/<int:invoice_id>/cancel', methods=['POST'])
@jwt_required()
def cancel_invoice(invoice_id):
    actor = get_actor()
    invoice = Invoice.query.get_or_404(invoice_id)
    account = invoice.account
    if not account:
        return jsonify({"error": "Invoice financial account not found."}), 404
        
    invoice.status = 'Void'
    
    actor_name = f"{actor.first_name} {actor.last_name}" if actor else "System"
    log_financial_event(
        account.id, 'Invoice', invoice.id, 'Void', invoice.total_amount, 
        'Voided', actor_name, "Invoice canceled/voided."
    )
    db.session.commit()
    return jsonify(invoice.to_dict()), 200

@billing_bp.route('/invoices/<int:invoice_id>/send', methods=['POST'])
@jwt_required()
def send_invoice(invoice_id):
    actor = get_actor()
    invoice = Invoice.query.get_or_404(invoice_id)
    account = invoice.account
    if not account:
        return jsonify({"error": "Invoice financial account not found."}), 404
        
    if invoice.status == 'Draft':
        invoice.status = 'Sent'
        
    actor_name = f"{actor.first_name} {actor.last_name}" if actor else "System"
    log_financial_event(
        account.id, 'Invoice', invoice.id, 'Send', invoice.total_amount, 
        'Success', actor_name, "Invoice sent to parent."
    )
    db.session.commit()
    return jsonify(invoice.to_dict()), 200

@billing_bp.route('/accounts/<int:student_id>/audit-logs', methods=['GET'])
@jwt_required()
def get_financial_audit_logs(student_id):
    student = Student.query.get_or_404(student_id)
    account = student.financial_account
    if not account:
        return jsonify([]), 200
        
    logs = account.audit_logs.order_by(FinancialAuditLog.created_at.desc()).all()
    return jsonify([l.to_dict() for l in logs]), 200


@billing_bp.route('/transactions', methods=['GET'])
@jwt_required()
def get_all_transactions():
    try:
        search = request.args.get('search', '').strip().lower()
        enrollment_status = request.args.get('enrollment_status', '').strip().lower()
        transaction_type = request.args.get('transaction_type', '').strip().lower()
        payment_mode = request.args.get('payment_mode', '').strip().lower()
        payment_status = request.args.get('payment_status', '').strip().lower()
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 30))
        
        # Calculate summary metrics (always calculated across the whole database)
        total_paid_metric = db.session.query(func.sum(Payment.amount)).filter(Payment.status == 'Success').scalar() or 0
        total_in_process_metric = db.session.query(func.sum(Payment.amount)).filter(Payment.status == 'In Process').scalar() or 0
        
        # Calculate total unpaid (open balance of all students)
        total_unpaid_metric = 0
        all_accounts = StudentFinancialAccount.query.all()
        for acc in all_accounts:
            total_invoiced = db.session.query(func.sum(InvoiceItem.amount)).join(Invoice).filter(Invoice.account_id == acc.id).scalar() or 0
            total_p = db.session.query(func.sum(Payment.amount)).filter(Payment.account_id == acc.id).scalar() or 0
            total_c = db.session.query(func.sum(Credit.amount)).filter(Credit.account_id == acc.id).scalar() or 0
            bal = total_invoiced - (total_p + total_c)
            if bal > 0:
                total_unpaid_metric += bal

        # Fetch and unify transactions
        invoices = Invoice.query.all()
        payments = Payment.query.all()
        credits = Credit.query.all()
        
        all_tx = []
        
        for inv in invoices:
            student = inv.account.student if inv.account else None
            if not student: continue
            
            student_name = f"{student.first_name} {student.last_name}"
            grade = student.grade_level
            
            if search and search not in student_name.lower():
                continue
                
            if enrollment_status and enrollment_status != 'all' and enrollment_status != 'all status':
                mapped_status = student.status.lower()
                if enrollment_status == 'graduate' and mapped_status == 'graduated':
                    pass
                elif enrollment_status == 'in active' and mapped_status == 'inactive':
                    pass
                else:
                    if mapped_status != enrollment_status:
                        continue
            
            if transaction_type and transaction_type != 'all transaction types' and transaction_type != 'invoice':
                continue
                
            if payment_mode and payment_mode != 'all payment modes':
                continue
                
            if payment_status:
                mapped_invoice_status = 'in process'
                if inv.status.lower() == 'paid':
                    mapped_invoice_status = 'successful'
                elif inv.status.lower() == 'overdue' or inv.status.lower() == 'void':
                    mapped_invoice_status = 'failed'
                if payment_status != mapped_invoice_status:
                    continue
            
            all_tx.append({
                'id': f"invoice_{inv.id}",
                'type': 'Invoice',
                'raw_type': 'invoice',
                'date': inv.created_at.isoformat() + 'Z',
                'student_id': student.id,
                'student_name': student_name,
                'student_status': student.status,
                'grade': grade,
                'description': f"Invoice - Due {inv.due_date.isoformat() if inv.due_date else 'N/A'}" if len(inv.items) == 0 else f"{', '.join([i.description for i in inv.items])}",
                'status': inv.status,
                'amount': inv.total_amount,
                'method': None,
                'payment_status': None
            })
            
        for p in payments:
            student = p.account.student if p.account else None
            if not student: continue
            
            student_name = f"{student.first_name} {student.last_name}"
            grade = student.grade_level
            
            if search and search not in student_name.lower():
                continue
                
            if enrollment_status and enrollment_status != 'all' and enrollment_status != 'all status':
                mapped_status = student.status.lower()
                if enrollment_status == 'graduate' and mapped_status == 'graduated':
                    pass
                elif enrollment_status == 'in active' and mapped_status == 'inactive':
                    pass
                else:
                    if mapped_status != enrollment_status:
                        continue
            
            if transaction_type and transaction_type != 'all transaction types' and transaction_type != 'payment':
                continue
                
            if payment_mode and payment_mode != 'all payment modes':
                method_lower = p.method.lower()
                is_card = 'card' in method_lower or 'debit' in method_lower or 'credit' in method_lower
                is_bank = 'ach' in method_lower or 'bank' in method_lower or 'transfer' in method_lower
                if payment_mode == 'cards' and not is_card:
                    continue
                elif payment_mode == 'bank / ach transfer' and not is_bank:
                    continue
                elif payment_mode == 'oother' and (is_card or is_bank):
                    continue
            
            if payment_status:
                mapped_status = p.status.lower()
                if payment_status == 'successful' and mapped_status == 'success':
                    pass
                elif payment_status == 'in process' and mapped_status == 'in process':
                    pass
                elif payment_status == 'failed' and mapped_status == 'failed':
                    pass
                else:
                    continue
                    
            all_tx.append({
                'id': f"payment_{p.id}",
                'type': 'Payment',
                'raw_type': 'payment',
                'date': p.transaction_date.isoformat() + 'Z',
                'student_id': student.id,
                'student_name': student_name,
                'student_status': student.status,
                'grade': grade,
                'description': f"Payment via {p.method}" if not p.notes else f"Payment via {p.method} - {p.notes}",
                'status': 'Success' if p.status == 'Success' else ('Failed' if p.status == 'Failed' else 'In Process'),
                'amount': -p.amount,
                'method': p.method,
                'payment_status': p.status
            })
            
        for c in credits:
            student = c.account.student if c.account else None
            if not student: continue
            
            student_name = f"{student.first_name} {student.last_name}"
            grade = student.grade_level
            
            if search and search not in student_name.lower():
                continue
                
            if enrollment_status and enrollment_status != 'all' and enrollment_status != 'all status':
                mapped_status = student.status.lower()
                if enrollment_status == 'graduate' and mapped_status == 'graduated':
                    pass
                elif enrollment_status == 'in active' and mapped_status == 'inactive':
                    pass
                else:
                    if mapped_status != enrollment_status:
                        continue
            
            if transaction_type and transaction_type != 'all transaction types' and transaction_type != 'credit':
                continue
                
            if payment_mode and payment_mode != 'all payment modes':
                if payment_mode != 'oother':
                    continue
                    
            if payment_status:
                if payment_status != 'successful':
                    continue
                    
            all_tx.append({
                'id': f"credit_{c.id}",
                'type': 'Credit',
                'raw_type': 'credit',
                'date': c.created_at.isoformat() + 'Z',
                'student_id': student.id,
                'student_name': student_name,
                'student_status': student.status,
                'grade': grade,
                'description': c.reason,
                'status': 'Applied',
                'amount': -c.amount,
                'method': 'Credit',
                'payment_status': 'Success'
            })
            
        all_tx.sort(key=lambda x: x['date'], reverse=True)
        
        total_results = len(all_tx)
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        paginated_tx = all_tx[start_idx:end_idx]
        
        return jsonify({
            'transactions': paginated_tx,
            'total_results': total_results,
            'page': page,
            'limit': limit,
            'metrics': {
                'total_paid': total_paid_metric,
                'total_in_process': total_in_process_metric,
                'total_unpaid': total_unpaid_metric
            }
        }), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@billing_bp.route('/import-procare-plans', methods=['POST'])
@jwt_required()
def import_procare_plans():
    actor = get_actor()
    data = request.get_json() or {}
    plans_data = data.get('plans', [])

    if not plans_data:
        return jsonify({"error": "No tuition plan data provided for import."}), 400

    imported_plans_count = 0
    created_subscriptions_count = 0
    today = date.today()

    for item in plans_data:
        plan_name = item.get('plan_name') or 'Procare Tuition Plan'
        cycle = item.get('cycle') or 'Monthly'
        amount = float(item.get('amount') or 0.0)
        description = item.get('description') or 'Tuition Charge'
        student_ids = item.get('student_ids', [])

        items_json = item.get('items') or [
            {
                "description": description,
                "amount": amount,
                "type": "New Item"
            }
        ]

        # 1. Save or update BillingPlan template
        plan = BillingPlan.query.filter_by(name=plan_name).first()
        if not plan:
            plan = BillingPlan(name=plan_name, items_json=items_json)
            db.session.add(plan)
            db.session.flush()
            imported_plans_count += 1

        # 2. Assign to specified students if any
        cycle_clean = cycle.lower().replace('-', '').replace(' ', '')
        if cycle_clean == 'weekly':
            next_invoice = today + relativedelta(weeks=1)
        elif cycle_clean == 'biweekly':
            next_invoice = today + relativedelta(weeks=2)
        elif cycle_clean == 'quarterly':
            next_invoice = today + relativedelta(months=3)
        else:
            next_invoice = today + relativedelta(months=1)

        for s_id in student_ids:
            st = Student.query.get(s_id)
            if st and st.financial_account:
                sub = Subscription(
                    account_id=st.financial_account.id,
                    plan_name=plan_name,
                    cycle=cycle,
                    start_date=today,
                    end_date=None,
                    invoice_generation_day=1,
                    due_day=15,
                    next_invoice_date=next_invoice,
                    items_json=items_json
                )
                db.session.add(sub)
                created_subscriptions_count += 1

    log_activity(actor, f"Imported {imported_plans_count} plan template(s) and {created_subscriptions_count} subscription(s) from Procare export.")
    db.session.commit()

    return jsonify({
        "message": f"Successfully imported Procare tuition plans!",
        "imported_templates_count": imported_plans_count,
        "assigned_subscriptions_count": created_subscriptions_count
    }), 201