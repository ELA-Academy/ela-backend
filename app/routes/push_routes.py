import os
import json
from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required
from app.models import db
from app.models.push_subscription_model import PushSubscription
from app.routes.messaging_routes import get_current_user # Re-use our helper

push_bp = Blueprint('push', __name__)

@push_bp.route('/vapid-key', methods=['GET'])
@jwt_required()
def get_vapid_key():
    """Provides the VAPID public key to the frontend."""
    key = os.getenv('VAPID_PUBLIC_KEY')
    if not key:
        return jsonify({"error": "VAPID public key not configured on server."}), 500
    return jsonify({"publicKey": key})

@push_bp.route('/subscribe', methods=['POST'])
@jwt_required()
def subscribe():
    """Saves a user's push notification subscription to the database."""
    user, role = get_current_user()
    if not user:
        return jsonify({"error": "User not found"}), 404
    
    subscription_data = request.get_json()
    if not subscription_data:
        return jsonify({"error": "No subscription data provided"}), 400
    
    # Check for and update an existing subscription, or create a new one.
    if role == 'staff':
        sub = PushSubscription.query.filter_by(staff_id=user.id).first()
    else: # superadmin
        sub = PushSubscription.query.filter_by(super_admin_id=user.id).first()

    if sub:
        sub.subscription_json = json.dumps(subscription_data)
    else:
        if role == 'staff':
            sub = PushSubscription(subscription_json=json.dumps(subscription_data), staff_id=user.id)
        else:
            sub = PushSubscription(subscription_json=json.dumps(subscription_data), super_admin_id=user.id)
        db.session.add(sub)
    
    db.session.commit()
    return jsonify({"message": "Subscription saved."}), 201