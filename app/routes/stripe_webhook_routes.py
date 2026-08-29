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

    # Record event in DB to prevent concurrent replay
    try:
        processed_record = ProcessedStripeEvent(event_id=event_id, event_type=event_type)
        db.session.add(processed_record)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        # If insertion fails due to race condition unique constraint, skip
        logger.warning(f"Could not record processed event {event_id} (likely processed in parallel): {e}")
        return jsonify({"status": "already_handled"}), 200

    # 3. Process Events
    try:
        if event_type == 'payment_intent.succeeded':
            _handle_payment_intent_succeeded(data_object)
        elif event_type == 'payment_intent.payment_failed':
            _handle_payment_intent_failed(data_object)
        elif event_type == 'payment_intent.requires_action':
            _handle_payment_intent_requires_action(data_object)
        elif event_type == 'charge.refunded':
            _handle_charge_refunded(data_object)
        else:
            logger.info(f"Unhandled Stripe webhook event type: {event_type}")

        return jsonify({"status": "success", "event_id": event_id}), 200
    except Exception as e:
        logger.error(f"Error executing webhook logic for {event_type} ({event_id}): {e}", exc_info=True)
        # Even on internal handling error, return 200 if event recorded to avoid endless Stripe retries for logic bugs
        return jsonify({"status": "handled_with_warning", "error": str(e)}), 200


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
        db.session.commit()
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
            db.session.commit()

    # Recheck Invoice status
    if invoice_id:
        inv = Invoice.query.get(invoice_id)
        if inv:
            total_paid = db.session.query(func.sum(Payment.amount)).filter_by(invoice_id=inv.id, status='Success').scalar() or 0.0
            if total_paid >= inv.total_amount:
                inv.status = 'Paid'
                db.session.commit()

    logger.info(f"Reconciled successful payment of ${amount:.2f} for PaymentIntent {pi_id}")


def _handle_payment_intent_failed(pi):
    pi_id = pi.get('id')
    last_error = pi.get('last_payment_error', {})
    err_message = last_error.get('message', 'Payment failed')

    payment = Payment.query.filter_by(stripe_payment_intent_id=pi_id).first()
    if payment:
        payment.status = 'Failed'
        payment.notes = f"Failed: {err_message}"
        db.session.commit()

    logger.warning(f"PaymentIntent {pi_id} marked as Failed: {err_message}")


def _handle_payment_intent_requires_action(pi):
    pi_id = pi.get('id')
    metadata = pi.get('metadata') or {}
    invoice_id = metadata.get('invoice_id')

    payment = Payment.query.filter_by(stripe_payment_intent_id=pi_id).first()
    if payment:
        payment.status = 'Action Required'
        db.session.commit()

    if invoice_id:
        inv = Invoice.query.get(invoice_id)
        if inv and inv.status != 'Paid':
            inv.status = 'Action Required'
            db.session.commit()

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
        if refund_amount >= payment.amount:
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

        db.session.commit()
        logger.info(f"Recorded refund of ${refund_amount:.2f} on Payment {payment.id}")
