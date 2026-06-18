from flask import Blueprint, request, jsonify
from app.models import db
from app.models.task_model import Task
from app.models.lead_model import Lead
from app.models.department_model import Department
from app.models.staff_model import Staff
from app.models.super_admin_model import SuperAdmin
from app.models.activity_log_model import log_activity
from app.utils.notifications import create_notifications_and_send_emails
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt
from sqlalchemy import or_
from datetime import datetime

task_bp = Blueprint('tasks', __name__)

def get_actor_from_jwt():
    claims = get_jwt()
    current_user_email = get_jwt_identity()
    if claims.get('role') == 'superadmin':
        return SuperAdmin.query.filter_by(email=current_user_email).first()
    elif claims.get('role') == 'staff':
        return Staff.query.filter_by(email=current_user_email).first()
    return None

@task_bp.route('/my-tasks', methods=['GET'])
@jwt_required()
def get_my_tasks():
    current_user_email = get_jwt_identity()
    claims = get_jwt()
    role = claims.get('role')

    staff_member = None
    super_admin = None

    if role == 'superadmin':
        super_admin = SuperAdmin.query.filter_by(email=current_user_email).first()
    else:
        staff_member = Staff.query.filter_by(email=current_user_email).first()

    if not staff_member and not super_admin:
        return jsonify({"error": "User not found"}), 404

    all_tasks_serialized = []

    # 1. Fetch Lead-associated Tasks (only for staff)
    if staff_member:
        staff_department_ids = [dept.id for dept in staff_member.departments]
        lead_tasks = Task.query.filter(
            or_(
                Task.assigned_departments.any(Department.id.in_(staff_department_ids)),
                Task.assigned_staff.any(id=staff_member.id)
            )
        ).all()
        for t in lead_tasks:
            d = t.to_dict()
            d['task_type'] = 'lead'
            all_tasks_serialized.append(d)

    # 2. Fetch Board Tasks (Monday.com style)
    from app.models.board_model import BoardTask, BoardTaskAssignee
    if staff_member:
        board_tasks = BoardTask.query.filter(
            or_(
                BoardTask.responsible_staff_id == staff_member.id,
                BoardTask.assignees.any(BoardTaskAssignee.staff_id == staff_member.id)
            )
        ).all()
    elif super_admin:
        board_tasks = BoardTask.query.filter(
            or_(
                BoardTask.responsible_super_admin_id == super_admin.id,
                BoardTask.assignees.any(BoardTaskAssignee.super_admin_id == super_admin.id)
            )
        ).all()
    else:
        board_tasks = []

    for bt in board_tasks:
        bt_dict = bt.to_dict()
        assignees = bt_dict.get('assignee_names') or []

        d = {
            'id': bt.id,
            'title': bt.title,
            'note': bt.notes or '',
            'status': bt.status,
            'due_date': bt.due_date.isoformat() + 'T00:00:00Z' if bt.due_date else None,
            'lead_id': None,
            'lead_status': None,
            'assigned_department_ids': [],
            'assigned_department_names': [],
            'assigned_staff_ids': [item['id'] for item in bt_dict.get('assignees', []) if item.get('role') == 'staff'],
            'assigned_staff_names': assignees,
            'created_by_staff_name': None,
            'created_at': bt.created_at.isoformat() + 'Z' if bt.created_at else None,
            'lead_secure_token': None,
            'task_type': 'board',
            'board_id': bt.group.board_id if bt.group else None,
            'board_name': bt.group.board.name if bt.group and bt.group.board else None,
            'group_name': bt.group.name if bt.group else None
        }
        all_tasks_serialized.append(d)

    # Sort combined tasks by created_at desc
    all_tasks_serialized.sort(key=lambda x: x.get('created_at', '') or '', reverse=True)

    return jsonify(all_tasks_serialized), 200

@task_bp.route('/my-tasks/count', methods=['GET'])
@jwt_required()
def get_my_tasks_count():
    """Returns the count of active (not completed/done) tasks for a user."""
    current_user_email = get_jwt_identity()
    claims = get_jwt()
    role = claims.get('role')

    staff_member = None
    super_admin = None

    if role == 'superadmin':
        super_admin = SuperAdmin.query.filter_by(email=current_user_email).first()
    else:
        staff_member = Staff.query.filter_by(email=current_user_email).first()

    if not staff_member and not super_admin:
        return jsonify({"count": 0}), 200

    count = 0

    # 1. Count Lead-associated Tasks (only for staff)
    if staff_member:
        staff_department_ids = [dept.id for dept in staff_member.departments]
        count += Task.query.filter(
            Task.status != 'Completed',
            or_(
                Task.assigned_departments.any(Department.id.in_(staff_department_ids)),
                Task.assigned_staff.any(id=staff_member.id)
            )
        ).count()

    # 2. Count Board Tasks
    from app.models.board_model import BoardTask, BoardTaskAssignee
    if staff_member:
        count += BoardTask.query.filter(
            or_(
                BoardTask.responsible_staff_id == staff_member.id,
                BoardTask.assignees.any(BoardTaskAssignee.staff_id == staff_member.id)
            ),
            BoardTask.status != 'Done'
        ).count()
    elif super_admin:
        count += BoardTask.query.filter(
            or_(
                BoardTask.responsible_super_admin_id == super_admin.id,
                BoardTask.assignees.any(BoardTaskAssignee.super_admin_id == super_admin.id)
            ),
            BoardTask.status != 'Done'
        ).count()

    return jsonify({"count": count}), 200

@task_bp.route('/lead/<string:lead_token>', methods=['GET'])
@jwt_required()
def get_tasks_for_lead(lead_token):
    lead = Lead.query.filter_by(secure_token=lead_token).first_or_404()
    tasks = Task.query.filter_by(lead_id=lead.id).order_by(Task.created_at.desc()).all()
    return jsonify([task.to_dict() for task in tasks]), 200


@task_bp.route('', methods=['POST'])
@jwt_required()
def create_task():
    actor = get_actor_from_jwt()
    if not actor:
        return jsonify({"error": "Unauthorized: Actor not found for this role."}), 401

    data = request.get_json()
    required_fields = ['title', 'lead_id']
    if not all(field in data for field in required_fields):
        return jsonify({"error": "Missing required fields"}), 400

    assigned_department_ids = data.get('assigned_department_ids', [])
    assigned_staff_ids = data.get('assigned_staff_ids', [])

    if not assigned_department_ids and not assigned_staff_ids:
        return jsonify({"error": "Task must be assigned to at least one department or staff member."}), 400

    due_date = None
    if data.get('due_date'):
        try:
            due_date = datetime.fromisoformat(data['due_date'].replace('Z', '+00:00'))
        except (ValueError, TypeError):
            return jsonify({"error": "Invalid due_date format. Please use ISO 8601 format."}), 400

    lead = Lead.query.get(data['lead_id'])
    if not lead:
        return jsonify({"error": "Associated lead not found."}), 404

    creator_staff_id = actor.id if isinstance(actor, Staff) else Staff.query.first().id
    if not creator_staff_id:
        return jsonify({"error": "Cannot create task. No staff members exist in the system."}), 400

    new_task = Task(title=data['title'], note=data.get('note', ''), lead_id=data['lead_id'], created_by_staff_id=creator_staff_id, due_date=due_date)
    
    recipients = set()
    for dept_id in assigned_department_ids:
        dept = Department.query.get(dept_id)
        if dept:
            new_task.assigned_departments.append(dept)
            recipients.update(dept.staff_members)

    for staff_id in assigned_staff_ids:
        staff = Staff.query.get(staff_id)
        if staff:
            new_task.assigned_staff.append(staff)
            recipients.add(staff)

    db.session.add(new_task)
    log_activity(actor, f"Created a new task: '{new_task.title}'", lead)
    
    student_name = f"{lead.students[0].first_name} {lead.students[0].last_name}"
    message = f"{actor.name} assigned you a new task: '{new_task.title}' for the lead {student_name}."
    
    if recipients:
         create_notifications_and_send_emails(list(recipients), message, new_task)

    db.session.commit()
    return jsonify(new_task.to_dict()), 201


@task_bp.route('/<int:id>', methods=['PUT'])
@jwt_required()
def update_task(id):
    actor = get_actor_from_jwt()
    if not actor:
        return jsonify({"error": "Unauthorized: Actor not found for this role."}), 401

    task = Task.query.get_or_404(id)
    data = request.get_json()

    task.title = data.get('title', task.title)
    task.note = data.get('note', task.note)
    
    if data.get('due_date'):
        task.due_date = datetime.fromisoformat(data['due_date'].replace('Z', '+00:00'))
    else:
        task.due_date = None

    if 'status' in data:
        task.status = data['status']
        if data['status'] == 'Completed':
            log_activity(actor, f"Completed task: '{task.title}'", task.lead)
        else:
            log_activity(actor, f"Updated task '{task.title}' status to '{data['status']}'", task.lead)
            
    if 'lead_status' in data:
        task.lead.status = data['lead_status']
        log_activity(actor, f"Updated lead status to '{data['lead_status']}' via task '{task.title}'", task.lead)

    if 'assigned_department_ids' in data or 'assigned_staff_ids' in data:
        task.assigned_departments.clear()
        task.assigned_staff.clear()
        new_recipients = set()
        for dept_id in data.get('assigned_department_ids', []):
            dept = Department.query.get(dept_id)
            if dept:
                task.assigned_departments.append(dept)
                new_recipients.update(dept.staff_members)
        for staff_id in data.get('assigned_staff_ids', []):
            staff = Staff.query.get(staff_id)
            if staff:
                task.assigned_staff.append(staff)
                new_recipients.add(staff)
        student_name = f"{task.lead.students[0].first_name} {task.lead.students[0].last_name}"
        message = f"{actor.name} assigned you a task: '{task.title}' for the lead {student_name}."
        if new_recipients:
            create_notifications_and_send_emails(list(new_recipients), message, task)
    
    db.session.commit()
    return jsonify(task.to_dict()), 200


@task_bp.route('/<int:id>/status', methods=['PUT'])
@jwt_required()
def update_task_status(id):
    actor = get_actor_from_jwt()
    if not actor:
        return jsonify({"error": "Unauthorized: Actor not found for this role."}), 401

    task = Task.query.get_or_404(id)
    new_status = request.json.get('status')
    if not new_status:
        return jsonify({"error": "Status is required"}), 400

    task.status = new_status
    if new_status == 'Completed':
        log_activity(actor, f"Completed task: '{task.title}'", task.lead)

    db.session.commit()
    return jsonify(task.to_dict()), 200
