from flask import Blueprint, request, jsonify
from app.models import db
from app.models.lead_model import Lead, LeadStudent, LeadParent
from app.models.student_model import Student, Parent # Import permanent models
from app.models.staff_model import Staff
from app.models.department_model import Department
from app.models.activity_log_model import log_activity
from app.utils.notifications import create_notifications_and_send_emails
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime

admissions_bp = Blueprint('admissions', __name__)

@admissions_bp.route('/captcha-challenge', methods=['GET'])
def get_captcha_challenge():
    import random
    from itsdangerous import URLSafeSerializer
    from flask import current_app
    
    a = random.randint(1, 10)
    b = random.randint(1, 10)
    ans = a + b
    
    serializer = URLSafeSerializer(current_app.config['SECRET_KEY'], salt='captcha-salt')
    token = serializer.dumps({"ans": ans})
    
    return jsonify({
        "question": f"Please solve: {a} + {b} = ?",
        "token": token
    }), 200


@admissions_bp.route('/leads', methods=['POST'])
def create_lead():
    data = request.get_json() or {}
    
    # Stateless Captcha Verification
    captcha_token = data.get('captcha_token')
    captcha_answer = data.get('captcha_answer')
    if not captcha_token or not captcha_answer:
        return jsonify({"error": "Captcha verification is required."}), 400
        
    from itsdangerous import URLSafeSerializer
    from flask import current_app
    try:
        serializer = URLSafeSerializer(current_app.config['SECRET_KEY'], salt='captcha-salt')
        decrypted = serializer.loads(captcha_token)
        expected = decrypted.get('ans')
        if int(captcha_answer) != int(expected):
            return jsonify({"error": "Incorrect captcha solution. Please try again."}), 400
    except Exception:
        return jsonify({"error": "Invalid or expired captcha token. Please refresh the page."}), 400

    students_data = data.get('students', [])
    parents_data = data.get('parents', [])
    if not students_data or not parents_data:
        return jsonify({"error": "Student and parent information are required."}), 400
    if not data.get('policy_agreed'):
        return jsonify({"error": "Policy agreement is required."}), 400
        
    new_lead = Lead(policy_agreed=data.get('policy_agreed', False))
    db.session.add(new_lead)
    db.session.flush()

    for student_info in students_data:
        new_student = LeadStudent(
            first_name=student_info['first_name'],
            last_name=student_info['last_name'],
            date_of_birth=datetime.strptime(student_info['date_of_birth'], '%Y-%m-%d').date(),
            city_state=student_info['city_state'],
            grade_level=student_info['grade_level'],
            lead_id=new_lead.id
        )
        db.session.add(new_student)
    for parent_info in parents_data:
        new_parent = LeadParent(
            first_name=parent_info['first_name'],
            last_name=parent_info['last_name'],
            email=parent_info['email'],
            phone=parent_info['phone'],
            lead_id=new_lead.id
        )
        db.session.add(new_parent)

    log_activity(None, "Submitted a new admission application", new_lead)

    admissions_dept = Department.query.filter_by(name="Admission Department").first()
    recipients = []
    if admissions_dept and admissions_dept.staff_members:
        recipients = admissions_dept.staff_members
    else:
        from app.models.super_admin_model import SuperAdmin
        recipients = SuperAdmin.query.all()

    if recipients:
        student_name = f"{students_data[0]['first_name']} {students_data[0]['last_name']}"
        message = f"A new admission application has been submitted for {student_name}."
        create_notifications_and_send_emails(
            recipients=recipients,
            message=message,
            target_obj=new_lead
        )

    db.session.commit()
    return jsonify(new_lead.to_dict()), 201


@admissions_bp.route('/live-look-in', methods=['POST'])
def create_live_look_in():
    import json
    from app.models.board_model import Board, BoardGroup, BoardTask
    from app.models.board_model_extensions import BoardCustomField, TaskCustomFieldValue
    
    data = request.get_json() or {}
    
    # Stateless Captcha Verification
    captcha_token = data.get('captcha_token')
    captcha_answer = data.get('captcha_answer')
    if not captcha_token or not captcha_answer:
        return jsonify({"error": "Captcha verification is required."}), 400
        
    from itsdangerous import URLSafeSerializer
    from flask import current_app
    try:
        serializer = URLSafeSerializer(current_app.config['SECRET_KEY'], salt='captcha-salt')
        decrypted = serializer.loads(captcha_token)
        expected = decrypted.get('ans')
        if int(captcha_answer) != int(expected):
            return jsonify({"error": "Incorrect captcha solution. Please try again."}), 400
    except Exception:
        return jsonify({"error": "Invalid or expired captcha token. Please refresh the page."}), 400

    name = data.get('name')
    email = data.get('email')
    phone = data.get('phone')
    grade = data.get('grade')
    message = data.get('message')
    
    if not name or not email:
        return jsonify({"error": "Name and Email are required."}), 400
        
    # Dynamic board mapping: Query param board_id, space, or body board_id
    board_id_val = request.args.get('board_id') or request.args.get('space') or data.get('board_id')
    board = None
    if board_id_val:
        try:
            board = Board.query.get(int(board_id_val))
        except Exception:
            pass
            
    if not board:
        # Find board/space named "Live Look-in" (case-insensitive)
        board = Board.query.filter(Board.name.ilike('%Live Look-in%')).first()
        
    if not board:
        # Fallback: use first available board
        board = Board.query.first()
        
    if not board:
        return jsonify({"error": "No workspace space found to receive submissions. Please create a space in the workspace first."}), 400
        
    # Get or create the first group
    group = BoardGroup.query.filter_by(board_id=board.id).order_by(BoardGroup.position.asc()).first()
    if not group:
        group = BoardGroup(board_id=board.id, name="Submissions", color="#fdab3d", position=0)
        db.session.add(group)
        db.session.flush()
        
    # Ensure custom fields exist: "Phone Number" (phone), "Grade" (text)
    phone_field = BoardCustomField.query.filter_by(board_id=board.id, name="Phone Number").first()
    if not phone_field:
        phone_field = BoardCustomField(board_id=board.id, name="Phone Number", type="phone")
        db.session.add(phone_field)
        db.session.flush()
        
    grade_field = BoardCustomField.query.filter_by(board_id=board.id, name="Grade").first()
    if not grade_field:
        grade_field = BoardCustomField(board_id=board.id, name="Grade", type="text")
        db.session.add(grade_field)
        db.session.flush()

    email_field = BoardCustomField.query.filter_by(board_id=board.id, name="Email").first()
    if not email_field:
        email_field = BoardCustomField(board_id=board.id, name="Email", type="email")
        db.session.add(email_field)
        db.session.flush()
        
    # Create the task
    task_notes = f"""<h3>📋 Live Look-in Schedule Request</h3>
<ul>
  <li><strong>Name:</strong> {name}</li>
  <li><strong>Email:</strong> {email}</li>
  <li><strong>Phone Number:</strong> {phone or '-'}</li>
  <li><strong>Grade:</strong> {grade or '-'}</li>
  <li><strong>Message:</strong> {message or '-'}</li>
</ul>"""

    task = BoardTask(
        group_id=group.id,
        title=f"Live Look-in Request: {name}",
        status="Not Started",
        priority="Normal",
        notes=task_notes,
        submitter_email=email
    )
    db.session.add(task)
    db.session.flush()
    
    # Save custom field values
    if phone:
        db.session.add(TaskCustomFieldValue(task_id=task.id, field_id=phone_field.id, value_json=json.dumps(phone)))
    if grade:
        db.session.add(TaskCustomFieldValue(task_id=task.id, field_id=grade_field.id, value_json=json.dumps(grade)))
    if email:
        db.session.add(TaskCustomFieldValue(task_id=task.id, field_id=email_field.id, value_json=json.dumps(email)))
        
    log_activity(None, f"Submitted a new Live Look-in request (Task: {task.title})", task)
    
    # Notify admissions department staff (and fallback to super admins)
    recipients = []
    admissions_dept = Department.query.filter_by(name="Admission Department").first()
    if admissions_dept and admissions_dept.staff_members:
        recipients = admissions_dept.staff_members
    else:
        from app.models.super_admin_model import SuperAdmin
        recipients = SuperAdmin.query.all()
        
    if recipients:
        notif_msg = f"📋 New Live Look-in request submitted by {name} ({email}) for Grade {grade or '-'}."
        create_notifications_and_send_emails(
            recipients=recipients,
            message=notif_msg,
            target_obj=task
        )
        
    db.session.commit()
    return jsonify({"success": True, "task_id": task.id}), 201

@admissions_bp.route('/leads', methods=['GET'])
@jwt_required()
def get_all_leads():
    leads = Lead.query.order_by(Lead.created_at.desc()).all()
    return jsonify([lead.to_dict() for lead in leads]), 200

@admissions_bp.route('/leads/<string:token>', methods=['GET'])
@jwt_required()
def get_lead_by_token(token):
    lead = Lead.query.filter_by(secure_token=token).first_or_404()
    return jsonify(lead.to_dict()), 200

@admissions_bp.route('/leads/<string:token>/details', methods=['PUT'])
@jwt_required()
def update_lead_details(token):
    current_user_email = get_jwt_identity()
    actor = Staff.query.filter_by(email=current_user_email).first()
    if not actor:
        from app.models.super_admin_model import SuperAdmin
        actor = SuperAdmin.query.filter_by(email=current_user_email).first()
    if not actor:
        return jsonify({"error": "Unauthorized actor"}), 401

    lead = Lead.query.filter_by(secure_token=token).first_or_404()
    data = request.get_json()

    # --- THIS IS THE NEW SYNC LOGIC ---
    # Find the permanent student record, if it exists
    permanent_student = Student.query.filter_by(lead_id=lead.id).first()
    # --- END OF NEW LOGIC ---

    students_data = data.get('students', [])
    for s_data in students_data:
        lead_student = LeadStudent.query.get(s_data['id'])
        if lead_student and lead_student.lead_id == lead.id:
            lead_student.first_name = s_data['first_name']
            lead_student.last_name = s_data['last_name']
            dob_str = s_data['date_of_birth'].split('T')[0]
            lead_student.date_of_birth = datetime.strptime(dob_str, '%Y-%m-%d').date()
            # city_state is not on the permanent student model, so we don't sync it
            lead_student.grade_level = s_data['grade_level']

            # --- SYNC ACTION ---
            if permanent_student:
                permanent_student.first_name = s_data['first_name']
                permanent_student.last_name = s_data['last_name']
                permanent_student.date_of_birth = datetime.strptime(dob_str, '%Y-%m-%d').date()
                permanent_student.grade_level = s_data['grade_level']
            # --- END SYNC ACTION ---

    parents_data = data.get('parents', [])
    for p_data in parents_data:
        lead_parent = LeadParent.query.get(p_data['id'])
        if lead_parent and lead_parent.lead_id == lead.id:
            lead_parent.first_name = p_data['first_name']
            lead_parent.last_name = p_data['last_name']
            lead_parent.email = p_data['email']
            lead_parent.phone = p_data['phone']
            
            # --- SYNC ACTION ---
            # Find and update the permanent parent record
            if permanent_student and permanent_student.parents:
                permanent_parent = permanent_student.parents[0] # Assuming one parent for simplicity
                if permanent_parent:
                    permanent_parent.first_name = p_data['first_name']
                    permanent_parent.last_name = p_data['last_name']
                    permanent_parent.email = p_data['email']
                    permanent_parent.phone = p_data['phone']
            # --- END SYNC ACTION ---
    
    log_activity(actor, "Updated lead details (student/parent info)", lead)

    db.session.commit()
    return jsonify(lead.to_dict()), 200

@admissions_bp.route('/leads/<string:token>', methods=['PUT'])
@jwt_required()
def update_lead(token):
    current_user_email = get_jwt_identity()
    actor = Staff.query.filter_by(email=current_user_email).first()
    if not actor:
        from app.models.super_admin_model import SuperAdmin
        actor = SuperAdmin.query.filter_by(email=current_user_email).first()
    if not actor:
        return jsonify({"error": "Unauthorized actor"}), 401
        
    lead = Lead.query.filter_by(secure_token=token).first_or_404()
    data = request.get_json()

    log_message = "Updated lead"
    if 'status' in data:
        log_message += f" status to '{data['status']}'"
    if 'internal_notes' in data:
        log_message += " and updated internal notes"
        
    log_activity(actor, log_message, lead)

    if 'status' in data:
        new_status = data['status']
        lead.status = new_status
        if new_status in ['Admitted', 'Enrolled']:
            from app.models.student_model import Student
            existing_student = Student.query.filter_by(lead_id=lead.id).first()
            if not existing_student:
                from app.routes.enrollment_routes import _perform_lead_conversion
                _perform_lead_conversion(lead)
                # Preserve selected status if Admitted
                lead.status = new_status
    if 'internal_notes' in data:
        lead.internal_notes = data['internal_notes']
        
    db.session.commit()
    return jsonify(lead.to_dict()), 200