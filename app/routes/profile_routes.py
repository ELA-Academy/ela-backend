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
            "name": user.name,
            "email": user.email,
            "role": "Super Admin",
            "departments": [] # Super Admins don't belong to departments
        }), 200
    
    elif user_role == 'staff':
        user = Staff.query.filter_by(email=current_user_email).first()
        if not user:
            return jsonify({"error": "Staff not found"}), 404
        return jsonify({
            "name": user.name,
            "email": user.email,
            "role": "Staff",
            "departments": [d.name for d in user.departments]
        }), 200
        
    return jsonify({"error": "Invalid user role"}), 400

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
    elif user_role == 'staff':
        user = Staff.query.filter_by(email=current_user_email).first()

    if not user:
        return jsonify({"error": "User not found"}), 404

    if not user.check_password(current_password):
        return jsonify({"error": "Current password is incorrect"}), 401

    user.set_password(new_password)
    db.session.commit()
    
    log_activity(user, "Changed their password") # Log the activity
    
    return jsonify({"message": "Password updated successfully"}), 200