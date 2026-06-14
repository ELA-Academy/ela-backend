from flask import Blueprint, jsonify, request
from flask_jwt_extended import create_access_token
from app.models.staff_model import Staff
from app.models.super_admin_model import SuperAdmin

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    email = data.get('email')
    password = data.get('password')

    if not email or not password:
        return jsonify({"msg": "Email and password are required"}), 400

    staff_member = Staff.query.filter_by(email=email).first()

    if not staff_member or not staff_member.is_active or not staff_member.check_password(password):
        return jsonify({"msg": "Invalid credentials"}), 401

    # Add the staff member's database ID to the token payload.
    additional_claims = {
        "id": staff_member.id, # Add this line
        "name": staff_member.name,
        "departmentNames": [d.name for d in staff_member.departments],
        "dashboardRoutes": [d.dashboard_route for d in staff_member.departments if d.dashboard_route],
        "role": "staff"
    }
    
    access_token = create_access_token(identity=staff_member.email, additional_claims=additional_claims)
    
    return jsonify(access_token=access_token), 200

@auth_bp.route('/verify-setup-token', methods=['POST'])
def verify_setup_token():
    data = request.get_json() or {}
    token = data.get('token')
    if not token:
        return jsonify({"error": "Token is required"}), 400
    
    from flask_jwt_extended import decode_token
    try:
        decoded = decode_token(token)
        purpose = decoded.get('purpose')
        if purpose != 'setup-password':
            return jsonify({"error": "Invalid token purpose", "valid": False}), 400
            
        return jsonify({
            "valid": True,
            "email": decoded.get('sub'),
            "name": decoded.get('name')
        }), 200
    except Exception as e:
        return jsonify({"error": "Invalid or expired token", "valid": False}), 400

@auth_bp.route('/setup-password', methods=['POST'])
def setup_password():
    data = request.get_json() or {}
    token = data.get('token')
    password = data.get('password')

    if not token or not password:
        return jsonify({"error": "Token and password are required"}), 400

    from flask_jwt_extended import decode_token
    from app.models import db
    try:
        decoded = decode_token(token)
        purpose = decoded.get('purpose')
        if purpose != 'setup-password':
            return jsonify({"error": "Invalid token purpose"}), 400
        
        email = decoded.get('sub')
        staff_member = Staff.query.filter_by(email=email).first()
        if staff_member:
            staff_member.set_password(password)
        else:
            admin_member = SuperAdmin.query.filter_by(email=email).first()
            if admin_member:
                admin_member.set_password(password)
            else:
                return jsonify({"error": "Account not found"}), 404
        db.session.commit()
        return jsonify({"message": "Password setup successfully! You can now log in."}), 200
    except Exception as e:
        return jsonify({"error": "Invalid or expired token"}), 400