from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required
from app.models import db
from app.models.lead_model import Lead
from app.models.student_model import Student, Parent
from app.models.financial_model import StudentFinancialAccount
from datetime import date
from app.routes.enrollment_routes import _perform_lead_conversion

student_bp = Blueprint('students', __name__)

@student_bp.route('/from-lead/<int:lead_id>', methods=['POST'])
@jwt_required()
def convert_lead_to_student(lead_id):
    lead = Lead.query.get_or_404(lead_id)
    
    new_student = _perform_lead_conversion(lead)
    if not new_student:
        return jsonify({"error": "This lead has already been converted or is invalid."}), 409

    db.session.commit()
    return jsonify(new_student.to_dict()), 201

@student_bp.route('/', methods=['GET'])
@jwt_required()
def get_all_students():
    students = Student.query.order_by(Student.last_name, Student.first_name).all()
    return jsonify([s.to_dict() for s in students]), 200

@student_bp.route('/<int:student_id>', methods=['GET'])
@jwt_required()
def get_student_by_id(student_id):
    student = Student.query.get_or_404(student_id)
    
    from app.models.activity_log_model import ActivityLog
    from app.models.task_model import Task
    
    # Query logs targeting this student or their associated lead
    logs = ActivityLog.query.filter(
        ((ActivityLog.target_type == 'Student') & (ActivityLog.target_id == student.id)) |
        ((ActivityLog.target_type == 'Lead') & (ActivityLog.target_id == student.lead_id))
    ).order_by(ActivityLog.created_at.desc()).all()
    
    # Query tasks associated with their lead
    tasks = []
    if student.lead_id:
        tasks = Task.query.filter_by(lead_id=student.lead_id).order_by(Task.created_at.desc()).all()
        
    student_dict = student.to_dict()
    student_dict['activity_logs'] = [log.to_dict() for log in logs]
    student_dict['tasks'] = [task.to_dict() for task in tasks]
    
    return jsonify(student_dict), 200