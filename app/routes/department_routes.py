from flask import Blueprint, request, jsonify
from app.models import db
from app.models.department_model import Department
from app.models.super_admin_model import SuperAdmin
from app.models.activity_log_model import log_activity
from flask_jwt_extended import jwt_required, get_jwt_identity

department_bp = Blueprint('departments', __name__)

# --- THIS IS THE FIX: Define routes that are reserved for built-in dashboards ---
RESERVED_ROUTES = [
    '/admin/admissions',
    '/admin/accounting',
    '/admin/administration'
]
# --- END OF FIX ---

def get_actor():
    email = get_jwt_identity()
    return SuperAdmin.query.filter_by(email=email).first()

@department_bp.route('', methods=['POST'])
@jwt_required()
def create_department():
    actor = get_actor()
    if not actor:
        return jsonify({"error": "Unauthorized actor"}), 401
        
    data = request.get_json()
    name = data.get('name')
    dashboard_route = data.get('dashboard_route')

    if not name:
        return jsonify({"error": "Department name is required"}), 400
    
    # --- FIX: Validate the dashboard route ---
    if dashboard_route and dashboard_route in RESERVED_ROUTES:
        return jsonify({"error": f"The route '{dashboard_route}' is reserved for a system department and cannot be assigned."}), 409 # 409 Conflict
    # --- END OF FIX ---

    if Department.query.filter_by(name=name).first():
        return jsonify({"error": "Department name already exists"}), 409
        
    new_department = Department(
        name=name,
        description=data.get('description'),
        dashboard_route=dashboard_route
    )
    db.session.add(new_department)
    
    log_activity(actor, f"Created new department: '{new_department.name}'", new_department)
    
    db.session.commit()
    return jsonify(new_department.to_dict()), 201

@department_bp.route('', methods=['GET'])
@jwt_required()
def get_departments():
    departments = Department.query.order_by(Department.name).all()
    return jsonify([d.to_dict() for d in departments]), 200

@department_bp.route('/<int:id>', methods=['PUT'])
@jwt_required()
def update_department(id):
    actor = get_actor()
    if not actor:
        return jsonify({"error": "Unauthorized actor"}), 401

    department = Department.query.get_or_404(id)
    data = request.get_json()
    new_dashboard_route = data.get('dashboard_route', department.dashboard_route)

    # --- FIX: Validate the dashboard route on update ---
    # We allow a department to keep its OWN reserved route, but not to take another's.
    # For example, the "Admissions Department" can be updated, but you can't
    # assign "/admin/admissions" to a different department.
    if new_dashboard_route in RESERVED_ROUTES and department.dashboard_route != new_dashboard_route:
        # Check if another department already uses this reserved route.
        existing_dept = Department.query.filter_by(dashboard_route=new_dashboard_route).first()
        if existing_dept and existing_dept.id != department.id:
            return jsonify({"error": f"The route '{new_dashboard_route}' is reserved for a system department and cannot be assigned."}), 409
    # --- END OF FIX ---

    log_activity(actor, f"Updated department: '{department.name}'", department)

    department.name = data.get('name', department.name)
    department.description = data.get('description', department.description)
    department.is_active = data.get('is_active', department.is_active)
    department.dashboard_route = new_dashboard_route
    
    db.session.commit()
    return jsonify(department.to_dict()), 200

@department_bp.route('/<int:id>', methods=['DELETE'])
@jwt_required()
def delete_department(id):
    actor = get_actor()
    if not actor:
        return jsonify({"error": "Unauthorized actor"}), 401

    department = Department.query.get_or_404(id)
    
    log_activity(actor, f"Permanently deleted department: '{department.name}'", department)

    department.staff_members.clear()
    department.tasks.clear()
    db.session.delete(department)
    db.session.commit()
    
    return jsonify({"message": "Department permanently deleted successfully"}), 200