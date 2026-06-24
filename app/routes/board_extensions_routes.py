import os
import json
import uuid
from datetime import datetime
from flask import Blueprint, jsonify, request, send_file, current_app
from flask_jwt_extended import jwt_required, get_jwt, get_jwt_identity
from werkzeug.utils import secure_filename

from app.models import db
from app.models.board_model import Board, BoardGroup, BoardTask, BoardTaskAssignee, TaskTimeEntry, WorkspaceDoc
from app.models.staff_model import Staff
from app.models.super_admin_model import SuperAdmin
from app.models.board_model_extensions import (
    BoardCustomField, TaskCustomFieldValue,
    BoardFormConfig, BoardFormResponse,
    WorkspaceDocumentFolder, WorkspaceDocumentFile, WorkspaceDocumentFileVersion
)
from app.models.activity_log_model import log_activity

board_extensions_bp = Blueprint('board_extensions', __name__)

# ==========================================
# Helpers
# ==========================================

def get_actor():
    claims = get_jwt()
    role = claims.get('role')
    email = get_jwt_identity()
    if role == 'superadmin':
        actor = SuperAdmin.query.filter_by(email=email).first()
        return actor, 'superadmin'
    actor = Staff.query.filter_by(email=email).first()
    return actor, 'staff'

def ensure_board_access(board, actor, role):
    if role == 'superadmin':
        return True
    if not board.is_private:
        return True
    return any(member.staff_id == actor.id for member in board.access_members)

# ==========================================
# 1. Custom Fields APIs
# ==========================================

@board_extensions_bp.route('/boards/<int:board_id>/custom-fields', methods=['GET'])
@jwt_required()
def get_board_custom_fields(board_id):
    actor, role = get_actor()
    board = Board.query.get_or_404(board_id)
    if not ensure_board_access(board, actor, role):
        return jsonify({"error": "Forbidden"}), 403
    
    fields = BoardCustomField.query.filter_by(board_id=board_id).all()
    return jsonify([f.to_dict() for f in fields]), 200

@board_extensions_bp.route('/boards/<int:board_id>/custom-fields', methods=['POST'])
@jwt_required()
def create_board_custom_field(board_id):
    actor, role = get_actor()
    board = Board.query.get_or_404(board_id)
    if not ensure_board_access(board, actor, role):
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json() or {}
    name = data.get('name')
    field_type = data.get('type')
    config = data.get('config') # dropdown options etc

    if not name or not field_type:
        return jsonify({"error": "Name and type are required"}), 400

    config_str = json.dumps(config) if config else None

    field = BoardCustomField(
        board_id=board_id,
        name=name,
        type=field_type,
        config_json=config_str
    )
    db.session.add(field)
    db.session.commit()
    log_activity(actor, f"Created custom field: '{name}' ({field_type}) in board '{board.name}'")
    return jsonify(field.to_dict()), 201

@board_extensions_bp.route('/custom-fields/<int:field_id>', methods=['DELETE'])
@jwt_required()
def delete_board_custom_field(field_id):
    actor, role = get_actor()
    field = BoardCustomField.query.get_or_404(field_id)
    board = Board.query.get_or_404(field.board_id)
    if not ensure_board_access(board, actor, role):
        return jsonify({"error": "Forbidden"}), 403

    db.session.delete(field)
    db.session.commit()
    log_activity(actor, f"Deleted custom field: '{field.name}' from board '{board.name}'")
    return jsonify({"message": "Custom field deleted"}), 200

@board_extensions_bp.route('/tasks/<int:task_id>/custom-fields', methods=['GET'])
@jwt_required()
def get_task_custom_field_values(task_id):
    actor, role = get_actor()
    task = BoardTask.query.get_or_404(task_id)
    board = task.group.board
    if not ensure_board_access(board, actor, role):
        return jsonify({"error": "Forbidden"}), 403

    values = TaskCustomFieldValue.query.filter_by(task_id=task_id).all()
    return jsonify([v.to_dict() for v in values]), 200

@board_extensions_bp.route('/tasks/<int:task_id>/custom-fields', methods=['PUT'])
@jwt_required()
def update_task_custom_field_values(task_id):
    actor, role = get_actor()
    task = BoardTask.query.get_or_404(task_id)
    board = task.group.board
    if not ensure_board_access(board, actor, role):
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json() or {}
    field_id = data.get('field_id')
    value = data.get('value') # Can be string, number, array of options, rating count etc.

    if not field_id:
        return jsonify({"error": "field_id is required"}), 400

    field = BoardCustomField.query.get_or_404(field_id)
    if field.board_id != board.id:
        return jsonify({"error": "Field does not belong to this board"}), 400

    val_record = TaskCustomFieldValue.query.filter_by(task_id=task_id, field_id=field_id).first()
    value_str = json.dumps(value) if value is not None else None

    if val_record:
        val_record.value_json = value_str
    else:
        val_record = TaskCustomFieldValue(
            task_id=task_id,
            field_id=field_id,
            value_json=value_str
        )
        db.session.add(val_record)

    db.session.commit()
    return jsonify(val_record.to_dict()), 200


# ==========================================
# 2. Workspace Forms APIs
# ==========================================

@board_extensions_bp.route('/boards/<int:board_id>/forms', methods=['GET'])
@jwt_required()
def get_board_forms(board_id):
    actor, role = get_actor()
    board = Board.query.get_or_404(board_id)
    if not ensure_board_access(board, actor, role):
        return jsonify({"error": "Forbidden"}), 403
    
    forms = BoardFormConfig.query.filter_by(board_id=board_id).all()
    return jsonify([f.to_dict() for f in forms]), 200

@board_extensions_bp.route('/boards/<int:board_id>/forms', methods=['POST'])
@jwt_required()
def create_board_form(board_id):
    actor, role = get_actor()
    board = Board.query.get_or_404(board_id)
    if not ensure_board_access(board, actor, role):
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json() or {}
    name = data.get('name')
    description = data.get('description')
    form_structure = data.get('form_structure') # list of questions

    if not name or not form_structure:
        return jsonify({"error": "Name and form structure are required"}), 400

    form = BoardFormConfig(
        board_id=board_id,
        name=name,
        description=description,
        form_structure_json=json.dumps(form_structure),
        is_active=True
    )
    db.session.add(form)
    db.session.commit()
    log_activity(actor, f"Created workspace form: '{name}' in board '{board.name}'")
    return jsonify(form.to_dict()), 201

@board_extensions_bp.route('/forms/<int:form_id>', methods=['DELETE'])
@jwt_required()
def delete_board_form(form_id):
    actor, role = get_actor()
    form = BoardFormConfig.query.get_or_404(form_id)
    board = Board.query.get_or_404(form.board_id)
    if not ensure_board_access(board, actor, role):
        return jsonify({"error": "Forbidden"}), 403

    db.session.delete(form)
    db.session.commit()
    log_activity(actor, f"Deleted form '{form.name}' from board '{board.name}'")
    return jsonify({"message": "Form deleted"}), 200

@board_extensions_bp.route('/forms/submit/<int:form_id>', methods=['POST'])
@jwt_required()
def submit_form_response(form_id):
    actor, role = get_actor()
    form = BoardFormConfig.query.get_or_404(form_id)
    board = Board.query.get_or_404(form.board_id)

    data = request.get_json() or {}
    response_data = data.get('response') # dictionary of {question_id: answer}
    if not response_data:
        return jsonify({"error": "Response data is required"}), 400

    # Auto Create Task logic
    # Find or create a default group in the board
    group = BoardGroup.query.filter_by(board_id=board.id).order_by(BoardGroup.position.asc()).first()
    if not group:
        group = BoardGroup(board_id=board.id, name="Form Submissions", color="#fdab3d", position=0)
        db.session.add(group)
        db.session.flush()

    # Parse form structure to map answers to task properties or custom fields
    form_struct = json.loads(form.form_structure_json)
    
    task_payload = {
        'title': f"Form Submission - {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}",
        'status': 'Not Started',
        'priority': 'Normal',
        'notes': 'Form submission auto-generated task'
    }

    custom_field_answers = {}

    for question in form_struct:
        qid = question.get('id')
        mapping = question.get('mapping') # e.g. 'title', 'priority', 'due_date', 'notes', or 'custom_field_X'
        answer = response_data.get(str(qid))

        if answer is None:
            continue

        if mapping in {'title', 'priority', 'status', 'notes'}:
            task_payload[mapping] = answer
        elif mapping == 'due_date':
            try:
                task_payload['due_date'] = datetime.fromisoformat(answer.replace('Z', '+00:00')).date()
            except:
                pass
        elif mapping and mapping.startswith('custom_field_'):
            field_id = int(mapping.split('_')[-1])
            custom_field_answers[field_id] = answer

    # Create the task
    task = BoardTask(
        group_id=group.id,
        title=task_payload['title'],
        status=task_payload['status'],
        priority=task_payload['priority'],
        notes=task_payload['notes'],
        due_date=task_payload.get('due_date')
    )
    db.session.add(task)
    db.session.flush()

    # Save Custom Field Answers
    for fid, val in custom_field_answers.items():
        field_val = TaskCustomFieldValue(
            task_id=task.id,
            field_id=fid,
            value_json=json.dumps(val)
        )
        db.session.add(field_val)

    # Save Form Response
    response_record = BoardFormResponse(
        form_id=form_id,
        response_json=json.dumps(response_data),
        created_task_id=task.id
    )
    db.session.add(response_record)
    db.session.commit()

    log_activity(actor, f"Submitted form response to '{form.name}' resulting in Task '{task.title}'")
    return jsonify({
        "message": "Form submitted successfully",
        "task": task.to_dict(),
        "response": response_record.to_dict()
    }), 201

@board_extensions_bp.route('/forms/<int:form_id>/responses', methods=['GET'])
@jwt_required()
def get_form_responses(form_id):
    actor, role = get_actor()
    form = BoardFormConfig.query.get_or_404(form_id)
    board = Board.query.get_or_404(form.board_id)
    if not ensure_board_access(board, actor, role):
        return jsonify({"error": "Forbidden"}), 403

    responses = BoardFormResponse.query.filter_by(form_id=form_id).order_by(BoardFormResponse.created_at.desc()).all()
    return jsonify([r.to_dict() for r in responses]), 200


# ==========================================
# 3. Document Management APIs
# ==========================================

@board_extensions_bp.route('/boards/<int:board_id>/files', methods=['GET'])
@jwt_required()
def get_board_documents(board_id):
    actor, role = get_actor()
    board = Board.query.get_or_404(board_id)
    if not ensure_board_access(board, actor, role):
        return jsonify({"error": "Forbidden"}), 403

    folders = WorkspaceDocumentFolder.query.filter_by(board_id=board_id).all()
    files = WorkspaceDocumentFile.query.filter_by(board_id=board_id).all()

    return jsonify({
        "folders": [f.to_dict() for f in folders],
        "files": [f.to_dict() for f in files]
    }), 200

@board_extensions_bp.route('/boards/<int:board_id>/folders', methods=['POST'])
@jwt_required()
def create_document_folder(board_id):
    actor, role = get_actor()
    board = Board.query.get_or_404(board_id)
    if not ensure_board_access(board, actor, role):
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json() or {}
    name = data.get('name')
    parent_id = data.get('parent_id')

    if not name:
        return jsonify({"error": "Folder name is required"}), 400

    folder = WorkspaceDocumentFolder(
        board_id=board_id,
        name=name,
        parent_id=parent_id
    )
    db.session.add(folder)
    db.session.commit()
    log_activity(actor, f"Created folder '{name}' in document center of board '{board.name}'")
    return jsonify(folder.to_dict()), 201

@board_extensions_bp.route('/boards/<int:board_id>/files/upload', methods=['POST'])
@jwt_required()
def upload_document_file(board_id):
    actor, role = get_actor()
    board = Board.query.get_or_404(board_id)
    if not ensure_board_access(board, actor, role):
        return jsonify({"error": "Forbidden"}), 403

    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400

    folder_id = request.form.get('folder_id', type=int)

    # Save file physically
    upload_folder = os.path.join(current_app.root_path, 'static', 'documents')
    os.makedirs(upload_folder, exist_ok=True)

    filename = secure_filename(file.filename)
    unique_filename = f"{uuid.uuid4().hex}_{filename}"
    file_path = os.path.join(upload_folder, unique_filename)
    file.save(file_path)

    relative_path = f"/static/documents/{unique_filename}"

    # Determine file type group
    ext = filename.split('.')[-1].lower()
    if ext in {'pdf'}:
        ftype = 'pdf'
    elif ext in {'doc', 'docx'}:
        ftype = 'word'
    elif ext in {'xls', 'xlsx'}:
        ftype = 'excel'
    elif ext in {'ppt', 'pptx'}:
        ftype = 'powerpoint'
    elif ext in {'jpg', 'jpeg', 'png', 'gif', 'svg'}:
        ftype = 'image'
    elif ext in {'mp4', 'mov', 'avi', 'mkv'}:
        ftype = 'video'
    else:
        ftype = 'other'

    doc_file = WorkspaceDocumentFile(
        board_id=board_id,
        folder_id=folder_id if folder_id else None,
        filename=filename,
        file_path=relative_path,
        file_type=ftype,
        version=1,
        uploaded_by_name=actor.name
    )
    db.session.add(doc_file)
    db.session.commit()

    # Create the first version
    first_version = WorkspaceDocumentFileVersion(
        file_id=doc_file.id,
        version_number=1,
        file_path=relative_path,
        uploaded_by_name=actor.name
    )
    db.session.add(first_version)
    db.session.commit()

    log_activity(actor, f"Uploaded document file '{filename}' in board '{board.name}'")
    return jsonify(doc_file.to_dict()), 201

@board_extensions_bp.route('/files/<int:file_id>/new-version', methods=['POST'])
@jwt_required()
def upload_new_file_version(file_id):
    actor, role = get_actor()
    doc_file = WorkspaceDocumentFile.query.get_or_404(file_id)
    board = Board.query.get_or_404(doc_file.board_id)
    if not ensure_board_access(board, actor, role):
        return jsonify({"error": "Forbidden"}), 403

    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400

    upload_folder = os.path.join(current_app.root_path, 'static', 'documents')
    os.makedirs(upload_folder, exist_ok=True)

    filename = secure_filename(file.filename)
    unique_filename = f"{uuid.uuid4().hex}_{filename}"
    file_path = os.path.join(upload_folder, unique_filename)
    file.save(file_path)

    relative_path = f"/static/documents/{unique_filename}"
    next_version = doc_file.version + 1

    doc_file.version = next_version
    doc_file.file_path = relative_path

    new_v = WorkspaceDocumentFileVersion(
        file_id=doc_file.id,
        version_number=next_version,
        file_path=relative_path,
        uploaded_by_name=actor.name
    )
    db.session.add(new_v)
    db.session.commit()

    log_activity(actor, f"Uploaded new version {next_version} for document file '{doc_file.filename}'")
    return jsonify(doc_file.to_dict()), 200

@board_extensions_bp.route('/files/<int:file_id>/share', methods=['PUT'])
@jwt_required()
def toggle_document_sharing(file_id):
    actor, role = get_actor()
    doc_file = WorkspaceDocumentFile.query.get_or_404(file_id)
    board = Board.query.get_or_404(doc_file.board_id)
    if not ensure_board_access(board, actor, role):
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json() or {}
    is_shared = data.get('is_shared', False)
    permissions = data.get('permissions') # dict representing viewer access details

    doc_file.is_shared = is_shared
    if permissions is not None:
        doc_file.permissions_json = json.dumps(permissions)

    db.session.commit()
    log_activity(actor, f"Updated sharing permissions for '{doc_file.filename}'")
    return jsonify(doc_file.to_dict()), 200

@board_extensions_bp.route('/files/download/<int:file_id>', methods=['GET'])
@jwt_required()
def download_document_file(file_id):
    actor, role = get_actor()
    doc_file = WorkspaceDocumentFile.query.get_or_404(file_id)
    board = Board.query.get_or_404(doc_file.board_id)
    if not ensure_board_access(board, actor, role):
        return jsonify({"error": "Forbidden"}), 403

    v_num = request.args.get('version', type=int)
    target_path = doc_file.file_path

    if v_num:
        v_record = WorkspaceDocumentFileVersion.query.filter_by(file_id=file_id, version_number=v_num).first()
        if v_record:
            target_path = v_record.file_path

    absolute_path = os.path.join(current_app.root_path, target_path.lstrip('/'))
    if not os.path.exists(absolute_path):
        return jsonify({"error": "File not found physically on the server"}), 404

    return send_file(absolute_path, as_attachment=True, download_name=doc_file.filename)


# ==========================================
# 4. Reporting & Analytics API
# ==========================================

@board_extensions_bp.route('/boards/<int:board_id>/reports', methods=['GET'])
@jwt_required()
def get_workspace_reports(board_id):
    actor, role = get_actor()
    board = Board.query.get_or_404(board_id)
    if not ensure_board_access(board, actor, role):
        return jsonify({"error": "Forbidden"}), 403

    # Tasks stats
    groups = BoardGroup.query.filter_by(board_id=board_id).all()
    group_ids = [g.id for g in groups]
    tasks = BoardTask.query.filter(BoardTask.group_id.in_(group_ids)).all()

    total_tasks = len(tasks)
    completed_tasks = sum(1 for t in tasks if t.status == 'Done')
    in_progress_tasks = sum(1 for t in tasks if t.status == 'In Progress')
    todo_tasks = sum(1 for t in tasks if t.status == 'Not Started')

    priority_stats = {'Urgent': 0, 'High': 0, 'Normal': 0, 'Low': 0}
    for t in tasks:
        p = t.priority or 'Normal'
        if p in priority_stats:
            priority_stats[p] += 1

    # Time tracking stats
    task_ids = [t.id for t in tasks]
    time_entries = TaskTimeEntry.query.filter(TaskTimeEntry.task_id.in_(task_ids)).all()
    total_time_seconds = sum(e.duration_seconds for e in time_entries)

    # Grouped by user productivity
    user_time_spent = {}
    for entry in time_entries:
        uname = entry.user_name
        user_time_spent[uname] = user_time_spent.get(uname, 0) + entry.duration_seconds

    # Grouped by department/department stats (if department models can be linked)
    return jsonify({
        "tasks": {
            "total": total_tasks,
            "completed": completed_tasks,
            "in_progress": in_progress_tasks,
            "todo": todo_tasks
        },
        "priorities": priority_stats,
        "time": {
            "total_hours": round(total_time_seconds / 3600.0, 2),
            "total_minutes": round(total_time_seconds / 60.0, 2),
            "user_breakdown": [{"user": k, "hours": round(v / 3600.0, 2)} for k, v in user_time_spent.items()]
        }
    }), 200

@board_extensions_bp.route('/boards/<int:board_id>/time-entries', methods=['GET'])
@jwt_required()
def get_board_time_entries(board_id):
    actor, role = get_actor()
    board = Board.query.get_or_404(board_id)
    if not ensure_board_access(board, actor, role):
        return jsonify({"error": "Forbidden"}), 403

    groups = BoardGroup.query.filter_by(board_id=board_id).all()
    group_ids = [g.id for g in groups]
    tasks = BoardTask.query.filter(BoardTask.group_id.in_(group_ids)).all()
    task_map = {t.id: t.title for t in tasks}

    time_entries = TaskTimeEntry.query.filter(TaskTimeEntry.task_id.in_(task_map.keys())).order_by(TaskTimeEntry.start_time.desc()).all()

    results = []
    for entry in time_entries:
        d = entry.to_dict()
        d['task_title'] = task_map.get(entry.task_id, 'Unknown Task')
        results.append(d)

    return jsonify(results), 200

@board_extensions_bp.route('/tasks/assigned-to/<string:target_role>/<int:target_user_id>', methods=['GET'])
@jwt_required()
def get_tasks_assigned_to(target_role, target_user_id):
    actor, role = get_actor()
    if not actor:
        return jsonify({"error": "Unauthorized"}), 401

    if target_role == 'staff':
        tasks = BoardTask.query.filter(
            (BoardTask.responsible_staff_id == target_user_id) |
            (BoardTask.assignees.any(BoardTaskAssignee.staff_id == target_user_id))
        ).all()
    elif target_role == 'superadmin':
        tasks = BoardTask.query.filter(
            (BoardTask.responsible_super_admin_id == target_user_id) |
            (BoardTask.assignees.any(BoardTaskAssignee.super_admin_id == target_user_id))
        ).all()
    else:
        return jsonify({"error": "Invalid role"}), 400

    results = []
    for t in tasks:
        d = t.to_dict()
        d['board_id'] = t.group.board_id if t.group else None
        d['board_name'] = t.group.board.name if t.group and t.group.board else ''
        results.append(d)

    return jsonify(results), 200

# ==========================================
# 5. Global Search API
# ==========================================

@board_extensions_bp.route('/search', methods=['GET'])
@jwt_required()
def global_search():
    actor, role = get_actor()
    if not actor:
        return jsonify({"error": "Unauthorized"}), 401
        
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify({
            "tasks": [],
            "boards": [],
            "docs": [],
            "files": [],
            "users": []
        }), 200

    like_query = f"%{q}%"

    # Tasks
    tasks = BoardTask.query.filter(
        BoardTask.title.ilike(like_query) | BoardTask.notes.ilike(like_query)
    ).limit(10).all()

    # Boards/Spaces (filters based on access if private)
    boards_query = Board.query.filter(
        Board.name.ilike(like_query) | Board.description.ilike(like_query)
    )
    boards_all = boards_query.all()
    boards = [b for b in boards_all if ensure_board_access(b, actor, role)][:10]

    # Docs
    docs_all = WorkspaceDoc.query.filter(
        WorkspaceDoc.title.ilike(like_query) | WorkspaceDoc.content_html.ilike(like_query)
    ).all()
    docs = [d for d in docs_all if ensure_board_access(d.board, actor, role)][:10]

    # Files
    files_all = WorkspaceDocumentFile.query.filter(
        WorkspaceDocumentFile.filename.ilike(like_query)
    ).all()
    files = [f for f in files_all if ensure_board_access(Board.query.get(f.board_id), actor, role)][:10]

    # Users (Staff and SuperAdmins)
    staff_members = Staff.query.filter(
        Staff.name.ilike(like_query) | Staff.email.ilike(like_query)
    ).limit(10).all()
    
    superadmins = SuperAdmin.query.filter(
        SuperAdmin.name.ilike(like_query) | SuperAdmin.email.ilike(like_query)
    ).limit(10).all()

    users = []
    for s in staff_members:
        users.append({"id": s.id, "name": s.name, "email": s.email, "role": "staff"})
    for sa in superadmins:
        users.append({"id": sa.id, "name": sa.name, "email": sa.email, "role": "superadmin"})

    return jsonify({
        "tasks": [t.to_dict() for t in tasks],
        "boards": [b.to_dict() for b in boards],
        "docs": [d.to_dict() for d in docs],
        "files": [f.to_dict() for f in files],
        "users": users[:15]
    }), 200

