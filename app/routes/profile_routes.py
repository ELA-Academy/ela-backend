from flask import Blueprint, request, jsonify
from app.models import db
from app.models.staff_model import Staff
from app.models.super_admin_model import SuperAdmin
from app.models.activity_log_model import log_activity
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt

profile_bp = Blueprint('profile', __name__)

@profile_bp.route('', methods=['GET'])
@jwt_required()
def get_my_profile():
    claims = get_jwt()
    current_user_email = get_jwt_identity()
    user_role = claims.get('role')

    if user_role == 'superadmin':
        user = SuperAdmin.query.filter_by(email=current_user_email).first()
        if not user:
            return jsonify({"error": "Super Admin not found"}), 404
        return jsonify({
            "id": user.id,
            "name": user.name,
            "preferred_name": user.name,
            "legal_name": user.name,
            "email": user.email,
            "role": "Super Admin",
            "job_title": "Super Administrator",
            "departments": ["Administration"],
            "department_names": ["Administration"],
            "worker_type": "Administrator",
            "hourly_rate": 0.0,
            "payment_method": "Direct deposit",
            "bank_name": "",
            "bank_account_last4": "",
            "bank_routing": "",
            "bank_account_type": "Checking",
            "address": {},
            "emergency_contact": {}
        }), 200
    
    elif user_role in ('staff', 'teacher', 'admin'):
        user = Staff.query.filter_by(email=current_user_email).first()
        if not user:
            return jsonify({"error": "Staff not found"}), 404
        return jsonify(user.to_dict()), 200
        
    return jsonify({"error": "Invalid user role"}), 400

@profile_bp.route('/payment-method', methods=['PUT'])
@jwt_required()
def update_payment_method():
    claims = get_jwt()
    current_user_email = get_jwt_identity()
    user_role = claims.get('role')

    if user_role not in ('staff', 'teacher', 'admin'):
        return jsonify({"message": "Payment method updated"}), 200

    user = Staff.query.filter_by(email=current_user_email).first()
    if not user:
        return jsonify({"error": "Staff not found"}), 404

    data = request.get_json() or {}
    if 'bank_name' in data:
        user.bank_name = data['bank_name']
    if 'bank_account_type' in data:
        user.bank_account_type = data['bank_account_type']
    if 'bank_routing' in data:
        user.bank_routing = data['bank_routing']
    if 'bank_account_last4' in data:
        user.bank_account_last4 = data['bank_account_last4']
    elif 'account_number' in data and data['account_number']:
        user.bank_account_last4 = str(data['account_number'])[-4:]
    if 'payment_method' in data:
        user.payment_method = data['payment_method']

    db.session.commit()
    log_activity(user, f"Updated their direct deposit payment details: {user.bank_name} ending in {user.bank_account_last4}")
    return jsonify(user.to_dict()), 200


@profile_bp.route('/personal', methods=['PUT'])
@jwt_required()
def update_my_personal_details():
    """
    Allows staff/teachers to update their own personal details on Time Tracking.
    Strictly scoped to safe personal fields — compensation/hourly_rate/role are NEVER editable here.
    """
    import json as _json
    claims = get_jwt()
    current_user_email = get_jwt_identity()
    user_role = (claims.get('role') or '').strip().lower()

    if user_role not in ('staff', 'teacher', 'admin'):
        return jsonify({"error": "Only staff/teacher profiles can be updated here."}), 403

    user = Staff.query.filter_by(email=current_user_email).first()
    if not user:
        return jsonify({"error": "Staff profile not found"}), 404

    data = request.get_json() or {}
    if 'pronouns' in data:
        user.pronouns = (data.get('pronouns') or '').strip() or None
    if 'ssn_last4' in data:
        raw_ssn = ''.join(ch for ch in str(data.get('ssn_last4') or '') if ch.isdigit())
        user.ssn_last4 = raw_ssn[-4:] if raw_ssn else None
    if 'birthday' in data:
        user.birthday = (data.get('birthday') or '').strip() or None
    if 'phone' in data:
        user.phone = (data.get('phone') or '').strip() or None
    if 'personal_email' in data:
        user.personal_email = (data.get('personal_email') or '').strip() or None
    if 'address' in data and isinstance(data.get('address'), dict):
        addr = data['address']
        user.address = _json.dumps({
            'street': (addr.get('street') or '').strip(),
            'city': (addr.get('city') or '').strip(),
            'state': (addr.get('state') or '').strip(),
            'zip': (addr.get('zip') or '').strip(),
            'country': (addr.get('country') or 'United States of America').strip(),
        })
    if 'emergency_contact' in data and isinstance(data.get('emergency_contact'), dict):
        emg = data['emergency_contact']
        user.emergency_contact = _json.dumps({
            'name': (emg.get('name') or '').strip(),
            'relationship': (emg.get('relationship') or '').strip(),
            'phone': (emg.get('phone') or '').strip(),
            'email': (emg.get('email') or '').strip(),
        })

    db.session.commit()
    log_activity(user, "Updated their personal profile details")
    return jsonify(user.to_dict()), 200

@profile_bp.route('/change-password', methods=['PUT'])
@jwt_required()
def change_password():
    claims = get_jwt()
    current_user_email = get_jwt_identity()
    user_role = claims.get('role')
    data = request.get_json()

    current_password = data.get('current_password')
    new_password = data.get('new_password')

    if not all([current_password, new_password]):
        return jsonify({"error": "Current password and new password are required"}), 400

    user = None
    if user_role == 'superadmin':
        user = SuperAdmin.query.filter_by(email=current_user_email).first()
    elif user_role in ('staff', 'teacher', 'admin'):
        user = Staff.query.filter_by(email=current_user_email).first()

    if not user:
        return jsonify({"error": "User not found"}), 404

    if not user.check_password(current_password):
        return jsonify({"error": "Current password is incorrect"}), 401

    user.set_password(new_password)
    db.session.commit()
    log_activity(user, "Changed their password")
    
    return jsonify({"message": "Password updated successfully"}), 200

@profile_bp.route('/update', methods=['PUT'])
@jwt_required()
def update_profile():
    claims = get_jwt()
    current_user_email = get_jwt_identity()
    user_role = claims.get('role')
    data = request.get_json() or {}

    name = data.get('name')
    email = data.get('email')

    if not name or not email:
        return jsonify({"error": "Name and email are required"}), 400

    user = None
    if user_role == 'superadmin':
        user = SuperAdmin.query.filter_by(email=current_user_email).first()
    elif user_role in ('staff', 'teacher', 'admin'):
        user = Staff.query.filter_by(email=current_user_email).first()

    if not user:
        return jsonify({"error": "User not found"}), 404

    if email != user.email:
        if user_role == 'staff':
            existing = Staff.query.filter_by(email=email).first()
        else:
            existing = SuperAdmin.query.filter_by(email=email).first()
        if existing:
            return jsonify({"error": "Email is already in use by another user"}), 400
        user.email = email

    user.name = name
    db.session.commit()
    log_activity(user, "Updated their profile details")

    return jsonify({
        "message": "Profile updated successfully",
        "user": {
            "name": user.name,
            "email": user.email,
            "role": user_role
        }
    }), 200
