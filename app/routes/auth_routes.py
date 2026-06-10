from flask import Blueprint, jsonify, request
from flask_jwt_extended import create_access_token
from app.models.staff_model import Staff

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