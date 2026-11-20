from flask import Blueprint, request, jsonify
from app.models import db
from app.models.staff_model import Staff
from app.models.department_model import Department
from app.models.super_admin_model import SuperAdmin
from app.models.activity_log_model import log_activity
from flask_jwt_extended import jwt_required, get_jwt_identity
import json
import re

staff_bp = Blueprint('staff', __name__)

def is_it_department_name(name):
    if not name:
        return False
    clean = name.strip().lower()
    return bool(re.search(r'\b(it|information technology|info tech|tech|accounting|admin)\b', clean))

# Helper to get the current authorized actor (SuperAdmin, Payroll Co-Admin, or IT/Accounting/HR Department Staff)
def get_actor():
    email = get_jwt_identity()
    admin = SuperAdmin.query.filter_by(email=email).first()
    if admin:
        return admin

    staff = Staff.query.filter_by(email=email).first()
    if staff and getattr(staff, 'is_active', True):
        from app.models.payroll_models import PayrollAdminAccess
        access = PayrollAdminAccess.query.filter_by(staff_id=staff.id, is_active=True).first()
        if access:
            return staff
        for dept in staff.departments:
            if is_it_department_name(dept.name):
                return staff
    return None

@staff_bp.route('', methods=['POST'])
@jwt_required()
def create_staff():
    actor = get_actor()
    if not actor:
        return jsonify({"error": "Unauthorized actor"}), 401

    from app.utils.sanitizer import sanitize_dict
    data = sanitize_dict(request.get_json(silent=True) or {})
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

    role = data.get('role', 'staff')
    new_staff = Staff(
        name=name,
        email=email,
        role=role,
        job_title=data.get('job_title', 'Contractor'),
        worker_type=data.get('worker_type', 'Contractor'),
        hourly_rate=float(data.get('hourly_rate', 25.0)),
        manager_name=data.get('manager_name', 'Marcus Cooper'),
        work_email=data.get('work_email', email),
        start_date=data.get('start_date', 'Jan 22, 2023')
    )
    
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
    log_activity(actor, f"Created new staff member: '{new_staff.name}'", new_staff)
    
    if is_invite:
        from flask_jwt_extended import create_access_token
        from datetime import timedelta
        from flask import current_app
        from app import mail
        from app.utils.email_otp import send_staff_invite_email
        import os

        setup_token = create_access_token(
            identity=email,
            additional_claims={"purpose": "setup-password", "name": name},
            expires_delta=timedelta(days=7)
        )

        frontend_url = current_app.config.get('FRONTEND_URL', 'http://localhost:5173')
        frontend_url = os.getenv('FRONTEND_URL', frontend_url)
        invite_link = f"{frontend_url}/setup-password?token={setup_token}"
        
        dept_email = None
        if department_ids:
            first_dept = Department.query.get(department_ids[0])
            if first_dept:
                from app.utils.ms_graph_email import get_dept_email_from_dept
                dept_email = get_dept_email_from_dept(first_dept)

        try:
            send_staff_invite_email(mail, email, name, invite_link, sender_email=dept_email)
        except Exception as e:
            print(f"[STAFF INVITE EMAIL ERROR] Failed to send invite to {email}: {e}")
        
    db.session.commit()
    return jsonify(new_staff.to_dict()), 201

@staff_bp.route('', methods=['GET'])
@jwt_required()
def get_staff():
    staff_list = Staff.query.all()
    return jsonify([s.to_dict() for s in staff_list]), 200

@staff_bp.route('/<int:id>', methods=['GET'])
@jwt_required()
def get_staff_by_id(id):
    staff = Staff.query.get_or_404(id)
    return jsonify(staff.to_dict()), 200

@staff_bp.route('/<int:id>', methods=['PUT'])
@jwt_required()
def update_staff(id):
    actor = get_actor()
    if not actor:
        return jsonify({"error": "Unauthorized actor"}), 401

    staff = Staff.query.get_or_404(id)
    from app.utils.sanitizer import sanitize_dict
    data = sanitize_dict(request.get_json() or {})

    old_depts = [d.name for d in staff.departments]
    log_activity(actor, f"Updated staff member details for '{staff.name}'", staff)

    staff.name = data.get('name', staff.name)
    staff.email = data.get('email', staff.email)
    if 'is_active' in data:
        staff.is_active = bool(data['is_active'])
    if 'role' in data:
        staff.role = data['role']

    # Detailed ELA TT / Payroll fields
    if 'job_title' in data:
        staff.job_title = data['job_title']
    if 'worker_type' in data:
        staff.worker_type = data['worker_type']
    if 'worker_status' in data:
        staff.worker_status = data['worker_status']
    if 'hourly_rate' in data and data['hourly_rate'] is not None:
        try:
            staff.hourly_rate = float(data['hourly_rate'])
        except (ValueError, TypeError):
            pass
    if 'manager_name' in data:
        staff.manager_name = data['manager_name']
    if 'work_email' in data:
        staff.work_email = data['work_email']
    if 'country' in data:
        staff.country = data['country']
    if 'start_date' in data:
        staff.start_date = data['start_date']
    if 'preferred_name' in data:
        staff.preferred_name = data['preferred_name']
    if 'legal_name' in data:
        staff.legal_name = data['legal_name']
    if 'pronouns' in data:
        staff.pronouns = data['pronouns']
    if 'ssn_last4' in data:
        staff.ssn_last4 = data['ssn_last4']
    if 'birthday' in data:
        staff.birthday = data['birthday']
    if 'personal_email' in data:
        staff.personal_email = data['personal_email']
    if 'phone' in data:
        staff.phone = data['phone']
    if 'payment_method' in data:
        staff.payment_method = data['payment_method']
    if 'bank_name' in data:
        staff.bank_name = data['bank_name']
    if 'bank_account_last4' in data:
        staff.bank_account_last4 = data['bank_account_last4']
    if 'bank_routing' in data:
        staff.bank_routing = data['bank_routing']
    if 'bank_account_type' in data:
        staff.bank_account_type = data['bank_account_type']
    if 'receives_tips' in data:
        staff.receives_tips = bool(data['receives_tips'])
    if 'expected_hours' in data:
        staff.expected_hours = data['expected_hours']
    for pay_field in ('substituting_pay', 'tutoring_pay', 'training_pay'):
        if pay_field in data:
            val = data[pay_field]
            if val in (None, ''):
                setattr(staff, pay_field, None)
            else:
                try:
                    setattr(staff, pay_field, float(val))
                except (ValueError, TypeError):
                    pass
    for bonus_field in ('us_holiday_bonuses', 'winter_break_bonuses', 'spring_break_bonuses'):
        if bonus_field in data:
            setattr(staff, bonus_field, data[bonus_field])

    if 'address' in data:
        staff.address = json.dumps(data['address']) if isinstance(data['address'], (dict, list)) else str(data['address'])
    if 'emergency_contact' in data:
        staff.emergency_contact = json.dumps(data['emergency_contact']) if isinstance(data['emergency_contact'], (dict, list)) else str(data['emergency_contact'])
    if 'compensation_history' in data:
        staff.compensation_history = json.dumps(data['compensation_history']) if isinstance(data['compensation_history'], (dict, list)) else str(data['compensation_history'])

    if 'department_ids' in data:
        staff.departments.clear()
        for dept_id in data['department_ids']:
            dept = Department.query.get(dept_id)
            if dept:
                staff.departments.append(dept)
            else:
                return jsonify({"error": f"Department with id {dept_id} not found"}), 404
        new_depts = [d.name for d in staff.departments]
        if old_depts != new_depts:
            log_activity(actor, f"Changed permissions/departments for '{staff.name}' from {old_depts} to {new_depts}", staff)

    if data.get('password'):
        staff.set_password(data['password'])

    db.session.commit()
    return jsonify(staff.to_dict()), 200

@staff_bp.route('/<int:id>/compensation', methods=['PUT'])
@jwt_required()
def update_staff_compensation(id):
    actor = get_actor()
    if not actor:
        return jsonify({"error": "Unauthorized actor"}), 401

    staff = Staff.query.get_or_404(id)
    data = request.get_json() or {}

    new_rate = data.get('rate')
    if new_rate is not None:
        try:
            staff.hourly_rate = float(new_rate)
        except (ValueError, TypeError):
            pass

    if 'job_title' in data and data['job_title']:
        staff.job_title = data['job_title']
    if 'receives_tips' in data:
        staff.receives_tips = bool(data['receives_tips'])
    if 'expected_hours' in data:
        staff.expected_hours = data['expected_hours']
    for pay_field in ('substituting_pay', 'tutoring_pay', 'training_pay'):
        if pay_field in data:
            val = data[pay_field]
            if val in (None, ''):
                setattr(staff, pay_field, None)
            else:
                try:
                    setattr(staff, pay_field, float(val))
                except (ValueError, TypeError):
                    pass
    for bonus_field in ('us_holiday_bonuses', 'winter_break_bonuses', 'spring_break_bonuses'):
        if bonus_field in data:
            setattr(staff, bonus_field, data[bonus_field])

    effective_date = data.get('effective_date', datetime.utcnow().strftime('%m/%d/%Y'))
    reason = data.get('reason', 'Compensation update')

    # Append to compensation history
    try:
        history = json.loads(staff.compensation_history) if staff.compensation_history else []
    except Exception:
        history = []

    history.insert(0, {
        "rate": staff.hourly_rate,
        "effective_date": effective_date,
        "reason": reason
    })
    staff.compensation_history = json.dumps(history)

    log_activity(actor, f"Updated compensation for '{staff.name}' to ${staff.hourly_rate:.2f}/hr", staff)
    db.session.commit()
    return jsonify(staff.to_dict()), 200

@staff_bp.route('/<int:id>', methods=['DELETE'])
@jwt_required()
def delete_staff(id):
    actor = get_actor()
    if not actor:
        return jsonify({"error": "Unauthorized actor"}), 401
        
    staff = Staff.query.get_or_404(id)
    log_activity(actor, f"Deleted staff member: '{staff.name}'", staff)

    try:
        db.session.delete(staff)
        db.session.commit()
        return jsonify({"message": "Staff member deleted successfully"}), 200
    except Exception:
        db.session.rollback()
        return jsonify({"error": "Could not delete staff member. They may have associated records. Please reassign or remove their tasks first."}), 409
