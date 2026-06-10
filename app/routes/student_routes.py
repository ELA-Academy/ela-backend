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
    students = Student.query.filter_by(status='Active').order_by(Student.last_name, Student.first_name).all()
    return jsonify([s.to_dict() for s in students]), 200

@student_bp.route('/<int:student_id>', methods=['GET'])
@jwt_required()
def get_student_by_id(student_id):
    student = Student.query.get_or_404(student_id)
    return jsonify(student.to_dict()), 200