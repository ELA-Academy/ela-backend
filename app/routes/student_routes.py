from flask import Blueprint, jsonify, request, current_app, send_from_directory
from flask_jwt_extended import jwt_required
from app.models import db
from app.models.lead_model import Lead
from app.models.student_model import Student, Parent
from app.models.student_document_model import StudentDocument
from datetime import date, datetime
from app.routes.enrollment_routes import _perform_lead_conversion
import os
import uuid
from werkzeug.utils import secure_filename

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
    
    logs = ActivityLog.query.filter(
        ((ActivityLog.target_type == 'Student') & (ActivityLog.target_id == student.id)) |
        ((ActivityLog.target_type == 'Lead') & (ActivityLog.target_id == student.lead_id))
    ).order_by(ActivityLog.created_at.desc()).all()
    
    tasks = []
    if student.lead_id:
        tasks = Task.query.filter_by(lead_id=student.lead_id).order_by(Task.created_at.desc()).all()
        
    student_dict = student.to_dict()
    student_dict['activity_logs'] = [log.to_dict() for log in logs]
    student_dict['tasks'] = [task.to_dict() for task in tasks]
    
    return jsonify(student_dict), 200


# --- STUDENT PROFILE DOCUMENTS ROUTES ---

@student_bp.route('/<int:student_id>/documents', methods=['GET'])
@jwt_required()
def get_student_documents(student_id):
    student = Student.query.get_or_404(student_id)
    docs = StudentDocument.query.filter_by(student_id=student.id).order_by(StudentDocument.created_at.desc()).all()
    return jsonify([d.to_dict() for d in docs]), 200

@student_bp.route('/<int:student_id>/documents', methods=['POST'])
@jwt_required()
def upload_student_document(student_id):
    student = Student.query.get_or_404(student_id)
    
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded."}), 400
        
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file."}), 400
        
    name = request.form.get('name')
    expiry_date_str = request.form.get('expiry_date')
    doc_type = request.form.get('document_type', 'Document')
    
    expiry_date = None
    if expiry_date_str:
        try:
            expiry_date = datetime.strptime(expiry_date_str, "%Y-%m-%d").date()
        except ValueError:
            pass

    # Save file safely on disk
    filename = secure_filename(file.filename)
    unique_filename = f"{uuid.uuid4().hex}_{filename}"
    
    docs_dir = os.path.join(current_app.root_path, 'static', 'documents')
    os.makedirs(docs_dir, exist_ok=True)
    
    file.save(os.path.join(docs_dir, unique_filename))
    file_url = f"/api/students/documents/download/{unique_filename}"
    
    # Save database entry
    doc = StudentDocument(
        student_id=student.id,
        name=name or filename,
        file_path=file_url,
        expiry_date=expiry_date,
        document_type=doc_type,
        status="UPLOADED"
    )
    db.session.add(doc)
    db.session.commit()
    
    return jsonify(doc.to_dict()), 201

@student_bp.route('/documents/<int:doc_id>', methods=['DELETE'])
@jwt_required()
def delete_student_document(doc_id):
    doc = StudentDocument.query.get_or_404(doc_id)
    
    # Remove file from disk if present
    filename = doc.file_path.split('/')[-1]
    file_path = os.path.join(current_app.root_path, 'static', 'documents', filename)
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
        except Exception as e:
            current_app.logger.warning(f"Failed to delete file from disk: {e}")
            
    db.session.delete(doc)
    db.session.commit()
    return jsonify({"message": "Document deleted successfully."}), 200

@student_bp.route('/documents/download/<path:filename>', methods=['GET'])
def download_student_document(filename):
    docs_dir = os.path.join(current_app.root_path, 'static', 'documents')
    return send_from_directory(docs_dir, filename, as_attachment=True)