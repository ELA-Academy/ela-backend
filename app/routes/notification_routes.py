from flask import Blueprint, jsonify, request
from app.models import db
from app.models.staff_model import Staff
from app.models.super_admin_model import SuperAdmin
from app.models.notification_model import Notification
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt

notification_bp = Blueprint('notifications', __name__)

@notification_bp.route('', methods=['GET'])
@jwt_required()
def get_notifications():
    claims = get_jwt()
    role = claims.get('role')
    current_user_email = get_jwt_identity()
    
    query = Notification.query
    
    if role == 'superadmin':
        admin = SuperAdmin.query.filter_by(email=current_user_email).first()
        if not admin:
            return jsonify({"error": "Super Admin not found"}), 404
        query = query.filter_by(super_admin_id=admin.id)
    elif role == 'staff':
        staff = Staff.query.filter_by(email=current_user_email).first()
        if not staff:
            return jsonify({"error": "Staff member not found"}), 404
        query = query.filter_by(staff_id=staff.id)
    else:
        return jsonify([]), 200
        
    unread_only = request.args.get('unread_only', 'false') == 'true'
    category = request.args.get('category') # 'all', 'mention', 'assignment'
    
    if unread_only:
        query = query.filter_by(is_read=False)
        
    if category and category != 'all':
        query = query.filter_by(category=category)
        
    notifications = query.order_by(Notification.created_at.desc()).limit(100).all()
    return jsonify([n.to_dict() for n in notifications]), 200

@notification_bp.route('/mark-all-as-read', methods=['POST'])
@jwt_required()
def mark_all_as_read():
    claims = get_jwt()
    role = claims.get('role')
    current_user_email = get_jwt_identity()
    
    if role == 'superadmin':
        admin = SuperAdmin.query.filter_by(email=current_user_email).first()
        if not admin:
            return jsonify({"error": "Super Admin not found"}), 404
        Notification.query.filter_by(super_admin_id=admin.id, is_read=False).update({'is_read': True})
    elif role == 'staff':
        staff = Staff.query.filter_by(email=current_user_email).first()
        if not staff:
            return jsonify({"error": "Staff member not found"}), 404
        Notification.query.filter_by(staff_id=staff.id, is_read=False).update({'is_read': True})
    else:
        return jsonify({"message": "No notifications to mark"}), 200

    db.session.commit()
    return jsonify({"message": "All notifications marked as read"}), 200

@notification_bp.route('/<int:notification_id>/read', methods=['POST'])
@jwt_required()
def mark_as_read(notification_id):
    claims = get_jwt()
    role = claims.get('role')
    current_user_email = get_jwt_identity()
    
    notification = Notification.query.get(notification_id)
    if not notification:
        return jsonify({"error": "Notification not found"}), 404
        
    if role == 'superadmin':
        admin = SuperAdmin.query.filter_by(email=current_user_email).first()
        if not admin or notification.super_admin_id != admin.id:
            return jsonify({"error": "Unauthorized"}), 401
    elif role == 'staff':
        staff = Staff.query.filter_by(email=current_user_email).first()
        if not staff or notification.staff_id != staff.id:
            return jsonify({"error": "Unauthorized"}), 401
    else:
        return jsonify({"error": "Unauthorized"}), 401

    notification.is_read = True
    db.session.commit()
    return jsonify(notification.to_dict()), 200