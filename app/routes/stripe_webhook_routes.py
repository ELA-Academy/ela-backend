import os
import logging
try:
    import stripe
except ImportError:
    stripe = None

from flask import Blueprint, request, jsonify, current_app
from datetime import datetime
from sqlalchemy import func
from app.models import db
from app.models.financial_model import (
    Payment, Invoice, StudentFinancialAccount,
    FinancialAuditLog, ProcessedStripeEvent
)
from app.models.student_model import Student, Parent

logger = logging.getLogger("stripe_webhook")
logger.setLevel(logging.INFO)

stripe_webhook_bp = Blueprint('stripe_webhook', __name__)

@stripe_webhook_bp.route('/webhook', methods=['POST'])
def handle_stripe_webhook():
    """
    Stripe Webhook Listener with strict HMAC signature verification,
    event deduplication via ProcessedStripeEvent, and idempotent reconciliation.
    """
    payload = request.get_data()
    sig_header = request.headers.get('Stripe-Signature')

    webhook_secret = current_app.config.get('STRIPE_WEBHOOK_SECRET') or os.getenv('STRIPE_WEBHOOK_SECRET')
    if not webhook_secret:
        logger.error("STRIPE_WEBHOOK_SECRET is not configured. Rejecting webhook.")
        return jsonify({"error": "Webhook secret unconfigured"}), 500

    if not sig_header:
        logger.warning("Received webhook request without Stripe-Signature header.")
        return jsonify({"error": "Missing signature header"}), 400

    # 1. Signature Verification
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, webhook_secret
        )
    except ValueError as e:
        logger.error(f"Invalid payload in webhook: {e}")
        return jsonify({"error": "Invalid payload"}), 400
    except stripe.error.SignatureVerificationError as e:
        logger.error(f"Invalid webhook signature: {e}")
        return jsonify({"error": "Invalid signature"}), 400
    except Exception as e:
        logger.error(f"Unexpected error constructing webhook event: {e}")
        return jsonify({"error": "Webhook processing error"}), 400

    event_id = event['id']
    event_type = event['type']
    data_object = event['data']['object']

    logger.info(f"Received Stripe Webhook event: {event_type} (id: {event_id})")

    # 2. Deduplication check
    existing_event = ProcessedStripeEvent.query.filter_by(event_id=event_id).first()
    if existing_event:
        logger.info(f"Duplicate webhook event {event_id} already processed. Returning 200.")
        return jsonify({"status": "duplicate", "event_id": event_id}), 200

    # 3. Record event + process business logic atomically so failures roll back and Stripe retries
    data_dict = data_object.to_dict() if hasattr(data_object, 'to_dict') else (dict(data_object) if isinstance(data_object, (dict, list)) else {})
    try:
        processed_record = ProcessedStripeEvent(event_id=event_id, event_type=event_type)
        db.session.add(processed_record)
        db.session.flush()

        if event_type == 'payment_intent.succeeded':
            _handle_payment_intent_succeeded(data_dict)
        elif event_type == 'payment_intent.payment_failed':
            _handle_payment_intent_failed(data_dict)
        elif event_type == 'payment_intent.requires_action':
            _handle_payment_intent_requires_action(data_dict)
        elif event_type == 'charge.refunded':
            _handle_charge_refunded(data_dict)
        elif event_type == 'account.updated':
            _handle_connect_account_updated(data_dict)
        elif event_type == 'transfer.reversed':
            _handle_transfer_reversed(data_dict)
        else:
            logger.info(f"Unhandled Stripe webhook event type: {event_type}")

        db.session.commit()
        return jsonify({"status": "success", "event_id": event_id}), 200
    except Exception as e:
        db.session.rollback()
        # Check if rollback was caused by a concurrent duplicate insert on unique event_id
        if ProcessedStripeEvent.query.filter_by(event_id=event_id).first():
            logger.warning(f"Webhook event {event_id} already handled concurrently.")
            return jsonify({"status": "already_handled", "event_id": event_id}), 200
        logger.error(f"Error executing webhook logic for {event_type} ({event_id}): {e}", exc_info=True)
        # Return 500 so Stripe automatically retries transient DB/network errors instead of losing the payment
        return jsonify({"status": "error", "error": "Temporary webhook processing error"}), 500


def _handle_payment_intent_succeeded(pi):
    pi_id = pi.get('id')
    amount_cents = pi.get('amount_received') or pi.get('amount', 0)
    amount = float(amount_cents) / 100.0
    metadata = pi.get('metadata') or {}
    invoice_id = metadata.get('invoice_id')
    student_id = metadata.get('student_id')
    parent_id = metadata.get('parent_id')

    # Look for existing Payment row
    payment = Payment.query.filter_by(stripe_payment_intent_id=pi_id).first()
    if payment:
        payment.status = 'Success'
        payment.amount = amount
        db.session.flush()
    else:
        # Reconcile if created directly or via webhook
        account_id = None
        if student_id:
            student = Student.query.get(student_id)
            if student and student.financial_account:
                account_id = student.financial_account.id
        if not account_id and invoice_id:
            inv = Invoice.query.get(invoice_id)
            if inv:
                account_id = inv.account_id

        if account_id:
            payment = Payment(
                account_id=account_id,
                invoice_id=invoice_id,
                amount=amount,
                method="Stripe Online Payment",
                notes=f"Stripe PaymentIntent {pi_id}",
                status='Success',
                stripe_payment_intent_id=pi_id,
                transaction_date=datetime.utcnow()
            )
            db.session.add(payment)
            db.session.flush()

    # Recheck Invoice status or auto-settle open invoices
    if invoice_id:
        inv = Invoice.query.get(invoice_id)
        if inv:
            total_paid = db.session.query(func.sum(Payment.amount - func.coalesce(Payment.refund_amount, 0.0))).filter_by(invoice_id=inv.id, status='Success').scalar() or 0.0
            if round(total_paid, 2) >= round(inv.total_amount, 2):
                inv.status = 'Paid'
                db.session.flush()
    elif payment and payment.account_id:
        open_invoices = Invoice.query.filter(
            Invoice.account_id == payment.account_id,
            Invoice.status.in_(['Sent', 'Overdue', 'Action Required'])
        ).order_by(Invoice.created_at.asc()).all()

        unallocated = round(payment.amount, 2)
        for inv in open_invoices:
            if unallocated <= 0:
                break
            paid_so_far = db.session.query(func.sum(Payment.amount - func.coalesce(Payment.refund_amount, 0.0))).filter_by(invoice_id=inv.id, status='Success').scalar() or 0.0
            due_amount = round(max(0.0, inv.total_amount - paid_so_far), 2)
            if due_amount > 0:
                if unallocated >= due_amount:
                    inv.status = 'Paid'
                    unallocated = round(unallocated - due_amount, 2)
                    if not payment.invoice_id:
                        payment.invoice_id = inv.id
                else:
                    unallocated = 0.0
        db.session.flush()

    logger.info(f"Reconciled successful payment of ${amount:.2f} for PaymentIntent {pi_id}")


def _handle_payment_intent_failed(pi):
    pi_id = pi.get('id')
    last_error = pi.get('last_payment_error', {})
    err_message = last_error.get('message', 'Payment failed')

    payment = Payment.query.filter_by(stripe_payment_intent_id=pi_id).first()
    if payment:
        payment.status = 'Failed'
        payment.notes = f"Failed: {err_message}"
        db.session.flush()

    logger.warning(f"PaymentIntent {pi_id} marked as Failed: {err_message}")


def _handle_payment_intent_requires_action(pi):
    pi_id = pi.get('id')
    metadata = pi.get('metadata') or {}
    invoice_id = metadata.get('invoice_id')

    payment = Payment.query.filter_by(stripe_payment_intent_id=pi_id).first()
    if payment:
        payment.status = 'Action Required'
        db.session.flush()

    if invoice_id:
        inv = Invoice.query.get(invoice_id)
        if inv and inv.status != 'Paid':
            inv.status = 'Action Required'
            db.session.flush()

    logger.info(f"PaymentIntent {pi_id} requires customer 3DS action.")


def _handle_charge_refunded(charge):
    charge_id = charge.get('id')
    pi_id = charge.get('payment_intent')
    amount_refunded_cents = charge.get('amount_refunded', 0)
    refund_amount = float(amount_refunded_cents) / 100.0

    payment = None
    if pi_id:
        payment = Payment.query.filter_by(stripe_payment_intent_id=pi_id).first()
    if not payment and charge_id:
        payment = Payment.query.filter_by(stripe_charge_id=charge_id).first()

    if payment:
        payment.is_refunded = True
        payment.refund_amount = refund_amount
        if round(refund_amount, 2) >= round(payment.amount, 2):
            payment.status = 'Refunded'

        # Record financial audit log
        try:
            log = FinancialAuditLog(
                account_id=payment.account_id,
                transaction_type='Refund',
                transaction_id=str(payment.id),
                action='Refund',
                amount=refund_amount,
                status='Success',
                actor_name="Stripe Webhook",
                description=f"Refund processed via Stripe (Charge: {charge_id})"
            )
            db.session.add(log)
        except Exception as e:
            logger.error(f"Failed to log refund audit: {e}")

        db.session.flush()
        logger.info(f"Recorded refund of ${refund_amount:.2f} on Payment {payment.id}")


def _handle_connect_account_updated(account_obj):
    """Syncs Staff.stripe_connect_onboarded when a worker finishes Stripe Connect Express onboarding."""
    from app.models.staff_model import Staff
    acct_id = account_obj.get('id')
    if not acct_id:
        return
    staff = Staff.query.filter_by(stripe_connect_account_id=acct_id).first()
    if not staff:
        return
    payouts_enabled = bool(account_obj.get('payouts_enabled') or account_obj.get('details_submitted'))
    staff.stripe_connect_onboarded = payouts_enabled
    db.session.flush()
    logger.info(f"Updated Stripe Connect status for Staff {staff.id} ({acct_id}): payouts_enabled={payouts_enabled}")


def _handle_transfer_reversed(transfer_obj):
    """Marks a PayrollPaymentItem as reversed if a Stripe Connect transfer is reversed."""
    from app.models.payroll_models import PayrollPaymentItem
    transfer_id = transfer_obj.get('id')
    if not transfer_id:
        return
    item = PayrollPaymentItem.query.filter_by(stripe_transfer_id=transfer_id).first()
    if item:
        item.payout_status = 'reversed'
        item.status = 'reversed'
        item.payout_error = 'Stripe transfer was reversed.'
        db.session.flush()
        logger.warning(f"Marked PayrollPaymentItem {item.id} as reversed for Stripe Transfer {transfer_id}")
