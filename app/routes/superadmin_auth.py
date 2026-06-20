from datetime import datetime, timedelta, timezone
import os

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import create_access_token, get_jwt, get_jwt_identity, jwt_required

from app import mail
from app.models import db
from app.models.activity_log_model import log_activity
from app.models.department_model import Department
from app.models.super_admin_model import SuperAdmin
from app.models.login_otp_model import LoginOTP
from app.utils.email_otp import generate_otp, send_otp_email


registration_requests = {}

super_admin_bp = Blueprint('super_admin_auth', __name__)


def get_current_super_admin():
    claims = get_jwt()
    if claims.get('role') != 'superadmin':
        return None
    current_admin_email = get_jwt_identity()
    return SuperAdmin.query.filter_by(email=current_admin_email).first()


def build_access_token(admin):
    additional_claims = {
        "id": admin.id,
        "role": "superadmin",
        "name": admin.name
    }
    return create_access_token(identity=admin.email, additional_claims=additional_claims)


def create_default_departments():
    default_departments = [
        {
            "name": "Admission Department",
            "description": "Handles new student applications and leads.",
            "dashboard_route": "/admin/admissions"
        },
        {
            "name": "Accounting Department",
            "description": "Manages finances, payments, and invoices.",
            "dashboard_route": "/admin/accounting"
        },
        {
            "name": "Administration Department",
            "description": "General school administration and other operational tasks.",
            "dashboard_route": "/admin/administration"
        }
    ]

    for department_data in default_departments:
        existing = Department.query.filter_by(name=department_data["name"]).first()
        if existing:
            continue
        db.session.add(Department(**department_data))


@super_admin_bp.route('/check', methods=['GET'])
def check_super_admin():
    count = SuperAdmin.query.count()
    return jsonify({
        "super_admin_exists": count > 0,
        "super_admin_count": count
    })


@super_admin_bp.route('/register', methods=['POST'])
def register_super_admin_request():
    if SuperAdmin.query.first():
        return jsonify({"error": "Initial Super Admin setup has already been completed."}), 409

    allowed_email = os.getenv('SUPER_ADMIN_EMAIL')
    if not allowed_email:
        current_app.logger.error("SUPER_ADMIN_EMAIL is not set in the environment.")
        return jsonify({"error": "Server configuration error: Super Admin email not defined."}), 500

    data = request.get_json() or {}
    name = data.get('name')
    email = data.get('email')
    password = data.get('password')

    if not all([name, email, password]):
        return jsonify({"error": "Name, email, and password are required."}), 400

    if email.lower() != allowed_email.lower():
        return jsonify({"error": "This email address is not authorized for Super Admin setup."}), 403

    otp = generate_otp()
    registration_requests[email] = {
        'otp': otp,
        'data': {'name': name, 'email': email, 'password': password},
        'timestamp': datetime.now(timezone.utc)
    }

    if not send_otp_email(mail, email, otp):
        return jsonify({"error": "Failed to send verification email. Please check the server configuration."}), 500

    return jsonify({"message": f"A verification code has been sent to {email}."}), 200


@super_admin_bp.route('/verify', methods=['POST'])
def verify_and_create_super_admin():
    if SuperAdmin.query.first():
        return jsonify({"error": "Initial Super Admin setup has already been completed."}), 409

    data = request.get_json() or {}
    email = data.get('email')
    otp_received = data.get('otp')

    if not all([email, otp_received]):
        return jsonify({"error": "Email and OTP are required."}), 400

    request_data = registration_requests.get(email)
    if not request_data:
        return jsonify({"error": "Invalid request or session expired. Please try registering again."}), 400

    if (datetime.now(timezone.utc) - request_data['timestamp']) > timedelta(minutes=10):
        del registration_requests[email]
        return jsonify({"error": "OTP has expired. Please try registering again."}), 400

    if request_data['otp'] != otp_received:
        return jsonify({"error": "Invalid verification code."}), 400

    admin_data = request_data['data']
    new_admin = SuperAdmin(name=admin_data['name'], email=admin_data['email'])
    new_admin.set_password(admin_data['password'])
    db.session.add(new_admin)

    create_default_departments()

    db.session.commit()
    del registration_requests[email]

    return jsonify({"message": "Super Admin account and default departments created successfully!"}), 201


from app.utils.email_otp import send_login_notice_email

@super_admin_bp.route('/login', methods=['POST'])
def login_super_admin():
    data = request.get_json() or {}
    email = data.get('email')
    password = data.get('password')

    admin = SuperAdmin.query.filter_by(email=email).first()
    if not admin or not admin.is_active or not admin.check_password(password):
        return jsonify({"msg": "Invalid email or password."}), 401

    # Generate OTP
    otp = generate_otp()
    
    # Save to database (deleting any existing pending OTPs for this email first)
    LoginOTP.query.filter_by(email=email).delete()
    login_otp_record = LoginOTP(
        email=email,
        otp=otp,
        role="superadmin",
        claims={"admin_id": admin.id},
        expiry=datetime.utcnow() + timedelta(minutes=30)
    )
    db.session.add(login_otp_record)
    db.session.commit()

    # Log to console for testing/development
    print(f"=== [OTP LOG] SuperAdmin Setup Login '{email}' OTP: {otp} ===")

    # Send OTP email
    send_otp_email(mail, email, otp)

    return jsonify({"otp_required": True, "email": email, "role": "superadmin"}), 200


@super_admin_bp.route('/verify-login-otp', methods=['POST'])
def verify_login_otp():
    data = request.get_json() or {}
    email = data.get('email')
    otp_received = data.get('otp')

    if not email or not otp_received:
        return jsonify({"msg": "Email and OTP are required"}), 400

    pending = LoginOTP.query.filter_by(email=email).order_by(LoginOTP.created_at.desc()).first()
    if not pending:
        return jsonify({"msg": "Invalid session or OTP expired. Please log in again."}), 400

    if datetime.utcnow() > pending.expiry:
        db.session.delete(pending)
        db.session.commit()
        return jsonify({"msg": "OTP has expired. Please log in again."}), 400

    if pending.otp != otp_received:
        return jsonify({"msg": "Invalid verification code"}), 400

    admin_id = None
    if pending.claims:
        admin_id = pending.claims.get('admin_id') or pending.claims.get('id')

    admin = SuperAdmin.query.get(admin_id) if admin_id else None
    if not admin:
        db.session.delete(pending)
        db.session.commit()
        return jsonify({"msg": "Admin user not found"}), 400

    db.session.delete(pending)
    db.session.commit()
    access_token = build_access_token(admin)

    # Send successful login email notice
    timestamp_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    ip_address = request.remote_addr or '127.0.0.1'
    device_str = request.user_agent.string or 'Unknown device'
    send_login_notice_email(mail, email, timestamp_str, ip_address, device_str)

    return jsonify(access_token=access_token), 200


@super_admin_bp.route('/profile', methods=['GET'])
@jwt_required()
def get_super_admin_profile():
    admin = get_current_super_admin()
    if not admin:
        return jsonify({"error": "Admin not found."}), 404
    return jsonify(admin.to_dict() | {"role": "superadmin"}), 200


@super_admin_bp.route('/admins', methods=['GET'])
@jwt_required()
def list_super_admins():
    actor = get_current_super_admin()
    if not actor:
        return jsonify({"error": "Unauthorized actor"}), 401

    admins = SuperAdmin.query.order_by(SuperAdmin.created_at.asc()).all()
    return jsonify([admin.to_dict() for admin in admins]), 200


@super_admin_bp.route('/admins', methods=['POST'])
@jwt_required()
def create_super_admin():
    actor = get_current_super_admin()
    if not actor:
        return jsonify({"error": "Unauthorized actor"}), 401

    data = request.get_json() or {}
    name = data.get('name')
    email = data.get('email')
    password = data.get('password')

    if not name or not email:
        return jsonify({"error": "Name and email are required."}), 400

    if SuperAdmin.query.filter_by(email=email).first():
        return jsonify({"error": "Email already in use"}), 409

    new_admin = SuperAdmin(
        name=name,
        email=email,
        is_active=data.get('is_active', True)
    )
    
    is_invite = False
    if not password:
        import uuid
        password = uuid.uuid4().hex
        is_invite = True

    new_admin.set_password(password)
    db.session.add(new_admin)
    db.session.flush()

    log_activity(actor, f"Created super admin account for '{new_admin.name}'", new_admin)
    
    if is_invite:
        from flask_jwt_extended import create_access_token
        from datetime import timedelta
        from app import mail
        from app.utils.email_otp import send_invite_email
        import os

        # Generate setup token
        setup_token = create_access_token(
            identity=email,
            additional_claims={"purpose": "setup-password", "name": name},
            expires_delta=timedelta(days=7)
        )
        
        frontend_url = os.getenv('FRONTEND_URL', 'http://localhost:5173')
        invite_link = f"{frontend_url}/setup-password?token={setup_token}"
        
        send_invite_email(mail, email, name, invite_link, role_name="superadmin")

    db.session.commit()

    return jsonify(new_admin.to_dict()), 201


@super_admin_bp.route('/admins/<int:admin_id>', methods=['PUT'])
@jwt_required()
def update_super_admin(admin_id):
    actor = get_current_super_admin()
    if not actor:
        return jsonify({"error": "Unauthorized actor"}), 401

    admin = SuperAdmin.query.get_or_404(admin_id)
    data = request.get_json() or {}

    new_email = data.get('email', admin.email)
    if new_email != admin.email and SuperAdmin.query.filter_by(email=new_email).first():
        return jsonify({"error": "Email already in use"}), 409

    admin.name = data.get('name', admin.name)
    admin.email = new_email
    admin.is_active = data.get('is_active', admin.is_active)

    if data.get('password'):
        admin.set_password(data['password'])

    if not admin.is_active:
        active_admins = SuperAdmin.query.filter_by(is_active=True).all()
        if len(active_admins) == 1 and active_admins[0].id == admin.id:
            return jsonify({"error": "At least one active super admin must remain."}), 400

    log_activity(actor, f"Updated super admin account for '{admin.name}'", admin)
    db.session.commit()

    return jsonify(admin.to_dict()), 200


@super_admin_bp.route('/admins/<int:admin_id>', methods=['DELETE'])
@jwt_required()
def delete_super_admin(admin_id):
    actor = get_current_super_admin()
    if not actor:
        return jsonify({"error": "Unauthorized actor"}), 401

    admin = SuperAdmin.query.get_or_404(admin_id)
    if admin.id == actor.id and SuperAdmin.query.count() == 1:
        return jsonify({"error": "You cannot delete the last super admin account."}), 400

    remaining_count = SuperAdmin.query.count()
    if remaining_count == 1:
        return jsonify({"error": "You cannot delete the last super admin account."}), 400

    log_activity(actor, f"Deleted super admin account for '{admin.name}'", admin)
    db.session.delete(admin)
    db.session.commit()

    return jsonify({"message": "Super admin deleted successfully"}), 200
