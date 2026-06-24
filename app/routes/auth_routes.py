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

@auth_bp.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    email = data.get('email')
    password = data.get('password')

    if not email or not password:
        return jsonify({"msg": "Email and password are required"}), 400

    staff_member = Staff.query.filter_by(email=email).first()

    if staff_member:
        if not staff_member.is_active or not staff_member.check_password(password):
            return jsonify({"msg": "Invalid credentials"}), 401

        additional_claims = {
            "id": staff_member.id,
            "name": staff_member.name,
            "departmentNames": [d.name for d in staff_member.departments],
            "dashboardRoutes": [d.dashboard_route for d in staff_member.departments if d.dashboard_route],
            "role": "staff"
        }
        
        # Generate OTP
        otp = generate_otp()
        
        # Save to database (deleting any existing pending OTPs for this email first)
        LoginOTP.query.filter_by(email=email).delete()
        login_otp_record = LoginOTP(
            email=email,
            otp=otp,
            role="staff",
            claims=additional_claims,
            expiry=datetime.utcnow() + timedelta(minutes=30)
        )
        db.session.add(login_otp_record)
        db.session.commit()
        
        # Log to console for testing/development
        print(f"=== [OTP LOG] Staff '{email}' OTP: {otp} ===")
        
        # Send OTP email
        send_otp_email(mail, email, otp)
        
        return jsonify({"otp_required": True, "email": email, "role": "staff"}), 200
 
    admin_member = SuperAdmin.query.filter_by(email=email).first()
 
    if admin_member:
        if not admin_member.is_active or not admin_member.check_password(password):
            return jsonify({"msg": "Invalid credentials"}), 401
 
        additional_claims = {
            "id": admin_member.id,
            "role": "superadmin",
            "name": admin_member.name
        }
        
        # Generate OTP
        otp = generate_otp()
        
        # Save to database (deleting any existing pending OTPs for this email first)
        LoginOTP.query.filter_by(email=email).delete()
        login_otp_record = LoginOTP(
            email=email,
            otp=otp,
            role="superadmin",
            claims=additional_claims,
            expiry=datetime.utcnow() + timedelta(minutes=30)
        )
        db.session.add(login_otp_record)
        db.session.commit()
        
        # Log to console for testing/development
        print(f"=== [OTP LOG] SuperAdmin '{email}' OTP: {otp} ===")
        
        # Send OTP email
        send_otp_email(mail, email, otp)
        
        return jsonify({"otp_required": True, "email": email, "role": "superadmin"}), 200
 
    return jsonify({"msg": "Invalid credentials"}), 401
 
 
@auth_bp.route('/verify-login-otp', methods=['POST'])
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

    role = pending.role
    claims = pending.claims
    db.session.delete(pending)
    db.session.commit()

    access_token = create_access_token(identity=email, additional_claims=claims)

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
    try:
        decoded = decode_token(token)
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
    try:
        decoded = decode_token(token)
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