from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required
from app.models.staff_model import Staff
from app.models.department_model import Department
from app.models.lead_model import Lead
from app.models.activity_log_model import ActivityLog # Import ActivityLog

dashboard_bp = Blueprint('dashboard', __name__)

@dashboard_bp.route('/overview', methods=['GET'])
@jwt_required()
def get_overview_data():
    """
    Provides a summary of key metrics for the Super Admin dashboard.
    """
    try:
        total_staff = Staff.query.count()
        total_departments = Department.query.count()
        total_leads = Lead.query.count()
        
        # --- NEW: Fetch recent activities and leads ---
        recent_leads = Lead.query.order_by(Lead.created_at.desc()).limit(5).all()
        recent_activities = ActivityLog.query.order_by(ActivityLog.created_at.desc()).limit(5).all()
        
        # This will be implemented later
        total_students = 0

        data = {
            "total_staff": total_staff,
            "total_students": total_students,
            "total_departments": total_departments,
            "total_leads": total_leads,
            # Add new data to the response
            "recent_leads": [lead.to_dict() for lead in recent_leads],
            "recent_activities": [activity.to_dict() for activity in recent_activities]
        }

        return jsonify(data), 200
    except Exception as e:
        # It's good practice to log the actual error
        print(f"Error fetching dashboard data: {e}")
        return jsonify({"error": "An error occurred while fetching dashboard data."}), 500