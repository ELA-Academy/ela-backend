from flask import Blueprint, jsonify, request
from flask_jwt_extended import create_access_token
from app.models import db
from app.models.staff_model import Staff
from app.models.super_admin_model import SuperAdmin
from app.models.login_otp_model import LoginOTP

from app import mail
from app.utils.email_otp import generate_otp, send_otp_email, send_login_notice_email, send_password_reset_email
from datetime import datetime, timedelta, timezone
from flask import current_app
import os

auth_bp = Blueprint('auth', __name__)

from app.models.login_otp_model import LoginOTP, RememberedDevice

def check_remembered_device(email, device_id):
    if not email or not device_id:
        return False
    record = RememberedDevice.query.filter(
        db.func.lower(RememberedDevice.email) == db.func.lower(email.strip()),
        RememberedDevice.device_id == str(device_id).strip()
    ).first()
    if record:
        if datetime.utcnow() < record.expires_at:
            record.expires_at = datetime.utcnow() + timedelta(days=30)
            db.session.commit()
            return True
        else:
            db.session.delete(record)
            db.session.commit()
    return False

def register_remembered_device(email, device_id, req):
    if not email or not device_id:
        return
    clean_email = email.strip().lower()
    clean_device_id = str(device_id).strip()
    record = RememberedDevice.query.filter(
        db.func.lower(RememberedDevice.email) == clean_email,
        RememberedDevice.device_id == clean_device_id
    ).first()
    if not record:
        record = RememberedDevice(
            email=clean_email,
            device_id=clean_device_id,
            user_agent=req.user_agent.string[:500] if req.user_agent else None,
            ip_address=req.remote_addr,
            expires_at=datetime.utcnow() + timedelta(days=30)
        )
        db.session.add(record)
    else:
        record.expires_at = datetime.utcnow() + timedelta(days=30)
        record.user_agent = req.user_agent.string[:500] if req.user_agent else None
        record.ip_address = req.remote_addr
    db.session.commit()

@auth_bp.route('/login', methods=['POST'])
def login():
    data = request.get_json() or {}
    email = (data.get('email') or '').strip()
    password = data.get('password')
    device_id = data.get('device_id')

    if not email or not password:
        return jsonify({"msg": "Email and password are required"}), 400

    # 1. SuperAdmin check takes precedence over Staff
    admin_member = SuperAdmin.query.filter(db.func.lower(SuperAdmin.email) == db.func.lower(email)).first()

    if admin_member:
        if hasattr(admin_member, 'is_active') and admin_member.is_active is False:
            print(f"[LOGIN FAIL] SuperAdmin '{email}' account is inactive (is_active=False)")
            return jsonify({"msg": "Account is inactive. Please contact support."}), 401

        if not admin_member.check_password(password):
            print(f"[LOGIN FAIL] Password mismatch for SuperAdmin '{email}'")
            return jsonify({"msg": "Invalid credentials"}), 401

        # Deactivate any duplicate Staff account with the same email to prevent identity conflict
        dup_staff = Staff.query.filter(db.func.lower(Staff.email) == db.func.lower(email)).first()
        if dup_staff and getattr(dup_staff, 'is_active', True):
            dup_staff.is_active = False
            db.session.commit()

        additional_claims = {
            "id": admin_member.id,
            "role": "superadmin",
            "name": admin_member.name
        }

        # Check 30-day Remembered Device
        if device_id and check_remembered_device(email, device_id):
            print(f"=== [DEVICE LOG] SuperAdmin '{email}' device remembered. Bypassing OTP. ===")
            access_token = create_access_token(identity=admin_member.email, additional_claims=additional_claims)
            timestamp_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            ip_address = request.remote_addr or '127.0.0.1'
            device_str = request.user_agent.string or 'Unknown device'
            send_login_notice_email(mail, admin_member.email, timestamp_str, ip_address, device_str)
            return jsonify(access_token=access_token), 200

        # Generate OTP
        otp = generate_otp()
        LoginOTP.query.filter(db.func.lower(LoginOTP.email) == db.func.lower(email)).delete()
        login_otp_record = LoginOTP(
            email=admin_member.email,
            otp=otp,
            role="superadmin",
            claims=additional_claims,
            expiry=datetime.utcnow() + timedelta(minutes=10)
        )
        db.session.add(login_otp_record)
        db.session.commit()

        print(f"=== [OTP LOG] SuperAdmin '{admin_member.email}' OTP: {otp} ===")
        send_otp_email(mail, admin_member.email, otp)

        return jsonify({"otp_required": True, "email": admin_member.email, "role": "superadmin"}), 200

    # 2. Staff check
    staff_member = Staff.query.filter(db.func.lower(Staff.email) == db.func.lower(email)).first()

    if staff_member:
        if hasattr(staff_member, 'is_active') and staff_member.is_active is False:
            print(f"[LOGIN FAIL] Staff '{email}' account is inactive (is_active=False)")
            return jsonify({"msg": "Account is inactive. Please contact support."}), 401

        if not staff_member.check_password(password):
            print(f"[LOGIN FAIL] Password mismatch for Staff '{email}'")
            return jsonify({"msg": "Invalid credentials"}), 401

        additional_claims = {
            "id": staff_member.id,
            "name": staff_member.name,
            "departmentNames": [d.name for d in staff_member.departments],
            "dashboardRoutes": [d.dashboard_route for d in staff_member.departments if d.dashboard_route],
            "role": "staff"
        }

        # Check 30-day Remembered Device
        if device_id and check_remembered_device(email, device_id):
            print(f"=== [DEVICE LOG] Staff '{email}' device remembered. Bypassing OTP. ===")
            access_token = create_access_token(identity=staff_member.email, additional_claims=additional_claims)
            timestamp_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            ip_address = request.remote_addr or '127.0.0.1'
            device_str = request.user_agent.string or 'Unknown device'
            send_login_notice_email(mail, staff_member.email, timestamp_str, ip_address, device_str)
            return jsonify(access_token=access_token), 200

        # Generate OTP
        otp = generate_otp()
        LoginOTP.query.filter(db.func.lower(LoginOTP.email) == db.func.lower(email)).delete()
        login_otp_record = LoginOTP(
            email=staff_member.email,
            otp=otp,
            role="staff",
            claims=additional_claims,
            expiry=datetime.utcnow() + timedelta(minutes=10)
        )
        db.session.add(login_otp_record)
        db.session.commit()

        print(f"=== [OTP LOG] Staff '{staff_member.email}' OTP: {otp} ===")
        send_otp_email(mail, staff_member.email, otp)

        return jsonify({"otp_required": True, "email": staff_member.email, "role": "staff"}), 200

    print(f"[LOGIN FAIL] No user found with email '{email}'")
    return jsonify({"msg": "Invalid credentials"}), 401


@auth_bp.route('/verify-login-otp', methods=['POST'])
def verify_login_otp():
    data = request.get_json() or {}
    email = (data.get('email') or '').strip()
    otp_received = (data.get('otp') or '').strip()
    device_id = data.get('device_id')
    remember_device = data.get('remember_device', True)

    if not email or not otp_received:
        return jsonify({"msg": "Email and OTP are required"}), 400

    pending = LoginOTP.query.filter(db.func.lower(LoginOTP.email) == db.func.lower(email)).order_by(LoginOTP.created_at.desc()).first()
    if not pending:
        return jsonify({"msg": "Invalid session or OTP expired. Please log in again."}), 400

    if datetime.utcnow() > pending.expiry:
        db.session.delete(pending)
        db.session.commit()
        return jsonify({"msg": "OTP has expired. Please log in again."}), 400

    if pending.otp.strip() != otp_received:
        return jsonify({"msg": "Invalid verification code"}), 400

    role = pending.role
    claims = pending.claims
    db.session.delete(pending)
    db.session.commit()

    access_token = create_access_token(identity=email, additional_claims=claims)

    if remember_device and device_id:
        register_remembered_device(email, device_id, request)

    # Send successful login email notice
    timestamp_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    ip_address = request.remote_addr or '127.0.0.1'
    device_str = request.user_agent.string or 'Unknown device'
    send_login_notice_email(mail, email, timestamp_str, ip_address, device_str)

    return jsonify(access_token=access_token), 200

@auth_bp.route('/verify-setup-token', methods=['POST'])
def verify_setup_token():
    data = request.get_json() or {}
    token = data.get('token')
    if not token:
        return jsonify({"error": "Token is required"}), 400
    
    from flask_jwt_extended import decode_token
    from app.models.used_token_model import UsedToken
    try:
        decoded = decode_token(token)
        jti = decoded.get('jti')
        if jti and UsedToken.query.filter_by(token_jti=jti).first():
            return jsonify({"error": "Token has already been used", "valid": False}), 400
            
        purpose = decoded.get('purpose')
        if purpose not in {'setup-password', 'reset-password'}:
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
    from app.models.used_token_model import UsedToken
    try:
        decoded = decode_token(token)
        jti = decoded.get('jti')
        if jti and UsedToken.query.filter_by(token_jti=jti).first():
            return jsonify({"error": "Token has already been used"}), 400
            
        purpose = decoded.get('purpose')
        if purpose not in {'setup-password', 'reset-password'}:
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
        
        if jti:
            db.session.add(UsedToken(token_jti=jti))
        db.session.commit()
        return jsonify({"message": "Password updated successfully! You can now log in."}), 200
    except Exception as e:
        return jsonify({"error": "Invalid or expired token"}), 400

@auth_bp.route('/forgot-password', methods=['POST'])
def forgot_password():
    data = request.get_json() or {}
    email = data.get('email')
    if not email:
        return jsonify({"error": "Email is required"}), 400

    user = Staff.query.filter_by(email=email).first()
    role = 'staff'
    if not user:
        user = SuperAdmin.query.filter_by(email=email).first()
        role = 'superadmin'

    if not user:
        # Avoid user enumeration for security, return 200
        return jsonify({"message": "If the email is registered, a password reset link has been sent."}), 200

    expires = timedelta(hours=1)
    reset_token = create_access_token(
        identity=email,
        expires_delta=expires,
        additional_claims={'purpose': 'reset-password', 'role': role, 'name': user.name}
    )

    frontend_url = current_app.config.get('FRONTEND_URL', 'http://localhost:5173')
    frontend_url = os.getenv('FRONTEND_URL', frontend_url)

    reset_link = f"{frontend_url}/setup-password?token={reset_token}"

    send_password_reset_email(mail, email, user.name, reset_link)
    
    print(f"=== [PASSWORD RESET LOG] User '{email}' reset link: {reset_link} ===")

    return jsonify({"message": "Password reset link has been sent to your email."}), 200