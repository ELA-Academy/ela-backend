import os
import logging
import stripe
from flask import current_app
from app.models import db
from app.models.student_model import Parent, ParentPaymentMethod

logger = logging.getLogger("stripe_service")
logger.setLevel(logging.INFO)

def _get_api_key():
    """Retrieve Stripe API Secret Key strictly from config or environment."""
    key = None
    if current_app:
        key = current_app.config.get('STRIPE_SECRET_KEY')
    if not key:
        key = os.getenv('STRIPE_SECRET_KEY')
    if not key:
        raise ValueError("STRIPE_SECRET_KEY is not configured in the environment.")
    return key

def get_or_create_stripe_customer(parent):
    """
    Ensures a Stripe Customer object exists for the given Parent model instance.
    Stores and commits the stripe_customer_id to the database.
    """
    stripe.api_key = _get_api_key()

    if parent.stripe_customer_id:
        try:
            customer = stripe.Customer.retrieve(parent.stripe_customer_id)
            if not getattr(customer, 'deleted', False):
                return customer.id
        except stripe.error.StripeError as e:
            logger.warning(f"Failed to retrieve existing Stripe customer {parent.stripe_customer_id}: {e}")

    # Create new customer
    try:
        customer = stripe.Customer.create(
            email=parent.email,
            name=f"{parent.first_name} {parent.last_name}",
            phone=parent.phone if parent.phone and parent.phone != "N/A" else None,
            metadata={
                "parent_id": str(parent.id),
                "environment": os.getenv("FLASK_ENV", "production")
            }
        )
        parent.stripe_customer_id = customer.id
        db.session.commit()
        logger.info(f"Created Stripe Customer {customer.id} for Parent ID {parent.id}")
        return customer.id
    except stripe.error.StripeError as e:
        logger.error(f"Stripe Customer creation error for Parent {parent.id}: {e}", exc_info=True)
        raise

def create_setup_intent(parent, idempotency_key=None):
    """
    Creates a Stripe SetupIntent to securely attach a payment method
    (Card or Bank Account) via Stripe Elements without raw PAN touching the server.
    """
    stripe.api_key = _get_api_key()
    customer_id = get_or_create_stripe_customer(parent)

    try:
        setup_intent = stripe.SetupIntent.create(
            customer=customer_id,
            payment_method_types=['card', 'us_bank_account'],
            metadata={
                "parent_id": str(parent.id),
            },
            idempotency_key=idempotency_key
        )
        return {
            "client_secret": setup_intent.client_secret,
            "setup_intent_id": setup_intent.id,
            "customer_id": customer_id
        }
    except stripe.error.CardError as e:
        logger.error(f"Card error during SetupIntent create: {e.user_message} (request_id: {e.request_id})")
        raise
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error during SetupIntent create: {e} (request_id: {getattr(e, 'request_id', None)})")
        raise

def create_payment_intent(
    amount_in_cents,
    customer_id=None,
    payment_method_id=None,
    currency="usd",
    description=None,
    metadata=None,
    idempotency_key=None,
    off_session=False,
    confirm=False
):
    """
    Creates a Stripe PaymentIntent for paying invoices, tuition, or balances.
    Supports on-session (Elements / 3DS) and off-session (saved card / auto-pay).
    """
    stripe.api_key = _get_api_key()
    meta = metadata or {}

    params = {
        "amount": int(amount_in_cents),
        "currency": currency.lower(),
        "description": description,
        "metadata": meta,
        "automatic_payment_methods": {"enabled": True, "allow_redirects": "always"} if not payment_method_id else None
    }

    # Remove automatic_payment_methods if explicit payment method is passed
    if payment_method_id:
        params.pop("automatic_payment_methods", None)
        params["payment_method"] = payment_method_id

    if customer_id:
        params["customer"] = customer_id

    if off_session:
        params["off_session"] = True
        params["confirm"] = True

    if confirm and not off_session:
        params["confirm"] = True

    try:
        intent = stripe.PaymentIntent.create(
            **{k: v for k, v in params.items() if v is not None},
            idempotency_key=idempotency_key
        )
        return intent
    except stripe.error.CardError as e:
        err = e.error
        logger.warning(
            f"Card declined / authentication required: code={err.code}, decline_code={err.decline_code}, "
            f"request_id={e.request_id}"
        )
        return {
            "error": True,
            "type": "card_error",
            "code": err.code,
            "decline_code": err.decline_code,
            "message": err.message or "Your card was declined. Please check the details or try another card.",
            "payment_intent": err.payment_intent if hasattr(err, 'payment_intent') else None
        }
    except stripe.error.StripeError as e:
        logger.error(f"Stripe API error creating PaymentIntent: {e} (request_id: {getattr(e, 'request_id', None)})")
        raise

def retrieve_payment_method_details(stripe_payment_method_id):
    """
    Retrieves safe display details (brand, last4, exp_month, exp_year, bank_name)
    from Stripe to store in ParentPaymentMethod.
    """
    stripe.api_key = _get_api_key()
    try:
        pm = stripe.PaymentMethod.retrieve(stripe_payment_method_id)
        details = {
            "id": pm.id,
            "type": pm.type,
            "card_brand": None,
            "last4": "0000",
            "exp_month": None,
            "exp_year": None,
            "bank_name": None,
            "account_holder_name": None
        }

        if pm.type == "card" and pm.card:
            details["card_brand"] = (pm.card.brand or "Card").capitalize()
            details["last4"] = pm.card.last4
            details["exp_month"] = pm.card.exp_month
            details["exp_year"] = pm.card.exp_year
        elif pm.type == "us_bank_account" and pm.us_bank_account:
            details["type"] = "bank_account"
            details["bank_name"] = pm.us_bank_account.bank_name or "Verified Bank"
            details["last4"] = pm.us_bank_account.last4
            details["account_holder_name"] = pm.billing_details.name if pm.billing_details else None

        return details
    except stripe.error.StripeError as e:
        logger.error(f"Failed to retrieve Stripe PaymentMethod {stripe_payment_method_id}: {e}")
        raise

def detach_payment_method(stripe_payment_method_id):
    """
    Detaches a saved payment method from a Stripe customer when deleted by the parent.
    """
    stripe.api_key = _get_api_key()
    try:
        return stripe.PaymentMethod.detach(stripe_payment_method_id)
    except stripe.error.InvalidRequestError as e:
        logger.warning(f"PaymentMethod {stripe_payment_method_id} already detached or invalid: {e}")
        return None
    except stripe.error.StripeError as e:
        logger.error(f"Error detaching PaymentMethod {stripe_payment_method_id}: {e}")
        raise

def create_refund(charge_id_or_payment_intent_id, amount_in_cents=None, reason=None, metadata=None, idempotency_key=None):
    """
    Issues a refund on a successful Stripe charge or payment intent.
    """
    stripe.api_key = _get_api_key()
    params = {
        "metadata": metadata or {},
        "reason": reason or "requested_by_customer"
    }

    if charge_id_or_payment_intent_id.startswith("pi_"):
        params["payment_intent"] = charge_id_or_payment_intent_id
    else:
        params["charge"] = charge_id_or_payment_intent_id

    if amount_in_cents:
        params["amount"] = int(amount_in_cents)

    try:
        refund = stripe.Refund.create(**params, idempotency_key=idempotency_key)
        return refund
    except stripe.error.StripeError as e:
        logger.error(f"Stripe Refund error for {charge_id_or_payment_intent_id}: {e}")
        raise
