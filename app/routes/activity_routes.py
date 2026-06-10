from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required
from app.models.activity_log_model import ActivityLog

activity_bp = Blueprint('activity', __name__)

@activity_bp.route('/logs', methods=['GET'])
@jwt_required()
def get_all_logs():
    """
    Fetches all activity logs, ordered by most recent first.
    In a production app, you would add pagination here.
    """
    try:
        # Future enhancement: Add role check to ensure only superadmins can access
        logs = ActivityLog.query.order_by(ActivityLog.created_at.desc()).all()
        return jsonify([log.to_dict() for log in logs]), 200
    except Exception as e:
        print(f"Error fetching activity logs: {e}")
        return jsonify({"error": "An error occurred while fetching activity logs."}), 500