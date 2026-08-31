import os
from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt
from app.models import db
from app.models.department_model import Department
from app.models.enrollment_form_model import EnrollmentForm
from app.models.enrollment_submission_model import EnrollmentSubmission
from app.models.lead_model import Lead
from app.models.student_model import Student, Parent
from app.models.financial_model import StudentFinancialAccount
from app.models.staff_model import Staff
from app.models.super_admin_model import SuperAdmin
from app.models.activity_log_model import log_activity
from app.utils.notifications import send_email_in_background, create_notifications_and_send_emails
from datetime import datetime, date

enrollment_bp = Blueprint('enrollment', __name__)

def _perform_lead_conversion(lead):
    """Converts a Lead to a permanent Student and Parent, and creates a financial account."""
    if not lead or Student.query.filter_by(lead_id=lead.id).first():
        return None # Already converted or invalid lead

    lead_student_info = lead.students[0]

    parents_to_link = []
    for lead_parent_info in lead.parents:
        parent = Parent.query.filter(Parent.email.ilike(lead_parent_info.email.strip())).first()
        if not parent:
            parent = Parent(
                first_name=lead_parent_info.first_name,
                last_name=lead_parent_info.last_name,
                email=lead_parent_info.email.strip().lower(),
                phone=lead_parent_info.phone or "N/A",
                is_active=True,
                sign_in_pin="2963"
            )
            db.session.add(parent)
        parents_to_link.append(parent)

    new_student = Student(
        first_name=lead_student_info.first_name,
        last_name=lead_student_info.last_name,
        date_of_birth=lead_student_info.date_of_birth,
        grade_level=lead_student_info.grade_level,
        enrollment_date=date.today(),
        lead_id=lead.id
    )
    for p in parents_to_link:
        new_student.parents.append(p)
    db.session.add(new_student)
    
    financial_account = StudentFinancialAccount(student=new_student)
    db.session.add(financial_account)
    
    lead.status = "Enrolled"
    return new_student

def _send_enrollment_email(submission):
    """Helper function to send the enrollment email to the parent."""
    if not submission.lead.parents:
        return False
    
    parent = submission.lead.parents[0]
    student_name = submission.lead.students[0].first_name if submission.lead.students else "your child"
    frontend_url = os.getenv('FRONTEND_URL', 'http://localhost:5173')
    action_link = f"{frontend_url}/enrollment/{submission.secure_token}"

    email_data = {
        'message': f"Please complete the enrollment process for {student_name}. Click the button below to access the enrollment form.",
        'action_link': action_link
    }
    
    send_email_in_background(
        subject=f"Complete Your Enrollment for {student_name}",
        recipients=[parent.email],
        template_data=email_data
    )
    return True

# --- PUBLIC ROUTES (NO AUTH) ---

@enrollment_bp.route('/public/submission/<string:token>', methods=['GET'])
def get_public_submission(token):
    """Fetches a submission, its form structure, and pre-population data."""
    submission = EnrollmentSubmission.query.filter_by(secure_token=token).first_or_404()
    
    if submission.form.status != 'Active':
        return jsonify({"error": "This enrollment form is not currently active."}), 403

    if submission.status in ['Submitted', 'Completed']:
         return jsonify({"error": "This form has already been submitted."}), 403

    # --- Prepopulate LOGIC ---
    prefill_data = {
        "students": [s.to_dict() for s in submission.lead.students],
        "parents": [p.to_dict() for p in submission.lead.parents]
    }

    return jsonify({
        "submission_id": submission.id,
        "form_structure": submission.form.form_structure_json,
        "fee_required": submission.form.collect_fee,
        "fee_amount": submission.form.fee_amount,
        "student_name": f"{submission.lead.students[0].first_name} {submission.lead.students[0].last_name}" if submission.lead.students else "N/A",
        "prefill_data": prefill_data # Add the data to the response
    }), 200

@enrollment_bp.route('/public/submission/<string:token>', methods=['POST'])
def submit_public_form(token):
    submission = EnrollmentSubmission.query.filter_by(secure_token=token).first_or_404()
    if submission.status in ['Submitted', 'Completed']:
         return jsonify({"error": "This form has already been submitted."}), 403
    data = request.get_json()
    submission.responses_json = data.get('responses')
    submission.status = 'Submitted'
    submission.submitted_at = datetime.utcnow()
    
    student_name = "A student"
    if submission.lead and submission.lead.students:
        student_name = f"{submission.lead.students[0].first_name} {submission.lead.students[0].last_name}"
    
    if submission.form.collect_fee:
        submission.payment_status = 'Paid'
        submission.status = 'Completed'

    # --- AUTOMATIC CONVERSION & STUDENT DOCUMENT SAVE ---
    target_student = _perform_lead_conversion(submission.lead)
    if not target_student and submission.lead:
        target_student = Student.query.filter_by(lead_id=submission.lead.id).first()

    if target_student:
        from app.models.student_document_model import StudentDocument
        doc_name = f"{target_student.first_name} {target_student.last_name} - {submission.form.name}"
        file_url = f"/api/enrollment/submission/{submission.secure_token}/pdf"
        
        existing_doc = StudentDocument.query.filter_by(student_id=target_student.id, name=doc_name).first()
        if not existing_doc:
            doc = StudentDocument(
                student_id=target_student.id,
                name=doc_name,
                file_path=file_url,
                document_type="Document",
                status="UPLOADED"
            )
            db.session.add(doc)
        else:
            existing_doc.file_path = file_url
    # --- END AUTOMATIC CONVERSION & STUDENT DOCUMENT SAVE ---

    accounting_dept = Department.query.filter_by(name="Accounting Department").first()
    if accounting_dept and accounting_dept.staff_members:
        message = f"An enrollment form for {student_name} has been submitted."
        if submission.payment_status == 'Paid': message += " Payment has been confirmed."
        create_notifications_and_send_emails(recipients=accounting_dept.staff_members, message=message, target_obj=submission.lead)
    
    log_activity(None, f"Parent submitted enrollment for {student_name}", submission.lead)
    db.session.commit()
    return jsonify({"message": "Your submission was successful!"}), 200


@enrollment_bp.route('/public/submission/<string:token>/view', methods=['GET'])
def get_public_submission_view(token):
    submission = EnrollmentSubmission.query.filter_by(secure_token=token).first_or_404()
    return jsonify({
        "id": submission.id,
        "form_name": submission.form.name,
        "form_structure": submission.form.form_structure_json,
        "responses": submission.responses_json,
        "status": submission.status,
        "submitted_at": submission.submitted_at.isoformat() if submission.submitted_at else None,
        "student_name": f"{submission.lead.students[0].first_name} {submission.lead.students[0].last_name}" if submission.lead and submission.lead.students else "N/A"
    }), 200


@enrollment_bp.route('/public/submission/<string:token>/pdf', methods=['GET'])
@enrollment_bp.route('/submission/<string:token>/pdf', methods=['GET'])
def download_submission_contract_pdf(token):
    submission = EnrollmentSubmission.query.filter_by(secure_token=token).first_or_404()
    
    from io import BytesIO
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import inch
    from flask import send_file

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle('DocTitle', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=18, leading=22, textColor=colors.HexColor('#0b2f4c'), spaceAfter=10)
    section_title = ParagraphStyle('SectionTitle', parent=styles['Heading2'], fontName='Helvetica-Bold', fontSize=12, leading=16, textColor=colors.HexColor('#007ba4'), spaceBefore=12, spaceAfter=6)
    normal_text = ParagraphStyle('NormalText', parent=styles['Normal'], fontName='Helvetica', fontSize=10, leading=14, textColor=colors.HexColor('#333333'))
    bold_text = ParagraphStyle('BoldText', parent=normal_text, fontName='Helvetica-Bold')

    story = []
    story.append(Paragraph("<b>Exceptional Learning and Arts Academy</b>", title_style))
    story.append(Paragraph("Official Enrollment & Registration Contract", ParagraphStyle('Sub', parent=normal_text, fontSize=11, textColor=colors.HexColor('#64748b'))))
    story.append(Spacer(1, 10))

    form_name = submission.form.name if submission.form else "Enrollment Form"
    submitted_date = submission.submitted_at.strftime('%B %d, %Y at %I:%M %p') if submission.submitted_at else "N/A"
    student_name = "N/A"
    if submission.lead and submission.lead.students:
        student_name = f"{submission.lead.students[0].first_name} {submission.lead.students[0].last_name}"

    meta_data = [
        [Paragraph("<b>Form / Contract:</b>", bold_text), Paragraph(form_name, normal_text)],
        [Paragraph("<b>Student Name:</b>", bold_text), Paragraph(student_name, normal_text)],
        [Paragraph("<b>Submission Date:</b>", bold_text), Paragraph(submitted_date, normal_text)],
        [Paragraph("<b>Contract Status:</b>", bold_text), Paragraph(submission.status, normal_text)],
        [Paragraph("<b>Registration Fee:</b>", bold_text), Paragraph(f"${submission.form.fee_amount:.2f} ({submission.payment_status or 'N/A'})" if submission.form and submission.form.collect_fee else "N/A", normal_text)]
    ]
    meta_table = Table(meta_data, colWidths=[1.8*inch, 5.7*inch])
    meta_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('LINEBELOW', (0,-1), (-1,-1), 1, colors.HexColor('#e2e8f0'))
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 15))

    structure = submission.form.form_structure_json if submission.form else {}
    responses = submission.responses_json or {}

    sections = structure.get('sections', [])
    for sec in sections:
        sec_title = sec.get('title', 'Section')
        story.append(Paragraph(f"<b>{sec_title}</b>", section_title))
        fields = sec.get('fields', [])
        
        sec_rows = []
        for field in fields:
            f_id = field.get('id')
            f_label = field.get('label') or field.get('name') or f_id
            f_val = responses.get(f_id, '')
            if isinstance(f_val, list):
                f_val = ", ".join([str(v) for v in f_val])
            elif isinstance(f_val, bool):
                f_val = "Yes / Agreed" if f_val else "No"
            else:
                f_val = str(f_val) if f_val is not None else ""

            sec_rows.append([
                Paragraph(f"<b>{f_label}:</b>", bold_text),
                Paragraph(f_val or "<i>[Not provided]</i>", normal_text)
            ])

        if not sec_rows:
            sec_rows.append([Paragraph("Section Info:", bold_text), Paragraph("Completed", normal_text)])

        sec_table = Table(sec_rows, colWidths=[2.5*inch, 5.0*inch])
        sec_table.setStyle(TableStyle([
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('BOTTOMPADDING', (0,0), (-1,-1), 5),
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#f8fafc')),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#cbd5e1'))
        ]))
        story.append(sec_table)
        story.append(Spacer(1, 10))

    story.append(Spacer(1, 15))
    story.append(Paragraph("<i>This document is an electronically signed and executed contract on file with Exceptional Learning and Arts Academy.</i>", ParagraphStyle('Foot', parent=normal_text, fontSize=8, textColor=colors.HexColor('#64748b'))))

    doc.build(story)
    buffer.seek(0)

    filename = f"Contract_{submission.secure_token[:8]}.pdf"
    return send_file(
        buffer,
        as_attachment=True,
        download_name=filename,
        mimetype='application/pdf'
    )



# --- ADMIN ROUTES (AUTH REQUIRED) ---

def get_actor():
    claims = get_jwt()
    email = get_jwt_identity()
    if claims.get('role') == 'superadmin':
        return SuperAdmin.query.filter_by(email=email).first()
    return Staff.query.filter_by(email=email).first()

@enrollment_bp.route('/forms', methods=['GET'])
@jwt_required()
def get_forms():
    forms = EnrollmentForm.query.order_by(EnrollmentForm.created_at.desc()).all()
    return jsonify([form.to_dict() for form in forms]), 200

@enrollment_bp.route('/submissions', methods=['GET'])
@jwt_required()
def get_submissions():
    submissions = EnrollmentSubmission.query.order_by(EnrollmentSubmission.sent_at.desc()).all()
    return jsonify([sub.to_dict() for sub in submissions]), 200

@enrollment_bp.route('/submissions/<int:submission_id>', methods=['DELETE'])
@jwt_required()
def delete_submission(submission_id):
    actor = get_actor()
    if not actor: return jsonify({"error": "Unauthorized actor"}), 401
    submission = EnrollmentSubmission.query.get_or_404(submission_id)
    student_name = submission.lead.students[0].first_name if submission.lead.students else "a lead"
    log_activity(actor, f"Deleted enrollment submission for {student_name}", submission.form)
    db.session.delete(submission)
    db.session.commit()
    return jsonify({"message": "Submission deleted successfully"}), 200

@enrollment_bp.route('/submissions/<int:submission_id>/resend', methods=['POST'])
@jwt_required()
def resend_submission_email(submission_id):
    actor = get_actor()
    if not actor: return jsonify({"error": "Unauthorized actor"}), 401
    submission = EnrollmentSubmission.query.get_or_404(submission_id)
    if _send_enrollment_email(submission):
        student_name = submission.lead.students[0].first_name if submission.lead.students else "a lead"
        log_activity(actor, f"Resent enrollment email for {student_name}", submission.form)
        db.session.commit()
        return jsonify({"message": "Enrollment email resent successfully."}), 200
    else:
        return jsonify({"error": "Could not resend email. Parent information may be missing."}), 400

@enrollment_bp.route('/forms/<int:form_id>', methods=['GET'])
@jwt_required()
def get_form_by_id(form_id):
    form = EnrollmentForm.query.get_or_404(form_id)
    return jsonify(form.to_dict()), 200

@enrollment_bp.route('/forms/<int:form_id>', methods=['PUT'])
@jwt_required()
def update_form(form_id):
    actor = get_actor()
    if not actor: return jsonify({"error": "Unauthorized actor"}), 401
    form = EnrollmentForm.query.get_or_404(form_id)
    data = request.get_json()
    if 'name' in data: form.name = data['name']
    if 'status' in data: form.status = data['status']
    if 'form_structure_json' in data: form.form_structure_json = data['form_structure_json']
    if 'collect_fee' in data: form.collect_fee = data['collect_fee']
    if 'fee_amount' in data: form.fee_amount = data['fee_amount']
    if 'recipient_type' in data: form.recipient_type = data['recipient_type']
    log_activity(actor, f"Updated enrollment form: '{form.name}'", form)
    db.session.commit()
    return jsonify(form.to_dict()), 200

@enrollment_bp.route('/forms', methods=['POST'])
@jwt_required()
def create_form():
    actor = get_actor()
    if not actor: return jsonify({"error": "Unauthorized actor"}), 401
    default_structure = {"title": "Untitled Enrollment Form","sections": [{"id": "student_info", "title": "Student Info", "visible": True, "fields": []},{"id": "parent_info", "title": "Parent Info", "visible": True, "fields": []},{"id": "policy_waiver", "title": "Policy & Waiver", "visible": True, "fields": []}]}
    new_form = EnrollmentForm(name="Untitled Enrollment Form", form_structure_json=default_structure)
    db.session.add(new_form)
    log_activity(actor, f"Created new enrollment form: '{new_form.name}'", new_form)
    db.session.commit()
    return jsonify(new_form.to_dict()), 201

@enrollment_bp.route('/forms/<int:form_id>', methods=['DELETE'])
@jwt_required()
def delete_form(form_id):
    actor = get_actor()
    if not actor: return jsonify({"error": "Unauthorized actor"}), 401
    form = EnrollmentForm.query.get_or_404(form_id)
    log_activity(actor, f"Deleted enrollment form: '{form.name}'", form)
    db.session.delete(form)
    db.session.commit()
    return jsonify({"message": "Form deleted successfully"}), 200

@enrollment_bp.route('/forms/<int:form_id>/copy', methods=['POST'])
@jwt_required()
def copy_form(form_id):
    actor = get_actor()
    if not actor: return jsonify({"error": "Unauthorized actor"}), 401
    original_form = EnrollmentForm.query.get_or_404(form_id)
    new_form = EnrollmentForm(name=f"{original_form.name} (Copy)", status='Draft',form_structure_json=original_form.form_structure_json, collect_fee=original_form.collect_fee,fee_amount=original_form.fee_amount, recipient_type=original_form.recipient_type)
    db.session.add(new_form)
    log_activity(actor, f"Copied enrollment form '{original_form.name}' to '{new_form.name}'", new_form)
    db.session.commit()
    return jsonify(new_form.to_dict()), 201

@enrollment_bp.route('/potential-recipients', methods=['GET'])
@jwt_required()
def get_potential_recipients():
    recipient_type = request.args.get('type', 'New Students')
    if recipient_type == 'New Students':
        leads = Lead.query.filter(Lead.status.in_(['Interested', 'Toured', 'Admitted'])).all()
        recipients = [{"id": lead.id, "name": f"{lead.students[0].first_name} {lead.students[0].last_name}" if lead.students else "Unnamed Lead", "status": lead.status} for lead in leads]
        return jsonify(recipients), 200
    elif recipient_type == 'Returning Students': return jsonify([]), 200
    return jsonify({"error": "Invalid recipient type"}), 400

@enrollment_bp.route('/forms/<int:form_id>/send', methods=['POST'])
@jwt_required()
def send_form_to_leads(form_id):
    actor = get_actor()
    if not actor: return jsonify({"error": "Unauthorized actor"}), 401
    form = EnrollmentForm.query.get_or_404(form_id)
    data = request.get_json()
    lead_ids = data.get('lead_ids', [])
    if not lead_ids: return jsonify({"error": "No recipients selected"}), 400
    
    submissions_created = []
    for lead_id in lead_ids:
        exists = EnrollmentSubmission.query.filter_by(form_id=form.id, lead_id=lead_id).first()
        if not exists:
            submission = EnrollmentSubmission(form_id=form.id, lead_id=lead_id)
            db.session.add(submission)
            submissions_created.append(submission)
            
    db.session.flush()

    for submission in submissions_created:
        _send_enrollment_email(submission)

    log_activity(actor, f"Sent enrollment form '{form.name}' to {len(lead_ids)} recipient(s)", form)
    db.session.commit()
    return jsonify({"message": f"Form sent successfully to {len(submissions_created)} new recipients."}), 201