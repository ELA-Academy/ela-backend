from flask import Blueprint, request, jsonify
from app.models import db
from app.models.staff_model import Staff
from app.models.department_model import Department
from app.models.super_admin_model import SuperAdmin
from app.models.activity_log_model import log_activity
from flask_jwt_extended import jwt_required, get_jwt_identity

staff_bp = Blueprint('staff', __name__)

# Helper to get the current super admin actor
def get_actor():
    email = get_jwt_identity()
    return SuperAdmin.query.filter_by(email=email).first()

@staff_bp.route('', methods=['POST'])
@jwt_required()
def create_staff():
    actor = get_actor()
    if not actor:
        return jsonify({"error": "Unauthorized actor"}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid or missing JSON body"}), 400
        
    name = data.get('name')
    email = data.get('email')
    password = data.get('password')
    department_ids = data.get('department_ids', [])

    if not name or not email:
        return jsonify({"error": "Name and email are required"}), 400
    
    if Staff.query.filter_by(email=email).first():
        return jsonify({"error": "Email already in use"}), 409

    new_staff = Staff(name=name, email=email)
    
    is_invite = False
    if not password:
        import uuid
        password = uuid.uuid4().hex
        is_invite = True

    new_staff.set_password(password)
    
    for dept_id in department_ids:
        dept = Department.query.get(dept_id)
        if dept:
            new_staff.departments.append(dept)
        else:
            return jsonify({"error": f"Department with id {dept_id} not found"}), 404
            
    db.session.add(new_staff)
    
    # Log the activity
    log_activity(actor, f"Created new staff member: '{new_staff.name}'", new_staff)
    
    if is_invite:
        from flask_jwt_extended import create_access_token
        from datetime import timedelta
        from app import mail
        from app.utils.email_otp import send_staff_invite_email
        import os

        # Generate a password setup token
        setup_token = create_access_token(
            identity=email,
            additional_claims={"purpose": "setup-password", "name": name},
            expires_delta=timedelta(days=7)
        )
        
        # Resolve department email for the invite
        from app.utils.ms_graph_email import get_dept_email_from_dept
        dept_email = None
        if department_ids:
            first_dept = Department.query.get(department_ids[0])
            if first_dept:
                dept_email = get_dept_email_from_dept(first_dept)

        send_staff_invite_email(mail, email, name, invite_link, sender_email=dept_email)
        
    db.session.commit()
    return jsonify(new_staff.to_dict()), 201

@staff_bp.route('', methods=['GET'])
@jwt_required()
def get_staff():
    staff_list = Staff.query.all()
    return jsonify([s.to_dict() for s in staff_list]), 200

@staff_bp.route('/<int:id>', methods=['PUT'])
@jwt_required()
def update_staff(id):
    actor = get_actor()
    if not actor:
        return jsonify({"error": "Unauthorized actor"}), 401

    staff = Staff.query.get_or_404(id)
    data = request.get_json()

    # Log before changes
    log_activity(actor, f"Updated staff member details for '{staff.name}'", staff)

    staff.name = data.get('name', staff.name)
    staff.email = data.get('email', staff.email)
    staff.is_active = data.get('is_active', staff.is_active)

    if 'department_ids' in data:
        staff.departments.clear()
        for dept_id in data['department_ids']:
            dept = Department.query.get(dept_id)
            if dept:
                staff.departments.append(dept)
            else:
                return jsonify({"error": f"Department with id {dept_id} not found"}), 404

    if data.get('password'):
        staff.set_password(data['password'])

    db.session.commit()
    return jsonify(staff.to_dict()), 200

@staff_bp.route('/<int:id>', methods=['DELETE'])
@jwt_required()
def delete_staff(id):
    actor = get_actor()
    if not actor:
        return jsonify({"error": "Unauthorized actor"}), 401
        
    staff = Staff.query.get_or_404(id)

    # Log before deleting
    log_activity(actor, f"Deleted staff member: '{staff.name}'", staff)

    try:
        db.session.delete(staff)
        db.session.commit()
        return jsonify({"message": "Staff member deleted successfully"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "Could not delete staff member. They may have associated records. Please reassign or remove their tasks first."}), 409