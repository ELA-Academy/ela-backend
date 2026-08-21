import os
import json
from datetime import datetime

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required

from app.models import db
from app.models.board_model import Board, BoardAccessMember, BoardGroup, BoardTask, BoardTaskAssignee, CalendarEvent, TaskTimeEntry, WorkspaceDoc, WorkspaceDocComment, BoardMilestone
from app.models.department_model import Department
from app.models.notification_model import Notification
from app.models.staff_model import Staff
from app.models.super_admin_model import SuperAdmin
from app.models.task_update_model import TaskUpdate, TaskUpdateLike, TaskUpdateReply, CommentReaction


board_bp = Blueprint('boards', __name__)


def get_actor():
    claims = get_jwt()
    role = claims.get('role')
    email = get_jwt_identity()
    if role == 'superadmin':
        actor = SuperAdmin.query.filter_by(email=email).first()
        return actor, 'superadmin'

    actor = Staff.query.filter_by(email=email).first()
    return actor, 'staff'


def board_is_accessible(board, actor, role):
    if not board or not actor:
        return False

    if getattr(board, 'is_personal', False):
        if role == 'superadmin':
            return board.owner_super_admin_id == actor.id
        else:
            return board.owner_staff_id == actor.id

    if role == 'superadmin':
        return True

    if not board.is_private:
        return True

    return any(member.staff_id == actor.id for member in board.access_members)


def merge_task_into_target_board(task, target_group_id, new_status=None):
    from app.models.board_model import BoardGroup
    from app.models.board_model_extensions import BoardCustomField, TaskCustomFieldValue

    target_group = BoardGroup.query.get(target_group_id)
    if not target_group:
        return

    old_group = task.group
    old_board_id = old_group.board_id if old_group else None
    new_board_id = target_group.board_id
    new_board = target_group.board

    # Update group ID
    task.group_id = target_group.id

    # 1. Status Update & Synchronization
    if new_status and str(new_status).strip():
        task.status = str(new_status).strip()

    # If target group has a specific name and it's not a generic container, align status or group
    if new_board and task.status:
        try:
            statuses = json.loads(new_board.custom_statuses) if new_board.custom_statuses else []
        except Exception:
            statuses = []

        status_names = []
        for s in statuses:
            if isinstance(s, dict):
                status_names.append(s.get('label', s.get('name', s.get('id', ''))).strip().lower())
            else:
                status_names.append(str(s).strip().lower())

        # If task's status doesn't exist on target board, automatically register it in target board's custom_statuses!
        if task.status.strip().lower() not in status_names and task.status.strip().lower() not in ['not started', 'in progress', 'done', 'to do', 'completed']:
            new_status_obj = {
                "id": task.status,
                "label": task.status,
                "color": getattr(old_group, 'color', '#673de6') or '#673de6',
                "type": "Active"
            }
            statuses.append(new_status_obj)
            new_board.custom_statuses = json.dumps(statuses)
            db.session.flush()

        # Ensure a matching group exists on new_board for this status if target_group is generic
        matching_group = BoardGroup.query.filter(
            BoardGroup.board_id == new_board_id,
            db.func.lower(BoardGroup.name) == db.func.lower(task.status)
        ).first()

        if matching_group:
            task.group_id = matching_group.id
        elif target_group.name.lower() in ['list', 'default', 'general', 'tasks', 'not started']:
            # Create a dedicated group for this custom status on target board
            last_group = BoardGroup.query.filter_by(board_id=new_board_id).order_by(BoardGroup.position.desc()).first()
            next_pos = (last_group.position + 1) if last_group else 0
            created_group = BoardGroup(
                board_id=new_board_id,
                name=task.status,
                color=getattr(old_group, 'color', '#673de6') or '#673de6',
                position=next_pos
            )
            db.session.add(created_group)
            db.session.flush()
            task.group_id = created_group.id

    # 2. Merge custom fields if moving across boards/spaces
    if old_board_id and old_board_id != new_board_id:
        existing_values = TaskCustomFieldValue.query.filter_by(task_id=task.id).all()
        if not existing_values:
            return

        target_fields = BoardCustomField.query.filter_by(board_id=new_board_id).all()
        target_field_map = {f.name.strip().lower(): f for f in target_fields}

        for val in existing_values:
            src_field = BoardCustomField.query.get(val.field_id)
            if not src_field:
                continue

            field_name_key = src_field.name.strip().lower()
            if field_name_key in target_field_map:
                target_field = target_field_map[field_name_key]
                val.field_id = target_field.id

                # Merge dropdown options if applicable
                if src_field.type in ('dropdown', 'multi_select') and src_field.config_json:
                    try:
                        src_cfg = json.loads(src_field.config_json) if isinstance(src_field.config_json, str) else (src_field.config_json or {})
                        tgt_cfg = json.loads(target_field.config_json) if isinstance(target_field.config_json, str) else (target_field.config_json or {})

                        src_opts = src_cfg.get('options', []) if isinstance(src_cfg, dict) else []
                        tgt_opts = tgt_cfg.get('options', []) if isinstance(tgt_cfg, dict) else []

                        opt_names = {o.get('label', o.get('name', str(o))).strip().lower() if isinstance(o, dict) else str(o).strip().lower() for o in tgt_opts}
                        added = False
                        for opt in src_opts:
                            opt_label = opt.get('label', opt.get('name', str(opt))).strip().lower() if isinstance(opt, dict) else str(opt).strip().lower()
                            if opt_label not in opt_names:
                                tgt_opts.append(opt)
                                opt_names.add(opt_label)
                                added = True
                        if added and isinstance(tgt_cfg, dict):
                            tgt_cfg['options'] = tgt_opts
                            target_field.config_json = json.dumps(tgt_cfg)
                    except Exception as e:
                        print("Error merging custom field config options:", e)
            else:
                new_field = BoardCustomField(
                    board_id=new_board_id,
                    name=src_field.name,
                    type=src_field.type,
                    config_json=src_field.config_json
                )
                db.session.add(new_field)
                db.session.flush()

                target_field_map[field_name_key] = new_field
                val.field_id = new_field.id


def doc_is_accessible(doc, actor, role):
    if not doc or not actor:
        return False

    if role == 'superadmin':
        return True

    # 1. If user has access to the parent board, they have access to the doc
    if doc.board and board_is_accessible(doc.board, actor, role):
        return True

    # 2. Check if the document is public
    if doc.is_public:
        return True

    # 3. Check if shared with user directly
    if doc.shared_user_ids:
        try:
            import json
            shared_users = json.loads(doc.shared_user_ids)
            if actor.id in shared_users:
                return True
        except:
            pass

    # 4. Check if shared with user's department
    if doc.shared_dept_ids and role == 'staff':
        try:
            import json
            shared_depts = json.loads(doc.shared_dept_ids)
            dept_ids = [d.id for d in actor.departments] if hasattr(actor, 'departments') else []
            if any(d_id in shared_depts for d_id in dept_ids):
                return True
        except:
            pass

    return False


def ensure_board_access(board, actor, role):
    if board_is_accessible(board, actor, role):
        return True
    return False


def sync_board_access(board, actor, role, access_members):
    board.access_members.clear()

    if not board.is_private:
        return

    normalized_members = {}
    if role == 'superadmin':
        normalized_members[f'superadmin_{actor.id}'] = ('superadmin', actor.id)
    else:
        normalized_members[f'staff_{actor.id}'] = ('staff', actor.id)

    for member in access_members or []:
        member_role = member.get('role')
        member_id = member.get('id')
        if member_role not in {'staff', 'superadmin'} or not member_id:
            continue
        normalized_members[f'{member_role}_{member_id}'] = (member_role, member_id)

    for member_role, member_id in normalized_members.values():
        board.access_members.append(
            BoardAccessMember(
                staff_id=member_id if member_role == 'staff' else None,
                super_admin_id=member_id if member_role == 'superadmin' else None,
            )
        )


def serialize_board_with_groups(board):
    groups_data = []
    groups = BoardGroup.query.filter_by(board_id=board.id).order_by(BoardGroup.position.asc()).all()
    for group in groups:
        tasks = BoardTask.query.filter_by(group_id=group.id).order_by(BoardTask.position.asc()).all()
        group_dict = group.to_dict()
        group_dict['tasks'] = [task.to_dict() for task in tasks]
        groups_data.append(group_dict)

    board_dict = board.to_dict()
    board_dict['groups'] = groups_data
    return board_dict


def get_board_or_404_with_access(board_id, actor, role):
    board = Board.get_by_id_or_public_id(board_id)
    if not board:
        from flask import abort
        abort(404)
    if not ensure_board_access(board, actor, role):
        return None
    return board


def get_board_from_group_or_403(group_id, actor, role):
    group = BoardGroup.query.get_or_404(group_id)
    if not ensure_board_access(group.board, actor, role):
        return None, None
    return group, group.board


def get_task_and_board_or_403(task_id, actor, role):
    task = BoardTask.query.get_or_404(task_id)
    board = task.group.board
    if not ensure_board_access(board, actor, role):
        return None, None
    return task, board


def normalize_task_assignees(data):
    assignees = data.get('assignees')
    if assignees is None and 'assignee_id' in data:
        assignee_id = data.get('assignee_id')
        assignee_role = data.get('assignee_role')
        assignees = [{'id': assignee_id, 'role': assignee_role}] if assignee_id and assignee_role else []

    normalized = {}
    for assignee in assignees or []:
        assignee_id = assignee.get('id')
        assignee_role = assignee.get('role')
        if not assignee_id or assignee_role not in {'staff', 'superadmin', 'department'}:
            continue
        normalized[f'{assignee_role}_{assignee_id}'] = {
            'id': int(assignee_id),
            'role': assignee_role
        }
    return list(normalized.values())


def sync_task_assignees(task, assignees):
    task.assignees.clear()
    task.responsible_staff_id = None
    task.responsible_super_admin_id = None

    for index, assignee in enumerate(assignees):
        assignee_role = assignee['role']
        assignee_id = assignee['id']
        task.assignees.append(
            BoardTaskAssignee(
                staff_id=assignee_id if assignee_role == 'staff' else None,
                super_admin_id=assignee_id if assignee_role == 'superadmin' else None,
                department_id=assignee_id if assignee_role == 'department' else None,
            )
        )
        if index == 0:
            if assignee_role == 'superadmin':
                task.responsible_super_admin_id = assignee_id
            elif assignee_role == 'staff':
                task.responsible_staff_id = assignee_id


def task_assignee_keys(task):
    return {
        f"{assignee['role']}_{assignee['id']}"
        for assignee in task.to_dict().get('assignees', [])
        if assignee.get('id') and assignee.get('role')
    }


@board_bp.route('', methods=['GET'])
@jwt_required()
def get_boards():
    actor, role = get_actor()
    include_archived = request.args.get('include_archived', 'false') == 'true'
    only_archived = request.args.get('only_archived', 'false') == 'true'
    
    query = Board.query.order_by(Board.created_at.desc())
    if only_archived:
        query = query.filter_by(is_archived=True)
    elif not include_archived:
        query = query.filter_by(is_archived=False)
        
    boards = query.all()
    visible_boards = [board.to_dict() for board in boards if board_is_accessible(board, actor, role)]
    return jsonify(visible_boards), 200


@board_bp.route('/all-tasks', methods=['GET'])
@jwt_required()
def get_all_board_tasks():
    actor, role = get_actor()
    boards = Board.query.order_by(Board.created_at.desc()).all()
    response = []

    for board in boards:
        if not board_is_accessible(board, actor, role):
            continue
        groups = BoardGroup.query.filter_by(board_id=board.id).order_by(BoardGroup.position.asc()).all()
        for group in groups:
            tasks = BoardTask.query.filter_by(group_id=group.id).order_by(BoardTask.position.asc()).all()
            for task in tasks:
                task_dict = task.to_dict()
                task_dict['board_id'] = board.id
                task_dict['board_name'] = board.name
                task_dict['board_is_private'] = board.is_private
                task_dict['group_name'] = group.name
                task_dict['group_color'] = group.color
                response.append(task_dict)

    return jsonify(response), 200


@board_bp.route('', methods=['POST'])
@jwt_required()
def create_board():
    actor, role = get_actor()
    if not actor:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json() or {}
    name = data.get('name')
    description = data.get('description')
    is_private = data.get('is_private', False)
    access_members = data.get('access_members', [])
    parent_id_val = data.get('parent_id')
    is_folder = data.get('is_folder', False)

    if not name:
        return jsonify({"error": "Board name is required"}), 400

    parent_id = None
    if parent_id_val:
        parent_board = Board.get_by_id_or_public_id(parent_id_val)
        parent_id = parent_board.id if parent_board else None

    new_board = Board(
        name=name,
        description=description,
        is_private=bool(is_private),
        parent_id=parent_id,
        is_folder=bool(is_folder)
    )
    db.session.add(new_board)
    db.session.flush()

    sync_board_access(new_board, actor, role, access_members)

    if not is_folder:
        group = BoardGroup(board_id=new_board.id, name="List", color="#673de6", position=0)
        db.session.add(group)

    db.session.commit()
    return jsonify(new_board.to_dict()), 201


@board_bp.route('/<string:board_id>', methods=['GET'])
@jwt_required()
def get_board(board_id):
    actor, role = get_actor()
    board = get_board_or_404_with_access(board_id, actor, role)
    if not board:
        return jsonify({"error": "Forbidden"}), 403
    return jsonify(serialize_board_with_groups(board)), 200


@board_bp.route('/<string:board_id>', methods=['PUT'])
@jwt_required()
def update_board(board_id):
    import json
    actor, role = get_actor()
    board = get_board_or_404_with_access(board_id, actor, role)
    if not board:
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json() or {}
    board.name = data.get('name', board.name)
    board.description = data.get('description', board.description)
    board.is_private = data.get('is_private', board.is_private)
    if 'parent_id' in data:
        p_id = data.get('parent_id')
        if p_id:
            parent_board = Board.get_by_id_or_public_id(p_id)
            board.parent_id = parent_board.id if parent_board else None
        else:
            board.parent_id = None
    if 'is_folder' in data:
        board.is_folder = bool(data.get('is_folder'))
    if 'custom_statuses' in data:
        board.custom_statuses = json.dumps(data.get('custom_statuses'))
        
    # Branding & Project settings
    if 'color' in data:
        board.color = data.get('color')
    if 'icon' in data:
        board.icon = data.get('icon')
    if 'is_template' in data:
        board.is_template = bool(data.get('is_template'))
    if 'is_archived' in data:
        board.is_archived = bool(data.get('is_archived'))
    if 'status' in data:
        board.status = data.get('status')
    if 'priority' in data:
        board.priority = data.get('priority')
    if 'category' in data:
        board.category = data.get('category')
    if 'budget_amount' in data:
        try:
            board.budget_amount = float(data.get('budget_amount')) if data.get('budget_amount') is not None else None
        except:
            pass

    sync_board_access(board, actor, role, data.get('access_members', board.to_dict().get('access_members', [])))
    db.session.commit()
    return jsonify(board.to_dict()), 200


@board_bp.route('/<string:board_id>', methods=['DELETE'])
@jwt_required()
def delete_board(board_id):
    actor, role = get_actor()
    board = get_board_or_404_with_access(board_id, actor, role)
    if not board:
        return jsonify({"error": "Forbidden"}), 403
    db.session.delete(board)
    db.session.commit()
    return jsonify({"message": "Board deleted successfully"}), 200


@board_bp.route('/<string:board_id>/groups', methods=['POST'])
@jwt_required()
def create_group(board_id):
    actor, role = get_actor()
    board = get_board_or_404_with_access(board_id, actor, role)
    if not board:
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json() or {}
    name = data.get('name')
    color = data.get('color', '#673de6')

    if not name:
        return jsonify({"error": "Group name is required"}), 400

    last_group = BoardGroup.query.filter_by(board_id=board.id).order_by(BoardGroup.position.desc()).first()
    position = (last_group.position + 1) if last_group else 0

    new_group = BoardGroup(board_id=board.id, name=name, color=color, position=position)
    db.session.add(new_group)
    db.session.commit()

    group_dict = new_group.to_dict()
    group_dict['tasks'] = []
    return jsonify(group_dict), 201


@board_bp.route('/groups/<int:group_id>', methods=['PUT'])
@jwt_required()
def update_group(group_id):
    actor, role = get_actor()
    group = BoardGroup.query.get_or_404(group_id)
    if not ensure_board_access(group.board, actor, role):
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json() or {}
    group.name = data.get('name', group.name)
    group.color = data.get('color', group.color)
    group.position = data.get('position', group.position)
    db.session.commit()
    return jsonify(group.to_dict()), 200


@board_bp.route('/groups/<int:group_id>', methods=['DELETE'])
@jwt_required()
def delete_group(group_id):
    actor, role = get_actor()
    group = BoardGroup.query.get_or_404(group_id)
    if not ensure_board_access(group.board, actor, role):
        return jsonify({"error": "Forbidden"}), 403

    db.session.delete(group)
    db.session.commit()
    return jsonify({"message": "Group deleted successfully"}), 200


@board_bp.route('/groups/<int:group_id>/tasks', methods=['POST'])
@jwt_required()
def create_task(group_id):
    actor, role = get_actor()
    group, board = get_board_from_group_or_403(group_id, actor, role)
    if not group:
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json() or {}
    title = data.get('title')

    if not title:
        return jsonify({"error": "Task title is required"}), 400

    last_task = BoardTask.query.filter_by(group_id=group.id).order_by(BoardTask.position.desc()).first()
    position = (last_task.position + 1) if last_task else 0

    new_task = BoardTask(group_id=group.id, title=title, position=position)
    
    if 'status' in data:
        new_task.status = data['status']
    if 'priority' in data:
        new_task.priority = data['priority']
    if 'notes' in data:
        new_task.notes = data['notes']
    if 'category' in data:
        new_task.category = data['category']
    if 'recurring_settings' in data:
        new_task.recurring_settings = data['recurring_settings']
    if 'dependency_task_id' in data:
        new_task.dependency_task_id = data['dependency_task_id']
    if 'parent_task_id' in data:
        new_task.parent_task_id = data['parent_task_id']
    if 'tags' in data:
        new_task.tags = data['tags']
    if 'description_html' in data:
        new_task.description_html = data['description_html']
        
    if data.get('start_date'):
        try:
            new_task.start_date = datetime.strptime(data['start_date'].split('T')[0], "%Y-%m-%d").date()
        except ValueError:
            pass

    if data.get('due_date'):
        date_str = data['due_date']
        try:
            new_task.due_date = datetime.strptime(date_str.split('T')[0], "%Y-%m-%d").date()
        except ValueError:
            pass

    if 'assignees' in data or ('assignee_id' in data and 'assignee_role' in data):
        sync_task_assignees(new_task, normalize_task_assignees(data))

    if board.is_personal:
        if role == 'superadmin':
            new_task.responsible_super_admin_id = actor.id
        else:
            new_task.responsible_staff_id = actor.id

    db.session.add(new_task)
    db.session.flush()

    # Log task creation in task history
    from app.models.board_model import BoardTaskHistory
    db.session.add(BoardTaskHistory(task_id=new_task.id, actor_name=actor.name, action="Created task"))

    for assignee in new_task.assignees:
        from app.utils.notifications import enqueue_user_notification
        user_id = assignee.super_admin_id if assignee.super_admin_id else assignee.staff_id
        user_role = 'superadmin' if assignee.super_admin_id else 'staff'
        enqueue_user_notification(
            user_id=user_id,
            user_role=user_role,
            message=f"{actor.name} assigned you the task: '{new_task.title}' on board '{board.name}'",
            category='assignment',
            target_type='Board',
            target_id=board.id,
            target_link=f"/admin/boards/{board.id}?task={new_task.id}"
        )

    # Mentions handling inside Task Notes or HTML description
    mentions = data.get('mentions', [])
    if mentions and (new_task.notes or new_task.description_html):
        content = new_task.notes or new_task.description_html
        new_update = TaskUpdate(task_id=new_task.id, content=content, sender_name=actor.name)
        if role == 'superadmin':
            new_update.sender_super_admin_id = actor.id
        else:
            new_update.sender_staff_id = actor.id
        db.session.add(new_update)
        db.session.flush()

        notified_user_keys = set()
        for mention in mentions:
            mention_type = mention.get('type')
            mention_id = mention.get('id')
            message = f"{actor.name} @mentioned you in task '{new_task.title}' on board '{board.name}'"

            from app.utils.notifications import enqueue_user_notification

            if mention_type == 'staff':
                key = f"staff_{mention_id}"
                if key not in notified_user_keys:
                    notified_user_keys.add(key)
                    enqueue_user_notification(
                        user_id=mention_id,
                        user_role='staff',
                        message=message,
                        category='mention',
                        target_type='Board',
                        target_id=board.id,
                        target_link=f"/admin/boards/{board.id}?task={new_task.id}"
                    )
            elif mention_type == 'superadmin':
                key = f"superadmin_{mention_id}"
                if key not in notified_user_keys:
                    notified_user_keys.add(key)
                    enqueue_user_notification(
                        user_id=mention_id,
                        user_role='superadmin',
                        message=message,
                        category='mention',
                        target_type='Board',
                        target_id=board.id,
                        target_link=f"/admin/boards/{board.id}?task={new_task.id}"
                    )
            elif mention_type == 'department':
                department = Department.query.get(mention_id)
                if department:
                    for member in department.staff_members:
                        key = f"staff_{member.id}"
                        if key not in notified_user_keys and (role != 'staff' or actor.id != member.id):
                            notified_user_keys.add(key)
                            enqueue_user_notification(
                                user_id=member.id,
                                user_role='staff',
                                message=f"{actor.name} @mentioned your department ({department.name}) in task '{new_task.title}' on board '{board.name}'",
                                category='mention',
                                target_type='Board',
                                target_id=board.id,
                                target_link=f"/admin/boards/{board.id}?task={new_task.id}"
                            )

    db.session.commit()
    return jsonify(new_task.to_dict()), 201


@board_bp.route('/tasks/<int:task_id>', methods=['PUT'])
@jwt_required()
def update_task(task_id):
    actor, role = get_actor()
    task, board = get_task_and_board_or_403(task_id, actor, role)
    if not task:
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json() or {}
    old_assignee_keys = task_assignee_keys(task)

    from app.models.board_model import BoardTaskHistory
    changes = []

    if 'title' in data and data['title'] != task.title:
        changes.append(f"Changed name from '{task.title}' to '{data['title']}'")
        task.title = data['title']
    if 'status' in data and data['status'] != task.status:
        if data['status'] == 'Done':
            incomplete_subtasks = [
                subtask.title for subtask in task.subtasks if subtask.status != 'Done'
            ]
            if incomplete_subtasks:
                return jsonify({
                    "error": "Complete all subtasks before marking this task complete.",
                    "incomplete_subtasks": incomplete_subtasks
                }), 400
        changes.append(f"Changed status from '{task.status}' to '{data['status']}'")
        task.status = data['status']
    if 'priority' in data and data['priority'] != task.priority:
        changes.append(f"Changed priority from '{task.priority}' to '{data['priority']}'")
        task.priority = data['priority']
    if 'notes' in data and data['notes'] != task.notes:
        task.notes = data['notes']
    if 'category' in data and data['category'] != task.category:
        changes.append(f"Changed category to '{data['category']}'")
        task.category = data['category']
    if 'recurring_settings' in data and data['recurring_settings'] != task.recurring_settings:
        task.recurring_settings = data['recurring_settings']
    if 'dependency_task_id' in data and data['dependency_task_id'] != task.dependency_task_id:
        task.dependency_task_id = data['dependency_task_id']
    if 'parent_task_id' in data and data['parent_task_id'] != task.parent_task_id:
        task.parent_task_id = data['parent_task_id']
    if 'tags' in data and data['tags'] != task.tags:
        task.tags = data['tags']
    if 'description_html' in data and data['description_html'] != task.description_html:
        task.description_html = data['description_html']

    if 'start_date' in data:
        date_str = data['start_date']
        if date_str:
            try:
                val = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                val = None
        else:
            val = None
        if val != task.start_date:
            changes.append(f"Changed start date to '{date_str}'")
            task.start_date = val

    if 'due_date' in data:
        date_str = data['due_date']
        if date_str:
            try:
                val = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                val = None
        else:
            val = None
        if val != task.due_date:
            changes.append(f"Changed due date to '{date_str}'")
            task.due_date = val

    if 'assignees' in data or ('assignee_id' in data and 'assignee_role' in data):
        next_assignees = normalize_task_assignees(data)
        next_keys = {f"{assignee['role']}_{assignee['id']}" for assignee in next_assignees}
        if next_keys != old_assignee_keys:
            changes.append("Changed assignees")
        sync_task_assignees(task, next_assignees)

    if 'group_id' in data and data['group_id'] != task.group_id:
        merge_task_into_target_board(task, data['group_id'])
        changes.append("Moved task to new group/board")

    # Save changes in history
    for change in changes:
        db.session.add(BoardTaskHistory(task_id=task.id, actor_name=actor.name, action=change))

    db.session.commit()

    new_assignee_keys = task_assignee_keys(task)
    added_assignee_keys = new_assignee_keys - old_assignee_keys

    if added_assignee_keys:
        message = f"{actor.name} assigned you the task: '{task.title}' on board '{board.name}'"
        for assignee_key in added_assignee_keys:
            assignee_role, raw_id = assignee_key.split('_', 1)
            from app.utils.notifications import enqueue_user_notification
            enqueue_user_notification(
                user_id=int(raw_id),
                user_role=assignee_role,
                message=message,
                category='assignment',
                target_type='Board',
                target_id=board.id,
                target_link=f"/admin/boards/{board.id}?task={task.id}"
            )
        db.session.commit()

    return jsonify(task.to_dict()), 200


@board_bp.route('/tasks/<int:task_id>', methods=['DELETE'])
@jwt_required()
def delete_task(task_id):
    actor, role = get_actor()
    task, board = get_task_and_board_or_403(task_id, actor, role)
    if not task:
        return jsonify({"error": "Forbidden"}), 403

    db.session.delete(task)
    db.session.commit()
    return jsonify({"message": "Task deleted successfully"}), 200


@board_bp.route('/tasks/<int:task_id>/updates', methods=['GET'])
@jwt_required()
def get_task_updates(task_id):
    actor, role = get_actor()
    task, board = get_task_and_board_or_403(task_id, actor, role)
    if not task:
        return jsonify({"error": "Forbidden"}), 403

    updates = TaskUpdate.query.filter_by(task_id=task.id).order_by(TaskUpdate.created_at.desc()).all()
    return jsonify([update.to_dict() for update in updates]), 200


@board_bp.route('/tasks/<int:task_id>/updates', methods=['POST'])
@jwt_required()
def create_task_update(task_id):
    from app.utils.notifications import enqueue_user_notification
    try:
        actor, role = get_actor()
        task, board = get_task_and_board_or_403(task_id, actor, role)
        if not task or not actor:
            return jsonify({"error": "Forbidden"}), 403

        data = request.get_json() or {}
        content = data.get('content')
        mentions = data.get('mentions', [])

        if not content:
            return jsonify({"error": "Content is required"}), 400

        send_via_email = data.get('send_via_email', False)
        to_email = data.get('to_email')
        cc_email = data.get('cc_email')
        subject_override = data.get('subject')

        new_update = TaskUpdate(task_id=task.id, content=content, sender_name=actor.name)
        if role == 'superadmin':
            new_update.sender_super_admin_id = actor.id
        else:
            new_update.sender_staff_id = actor.id

        db.session.add(new_update)
        db.session.flush()

        notified_user_keys = set()
        for mention in mentions:
            mention_type = mention.get('type')
            mention_id = mention.get('id')
            message = f"{actor.name} @mentioned you in task '{task.title}' on board '{board.name}'"

            if mention_type == 'staff':
                key = f"staff_{mention_id}"
                if key not in notified_user_keys:
                    notified_user_keys.add(key)
                    enqueue_user_notification(
                        user_id=mention_id,
                        user_role='staff',
                        message=message,
                        category='mention',
                        target_type='Board',
                        target_id=board.id,
                        target_link=f"/admin/boards/{board.id}?task={task.id}"
                    )
            elif mention_type == 'superadmin':
                key = f"superadmin_{mention_id}"
                if key not in notified_user_keys:
                    notified_user_keys.add(key)
                    enqueue_user_notification(
                        user_id=mention_id,
                        user_role='superadmin',
                        message=message,
                        category='mention',
                        target_type='Board',
                        target_id=board.id,
                        target_link=f"/admin/boards/{board.id}?task={task.id}"
                    )
            elif mention_type == 'department':
                department = Department.query.get(mention_id)
                if department:
                    for member in department.staff_members:
                        key = f"staff_{member.id}"
                        if key not in notified_user_keys and (role != 'staff' or actor.id != member.id):
                            notified_user_keys.add(key)
                            enqueue_user_notification(
                                user_id=member.id,
                                user_role='staff',
                                message=f"{actor.name} @mentioned your department ({department.name}) in task '{task.title}' on board '{board.name}'",
                                category='mention',
                                target_type='Board',
                                target_id=board.id,
                                target_link=f"/admin/boards/{board.id}?task={task.id}"
                            )

        # Notify assignees and participants (other commenters) on the task
        notified_users = set(notified_user_keys)
        
        # 1. Notify Assignees
        assignees_to_notify = []
        if task.responsible_staff_id:
            assignees_to_notify.append(('staff', task.responsible_staff_id))
        if task.responsible_super_admin_id:
            assignees_to_notify.append(('superadmin', task.responsible_super_admin_id))
        
        for ass in task.assignees:
            if ass.staff_id:
                assignees_to_notify.append(('staff', ass.staff_id))
            elif ass.super_admin_id:
                assignees_to_notify.append(('superadmin', ass.super_admin_id))
                
        for u_role, u_id in assignees_to_notify:
            key = f"{u_role}_{u_id}"
            if key != f"{role}_{actor.id}" and key not in notified_users:
                notified_users.add(key)
                enqueue_user_notification(
                    user_id=u_id,
                    user_role=u_role,
                    message=f"{actor.name} commented on task '{task.title}' assigned to you on board '{board.name}'",
                    category='comment',
                    target_type='Board',
                    target_id=board.id,
                    target_link=f"/admin/boards/{board.id}?task={task.id}"
                )
                
        # 2. Notify other comment thread participants
        for old_update in task.updates:
            u_role = 'superadmin' if old_update.sender_super_admin_id else ('staff' if old_update.sender_staff_id else None)
            u_id = old_update.sender_super_admin_id or old_update.sender_staff_id
            if u_role and u_id:
                key = f"{u_role}_{u_id}"
                if key != f"{role}_{actor.id}" and key not in notified_users:
                    notified_users.add(key)
                    enqueue_user_notification(
                        user_id=u_id,
                        user_role=u_role,
                        message=f"{actor.name} commented on task '{task.title}' that you commented on",
                        category='comment',
                        target_type='Board',
                        target_id=board.id,
                        target_link=f"/admin/boards/{board.id}?task={task.id}"
                    )

        db.session.commit()

        # Send comment via email if requested
        if send_via_email:
            try:
                from app.utils.ms_graph_email import is_ms_graph_configured, send_email_via_graph_background, get_department_sender_email, format_task_comment_email
                sender_dept_email = get_department_sender_email(actor)
                email_html = format_task_comment_email(actor.name, task.title, content, board.name)
                
                # Collect unique recipient emails & CC emails
                recipient_emails = set()
                cc_emails = set()

                if cc_email and isinstance(cc_email, str):
                    for addr in cc_email.split(','):
                        clean_addr = addr.strip()
                        if '@' in clean_addr:
                            cc_emails.add(clean_addr)

                # 1. If explicit to_email is provided (e.g. form submitter's email), add it
                has_explicit_to = False
                if to_email and isinstance(to_email, str) and '@' in to_email:
                    for addr in to_email.split(','):
                        clean_addr = addr.strip()
                        if '@' in clean_addr:
                            recipient_emails.add(clean_addr)
                            has_explicit_to = True

                # 2. Add task assignees and responsible staff ONLY IF no explicit to_email was provided
                if not has_explicit_to:
                    from app.models.staff_model import Staff
                    from app.models.super_admin_model import SuperAdmin
                    
                    if task.responsible_staff_id:
                        resp = Staff.query.get(task.responsible_staff_id)
                        if resp and resp.email:
                            recipient_emails.add(resp.email)
                    if task.responsible_super_admin_id:
                        resp = SuperAdmin.query.get(task.responsible_super_admin_id)
                        if resp and resp.email:
                            recipient_emails.add(resp.email)
                    for ass in task.assignees:
                        if ass.staff_id:
                            u = Staff.query.get(ass.staff_id)
                            if u and u.email:
                                recipient_emails.add(u.email)
                        elif ass.super_admin_id:
                            u = SuperAdmin.query.get(ass.super_admin_id)
                            if u and u.email:
                                recipient_emails.add(u.email)
                    
                    if hasattr(actor, 'email') and actor.email in recipient_emails and len(recipient_emails) > 1:
                        recipient_emails.discard(actor.email)
                
                email_subject = subject_override if subject_override else f"[Task Email] {task.title} — {board.name}"

                if recipient_emails:
                    if is_ms_graph_configured():
                        send_email_via_graph_background(
                            subject=email_subject,
                            recipients=list(recipient_emails),
                            html_content=email_html,
                            sender_email=sender_dept_email,
                            cc_recipients=list(cc_emails) if cc_emails else None
                        )
                        print(f"[Email Comment] Sent from {sender_dept_email} to {recipient_emails} (CC: {cc_emails}) with subject '{email_subject}'")
                    else:
                        print(f"[Email Comment Notice] MS_GRAPH credentials not set in .env on localhost. Email from {sender_dept_email} to {recipient_emails} (CC: {cc_emails}) prepared successfully.")
            except Exception as email_err:
                print(f"[Email Comment Error] {email_err}")

        return jsonify(new_update.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        import traceback
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@board_bp.route('/updates/<int:update_id>/like', methods=['POST'])
@jwt_required()
def toggle_like(update_id):
    actor, role = get_actor()
    update = TaskUpdate.query.get_or_404(update_id)
    if not ensure_board_access(update.task.group.board, actor, role):
        return jsonify({"error": "Forbidden"}), 403

    if role == 'superadmin':
        like = TaskUpdateLike.query.filter_by(update_id=update.id, super_admin_id=actor.id).first()
        if like:
            db.session.delete(like)
            liked = False
        else:
            like = TaskUpdateLike(update_id=update.id, super_admin_id=actor.id)
            db.session.add(like)
            liked = True
    else:
        like = TaskUpdateLike.query.filter_by(update_id=update.id, staff_id=actor.id).first()
        if like:
            db.session.delete(like)
            liked = False
        else:
            like = TaskUpdateLike(update_id=update.id, staff_id=actor.id)
            db.session.add(like)
            liked = True

    db.session.commit()
    return jsonify({"liked": liked, "likes_count": len(update.likes)}), 200


@board_bp.route('/updates/<int:update_id>/react', methods=['POST'])
@jwt_required()
def toggle_comment_reaction(update_id):
    actor, role = get_actor()
    update = TaskUpdate.query.get_or_404(update_id)
    if not ensure_board_access(update.task.group.board, actor, role):
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json() or {}
    emoji = data.get('emoji')
    if not emoji:
        return jsonify({"error": "Emoji is required"}), 400

    # Toggle reaction
    if role == 'superadmin':
        reaction = CommentReaction.query.filter_by(
            update_id=update_id,
            emoji=emoji,
            super_admin_id=actor.id
        ).first()
    else:
        reaction = CommentReaction.query.filter_by(
            update_id=update_id,
            emoji=emoji,
            staff_id=actor.id
        ).first()

    if reaction:
        db.session.delete(reaction)
        action = "removed"
    else:
        reaction = CommentReaction(
            update_id=update_id,
            emoji=emoji,
            staff_id=actor.id if role == 'staff' else None,
            super_admin_id=actor.id if role == 'superadmin' else None
        )
        db.session.add(reaction)
        action = "added"

    db.session.commit()
    return jsonify({
        "message": f"Reaction {action}",
        "reactions": [r.to_dict() for r in update.reactions]
    }), 200


@board_bp.route('/updates/<int:update_id>/reply', methods=['POST'])
@jwt_required()
def create_reply(update_id):
    from app.utils.notifications import enqueue_user_notification
    actor, role = get_actor()
    update = TaskUpdate.query.get_or_404(update_id)
    task = update.task
    board = task.group.board
    if not ensure_board_access(board, actor, role):
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json() or {}
    content = data.get('content')
    mentions = data.get('mentions', [])
    reply_to_name = data.get('reply_to_name')

    if not content:
        return jsonify({"error": "Reply content is required"}), 400

    reply = TaskUpdateReply(update_id=update.id, content=content, sender_name=actor.name, reply_to_name=reply_to_name)
    if role == 'superadmin':
        reply.sender_super_admin_id = actor.id
    else:
        reply.sender_staff_id = actor.id

    db.session.add(reply)
    db.session.commit()

    # Process mentions in reply
    notified_user_keys = set()
    for mention in mentions:
        mention_type = mention.get('type')
        mention_id = mention.get('id')
        message = f"{actor.name} @mentioned you in a reply on task '{task.title}' on board '{board.name}'"

        if mention_type == 'staff':
            key = f"staff_{mention_id}"
            if key not in notified_user_keys:
                notified_user_keys.add(key)
                enqueue_user_notification(
                    user_id=mention_id,
                    user_role='staff',
                    message=message,
                    category='mention',
                    target_type='Board',
                    target_id=board.id,
                    target_link=f"/admin/boards/{board.id}?task={task.id}"
                )
        elif mention_type == 'superadmin':
            key = f"superadmin_{mention_id}"
            if key not in notified_user_keys:
                notified_user_keys.add(key)
                enqueue_user_notification(
                    user_id=mention_id,
                    user_role='superadmin',
                    message=message,
                    category='mention',
                    target_type='Board',
                    target_id=board.id,
                    target_link=f"/admin/boards/{board.id}?task={task.id}"
                )

    return jsonify(reply.to_dict()), 201

@board_bp.route('/replies/<int:reply_id>', methods=['PUT'])
@jwt_required()
def update_reply(reply_id):
    actor, role = get_actor()
    reply = TaskUpdateReply.query.get_or_404(reply_id)
    if not ensure_board_access(reply.update.task.group.board, actor, role):
        return jsonify({"error": "Forbidden"}), 403

    is_owner = False
    if role == 'superadmin' and reply.sender_super_admin_id == actor.id:
        is_owner = True
    elif role == 'staff' and reply.sender_staff_id == actor.id:
        is_owner = True

    if role != 'superadmin' and not is_owner:
        return jsonify({"error": "You can only edit your own replies"}), 403

    data = request.get_json() or {}
    content = data.get('content')
    if not content:
        return jsonify({"error": "Content is required"}), 400

    reply.content = content
    db.session.commit()
    return jsonify(reply.to_dict()), 200

@board_bp.route('/replies/<int:reply_id>', methods=['DELETE'])
@jwt_required()
def delete_reply(reply_id):
    actor, role = get_actor()
    reply = TaskUpdateReply.query.get_or_404(reply_id)
    if not ensure_board_access(reply.update.task.group.board, actor, role):
        return jsonify({"error": "Forbidden"}), 403

    is_owner = False
    if role == 'superadmin' and reply.sender_super_admin_id == actor.id:
        is_owner = True
    elif role == 'staff' and reply.sender_staff_id == actor.id:
        is_owner = True

    if role != 'superadmin' and not is_owner:
        return jsonify({"error": "You can only delete your own replies"}), 403

    db.session.delete(reply)
    db.session.commit()
    return jsonify({"message": "Reply deleted"}), 200

@board_bp.route('/updates/<int:update_id>', methods=['PUT'])
@jwt_required()
def update_task_update(update_id):
    actor, role = get_actor()
    update = TaskUpdate.query.get_or_404(update_id)
    if not ensure_board_access(update.task.group.board, actor, role):
        return jsonify({"error": "Forbidden"}), 403

    is_owner = False
    if role == 'superadmin' and update.sender_super_admin_id == actor.id:
        is_owner = True
    elif role == 'staff' and update.sender_staff_id == actor.id:
        is_owner = True

    if role != 'superadmin' and not is_owner:
        return jsonify({"error": "You can only edit your own comments"}), 403

    data = request.get_json() or {}
    content = data.get('content')
    if not content:
        return jsonify({"error": "Content is required"}), 400

    update.content = content
    db.session.commit()
    return jsonify(update.to_dict()), 200

@board_bp.route('/updates/<int:update_id>', methods=['DELETE'])
@jwt_required()
def delete_task_update(update_id):
    actor, role = get_actor()
    update = TaskUpdate.query.get_or_404(update_id)
    if not ensure_board_access(update.task.group.board, actor, role):
        return jsonify({"error": "Forbidden"}), 403

    is_owner = False
    if role == 'superadmin' and update.sender_super_admin_id == actor.id:
        is_owner = True
    elif role == 'staff' and update.sender_staff_id == actor.id:
        is_owner = True

    if role != 'superadmin' and not is_owner:
        return jsonify({"error": "You can only delete your own comments"}), 403

    db.session.delete(update)
    db.session.commit()
    return jsonify({"message": "Comment deleted"}), 200

# Checklist items
@board_bp.route('/tasks/<int:task_id>/checklists', methods=['POST'])
@jwt_required()
def add_checklist_item(task_id):
    actor, role = get_actor()
    task, board = get_task_and_board_or_403(task_id, actor, role)
    if not task:
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json() or {}
    title = data.get('title')
    if not title:
        return jsonify({"error": "Checklist item title is required"}), 400

    from app.models.board_model import BoardTaskChecklistItem
    last_item = BoardTaskChecklistItem.query.filter_by(task_id=task.id).order_by(BoardTaskChecklistItem.position.desc()).first()
    position = (last_item.position + 1) if last_item else 0

    new_item = BoardTaskChecklistItem(task_id=task.id, title=title, position=position)
    db.session.add(new_item)
    db.session.commit()
    return jsonify(new_item.to_dict()), 201


@board_bp.route('/tasks/checklists/<int:item_id>', methods=['PUT'])
@jwt_required()
def update_checklist_item(item_id):
    actor, role = get_actor()
    from app.models.board_model import BoardTaskChecklistItem
    item = BoardTaskChecklistItem.query.get_or_404(item_id)
    if not ensure_board_access(item.task.group.board, actor, role):
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json() or {}
    if 'is_checked' in data:
        item.is_checked = bool(data['is_checked'])
    if 'title' in data:
        item.title = data['title']
    if 'position' in data:
        item.position = data['position']

    db.session.commit()
    return jsonify(item.to_dict()), 200


@board_bp.route('/tasks/checklists/<int:item_id>', methods=['DELETE'])
@jwt_required()
def delete_checklist_item(item_id):
    actor, role = get_actor()
    from app.models.board_model import BoardTaskChecklistItem
    item = BoardTaskChecklistItem.query.get_or_404(item_id)
    if not ensure_board_access(item.task.group.board, actor, role):
        return jsonify({"error": "Forbidden"}), 403

    db.session.delete(item)
    db.session.commit()
    return jsonify({"message": "Checklist item deleted successfully"}), 200


@board_bp.route('/tasks/<int:task_id>/checklists/reorder', methods=['POST'])
@jwt_required()
def reorder_checklist_items(task_id):
    actor, role = get_actor()
    from app.models.board_model import BoardTask, BoardTaskChecklistItem
    task = BoardTask.query.get_or_404(task_id)
    if not ensure_board_access(task.group.board, actor, role):
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json() or {}
    ordered_ids = data.get('ordered_ids', [])

    for idx, item_id in enumerate(ordered_ids):
        item = BoardTaskChecklistItem.query.filter_by(id=item_id, task_id=task.id).first()
        if item:
            item.position = idx

    db.session.commit()
    return jsonify({"message": "Checklist items reordered successfully"}), 200


# Watchers / Followers
@board_bp.route('/tasks/<int:task_id>/watchers', methods=['POST'])
@jwt_required()
def add_task_watcher(task_id):
    actor, role = get_actor()
    task, board = get_task_and_board_or_403(task_id, actor, role)
    if not task:
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json() or {}
    watcher_id = data.get('watcher_id')
    watcher_role = data.get('watcher_role')

    if not watcher_id or not watcher_role:
        return jsonify({"error": "Watcher ID and role are required"}), 400

    from app.models.board_model import BoardTaskWatcher
    if watcher_role == 'superadmin':
        existing = BoardTaskWatcher.query.filter_by(task_id=task.id, super_admin_id=watcher_id).first()
    else:
        existing = BoardTaskWatcher.query.filter_by(task_id=task.id, staff_id=watcher_id).first()

    if existing:
        return jsonify(existing.to_dict()), 200

    new_watcher = BoardTaskWatcher(
        task_id=task.id,
        staff_id=watcher_id if watcher_role != 'superadmin' else None,
        super_admin_id=watcher_id if watcher_role == 'superadmin' else None
    )
    db.session.add(new_watcher)
    db.session.commit()
    return jsonify(new_watcher.to_dict()), 201


@board_bp.route('/tasks/<int:task_id>/watchers', methods=['DELETE'])
@jwt_required()
def remove_task_watcher(task_id):
    actor, role = get_actor()
    task, board = get_task_and_board_or_403(task_id, actor, role)
    if not task:
        return jsonify({"error": "Forbidden"}), 403

    watcher_id = request.args.get('watcher_id', type=int)
    watcher_role = request.args.get('watcher_role')

    if not watcher_id or not watcher_role:
        return jsonify({"error": "Watcher ID and role are required"}), 400

    from app.models.board_model import BoardTaskWatcher
    if watcher_role == 'superadmin':
        watcher = BoardTaskWatcher.query.filter_by(task_id=task.id, super_admin_id=watcher_id).first()
    else:
        watcher = BoardTaskWatcher.query.filter_by(task_id=task.id, staff_id=watcher_id).first()

    if watcher:
        db.session.delete(watcher)
        db.session.commit()

    return jsonify({"message": "Watcher removed successfully"}), 200


# Attachments
@board_bp.route('/tasks/<int:task_id>/attachments', methods=['POST'])
@jwt_required()
def upload_attachment(task_id):
    actor, role = get_actor()
    task, board = get_task_and_board_or_403(task_id, actor, role)
    if not task:
        return jsonify({"error": "Forbidden"}), 403

    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400

    import os
    import uuid
    from werkzeug.utils import secure_filename
    from flask import current_app

    upload_folder = os.path.join(current_app.root_path, 'static', 'uploads')
    os.makedirs(upload_folder, exist_ok=True)

    filename = secure_filename(file.filename)
    unique_filename = f"{uuid.uuid4().hex}_{filename}"
    file_path = os.path.join(upload_folder, unique_filename)
    file.save(file_path)

    relative_path = f"/static/uploads/{unique_filename}"

    from app.models.board_model import BoardTaskAttachment
    new_attachment = BoardTaskAttachment(
        task_id=task.id,
        filename=filename,
        file_path=relative_path,
        uploaded_by_name=actor.name
    )
    db.session.add(new_attachment)
    db.session.commit()

    # Log attachment in history
    from app.models.board_model import BoardTaskHistory
    db.session.add(BoardTaskHistory(task_id=task.id, actor_name=actor.name, action=f"Uploaded attachment: '{filename}'"))
    db.session.commit()

    return jsonify(new_attachment.to_dict()), 201


@board_bp.route('/tasks/attachments/<int:attachment_id>', methods=['DELETE'])
@jwt_required()
def delete_attachment(attachment_id):
    actor, role = get_actor()
    from app.models.board_model import BoardTaskAttachment
    attachment = BoardTaskAttachment.query.get_or_404(attachment_id)
    if not ensure_board_access(attachment.task.group.board, actor, role):
        return jsonify({"error": "Forbidden"}), 403

    import os
    from flask import current_app
    file_path = os.path.join(current_app.root_path, attachment.file_path.lstrip('/'))
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
        except Exception:
            pass

    # Log deletion in history
    from app.models.board_model import BoardTaskHistory
    db.session.add(BoardTaskHistory(task_id=attachment.task_id, actor_name=actor.name, action=f"Deleted attachment: '{attachment.filename}'"))

    db.session.delete(attachment)
    db.session.commit()
    return jsonify({"message": "Attachment deleted successfully"}), 200


# History Log
@board_bp.route('/tasks/<int:task_id>/history', methods=['GET'])
@jwt_required()
def get_task_history(task_id):
    actor, role = get_actor()
    task, board = get_task_and_board_or_403(task_id, actor, role)
    if not task:
        return jsonify({"error": "Forbidden"}), 403

    history = task.history_logs
    return jsonify([h.to_dict() for h in history]), 200


# Task Templates
@board_bp.route('/task-templates', methods=['GET'])
@jwt_required()
def get_task_templates():
    from app.models.board_model import BoardTaskTemplate
    templates = BoardTaskTemplate.query.order_by(BoardTaskTemplate.created_at.desc()).all()
    return jsonify([t.to_dict() for t in templates]), 200


@board_bp.route('/task-templates', methods=['POST'])
@jwt_required()
def create_task_template():
    data = request.get_json() or {}
    title = data.get('title')
    if not title:
        return jsonify({"error": "Template title is required"}), 400

    from app.models.board_model import BoardTaskTemplate
    new_template = BoardTaskTemplate(
        title=title,
        notes=data.get('notes'),
        priority=data.get('priority', 'Normal'),
        category=data.get('category'),
        tags=data.get('tags')
    )
    db.session.add(new_template)
    db.session.commit()
    return jsonify(new_template.to_dict()), 201


@board_bp.route('/task-templates/<int:template_id>', methods=['DELETE'])
@jwt_required()
def delete_task_template(template_id):
    from app.models.board_model import BoardTaskTemplate
    template = BoardTaskTemplate.query.get_or_404(template_id)
    db.session.delete(template)
    db.session.commit()
    return jsonify({"message": "Template deleted successfully"}), 200


# ─── Calendar Events ─────────────────────────────────────────

@board_bp.route('/calendar-events', methods=['GET'])
@jwt_required()
def get_calendar_events():
    actor, role = get_actor()
    if not actor:
        return jsonify({'error': 'Unauthorized'}), 401

    start = request.args.get('start')
    end = request.args.get('end')
    board_id = request.args.get('board_id')

    query = CalendarEvent.query
    if board_id:
        board_record = Board.get_by_id_or_public_id(board_id)
        if board_record:
            query = query.filter_by(board_id=board_record.id)
        else:
            query = query.filter(CalendarEvent.id == -1)
    if start:
        query = query.filter(CalendarEvent.start_datetime >= start)
    if end:
        query = query.filter(CalendarEvent.start_datetime <= end)

    events = query.order_by(CalendarEvent.start_datetime.asc()).all()
    return jsonify([e.to_dict() for e in events]), 200


@board_bp.route('/calendar-events', methods=['POST'])
@jwt_required()
def create_calendar_event():
    actor, role = get_actor()
    if not actor:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    if not data or not data.get('title') or not data.get('start_datetime'):
        return jsonify({'error': 'Title and start_datetime are required'}), 400

    board_id_val = data.get('board_id')
    board_id_resolved = None
    if board_id_val:
        board_rec = Board.get_by_id_or_public_id(board_id_val)
        board_id_resolved = board_rec.id if board_rec else None

    event = CalendarEvent(
        board_id=board_id_resolved,
        title=data['title'],
        description=data.get('description', ''),
        start_datetime=datetime.fromisoformat(data['start_datetime'].replace('Z', '+00:00')),
        end_datetime=datetime.fromisoformat(data['end_datetime'].replace('Z', '+00:00')) if data.get('end_datetime') else None,
        all_day=data.get('all_day', False),
        color=data.get('color', '#673de6'),
        recurring_rule=data.get('recurring_rule'),
        reminder_minutes=data.get('reminder_minutes'),
        created_by_name=actor.name,
        linked_task_id=data.get('linked_task_id'),
    )
    db.session.add(event)
    db.session.commit()
    return jsonify(event.to_dict()), 201


@board_bp.route('/calendar-events/<int:event_id>', methods=['PUT'])
@jwt_required()
def update_calendar_event(event_id):
    actor, role = get_actor()
    if not actor:
        return jsonify({'error': 'Unauthorized'}), 401

    event = CalendarEvent.query.get(event_id)
    if not event:
        return jsonify({'error': 'Event not found'}), 404

    data = request.get_json()
    if data.get('title'):
        event.title = data['title']
    if data.get('description') is not None:
        event.description = data['description']
    if data.get('start_datetime'):
        event.start_datetime = datetime.fromisoformat(data['start_datetime'].replace('Z', '+00:00'))
    if data.get('end_datetime'):
        event.end_datetime = datetime.fromisoformat(data['end_datetime'].replace('Z', '+00:00'))
    if 'all_day' in data:
        event.all_day = data['all_day']
    if data.get('color'):
        event.color = data['color']
    if 'recurring_rule' in data:
        event.recurring_rule = data['recurring_rule']
    if 'reminder_minutes' in data:
        event.reminder_minutes = data['reminder_minutes']
    if 'linked_task_id' in data:
        event.linked_task_id = data['linked_task_id']

    db.session.commit()
    return jsonify(event.to_dict()), 200


@board_bp.route('/calendar-events/<int:event_id>', methods=['DELETE'])
@jwt_required()
def delete_calendar_event(event_id):
    actor, role = get_actor()
    if not actor:
        return jsonify({'error': 'Unauthorized'}), 401

    event = CalendarEvent.query.get(event_id)
    if not event:
        return jsonify({'error': 'Event not found'}), 404

    db.session.delete(event)
    db.session.commit()
    return jsonify({'message': 'Event deleted'}), 200


@board_bp.route('/calendar-tasks', methods=['GET'])
@jwt_required()
def get_calendar_tasks():
    """Get tasks with dates for calendar rendering."""
    actor, role = get_actor()
    if not actor:
        return jsonify({'error': 'Unauthorized'}), 401

    board_id = request.args.get('board_id')
    start = request.args.get('start')
    end = request.args.get('end')

    query = BoardTask.query.join(BoardGroup)
    if board_id:
        board_record = Board.get_by_id_or_public_id(board_id)
        if board_record:
            query = query.filter(BoardGroup.board_id == board_record.id)
        else:
            query = query.filter(BoardTask.id == -1)

    # Tasks that have either a due_date or start_date within the range
    from sqlalchemy import or_
    if start and end:
        query = query.filter(
            or_(
                BoardTask.due_date.between(start, end),
                BoardTask.start_date.between(start, end),
            )
        )

    tasks = query.all()
    result = []
    for t in tasks:
        task_dict = t.to_dict()
        task_dict['group_name'] = t.group.name if t.group else ''
        task_dict['group_color'] = t.group.color if t.group else '#673de6'
        task_dict['board_name'] = t.group.board.name if t.group and t.group.board else ''
        result.append(task_dict)

    return jsonify(result), 200


# ─── Time Tracking Endpoints ─────────────────────────────────────────

@board_bp.route('/tasks/<int:task_id>/time-entries', methods=['GET'])
@jwt_required()
def get_task_time_entries(task_id):
    actor, role = get_actor()
    if not actor:
        return jsonify({'error': 'Unauthorized'}), 401

    task = BoardTask.query.get(task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404

    entries = TaskTimeEntry.query.filter_by(task_id=task_id).order_by(TaskTimeEntry.start_time.desc()).all()
    return jsonify([e.to_dict() for e in entries]), 200


@board_bp.route('/tasks/<int:task_id>/time-entries', methods=['POST'])
@jwt_required()
def create_task_time_entry(task_id):
    actor, role = get_actor()
    if not actor:
        return jsonify({'error': 'Unauthorized'}), 401

    task = BoardTask.query.get(task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404

    data = request.get_json() or {}
    start_time_str = data.get('start_time')
    if not start_time_str:
        return jsonify({'error': 'start_time is required'}), 400

    try:
        start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
    except Exception:
        return jsonify({'error': 'Invalid start_time format'}), 400

    end_time = None
    end_time_str = data.get('end_time')
    if end_time_str:
        try:
            end_time = datetime.fromisoformat(end_time_str.replace('Z', '+00:00'))
        except Exception:
            return jsonify({'error': 'Invalid end_time format'}), 400

    duration_seconds = data.get('duration_seconds', 0)
    if end_time and start_time and not duration_seconds:
        duration_seconds = int((end_time - start_time).total_seconds())

    entry = TaskTimeEntry(
        task_id=task_id,
        user_name=actor.name,
        user_role=role,
        user_id=actor.id,
        start_time=start_time,
        end_time=end_time,
        duration_seconds=duration_seconds,
        description=data.get('description'),
        is_billable=data.get('is_billable', False)
    )
    db.session.add(entry)
    db.session.commit()
    return jsonify(entry.to_dict()), 201


@board_bp.route('/time-entries/<int:entry_id>', methods=['DELETE'])
@jwt_required()
def delete_task_time_entry(entry_id):
    actor, role = get_actor()
    if not actor:
        return jsonify({'error': 'Unauthorized'}), 401

    entry = TaskTimeEntry.query.get(entry_id)
    if not entry:
        return jsonify({'error': 'Time entry not found'}), 404

    db.session.delete(entry)
    db.session.commit()
    return jsonify({'message': 'Time entry deleted'}), 200


@board_bp.route('/tasks/<int:task_id>/time-estimate', methods=['PUT'])
@jwt_required()
def update_task_time_estimate(task_id):
    actor, role = get_actor()
    if not actor:
        return jsonify({'error': 'Unauthorized'}), 401

    task = BoardTask.query.get(task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404

    data = request.get_json() or {}
    estimate = data.get('time_estimate_minutes')
    
    # Allow clearing estimate
    task.time_estimate_minutes = estimate if estimate is not None else None
    
    db.session.commit()
    return jsonify(task.to_dict()), 200


# ─── Workspace Documents (Wiki) ──────────────────────────────────────

@board_bp.route('/<string:board_id>/docs', methods=['GET'])
@jwt_required()
def get_workspace_docs(board_id):
    actor, role = get_actor()
    if not actor:
        return jsonify({'error': 'Unauthorized'}), 401

    board = Board.get_by_id_or_public_id(board_id)
    if not board:
        return jsonify({'error': 'Space not found'}), 404

    if not board_is_accessible(board, actor, role):
        return jsonify({'error': 'Forbidden'}), 403

    docs = WorkspaceDoc.query.filter_by(board_id=board.id).order_by(WorkspaceDoc.position.asc()).all()
    return jsonify([d.to_dict() for d in docs]), 200


@board_bp.route('/<string:board_id>/docs', methods=['POST'])
@jwt_required()
def create_workspace_doc(board_id):
    actor, role = get_actor()
    if not actor:
        return jsonify({'error': 'Unauthorized'}), 401

    board = Board.get_by_id_or_public_id(board_id)
    if not board:
        return jsonify({'error': 'Space not found'}), 404

    if not board_is_accessible(board, actor, role):
        return jsonify({'error': 'Forbidden'}), 403

    data = request.get_json() or {}
    title = data.get('title', '').strip()
    if not title:
        return jsonify({'error': 'Title is required'}), 400

    doc = WorkspaceDoc(
        board_id=board.id,
        title=title,
        content_html=data.get('content_html', ''),
        created_by_name=actor.name,
    )
    db.session.add(doc)
    db.session.commit()
    return jsonify(doc.to_dict()), 201


@board_bp.route('/docs/<int:doc_id>', methods=['GET'])
@jwt_required()
def get_single_workspace_doc(doc_id):
    actor, role = get_actor()
    if not actor:
        return jsonify({'error': 'Unauthorized'}), 401

    doc = WorkspaceDoc.query.get(doc_id)
    if not doc:
        return jsonify({'error': 'Document not found'}), 404

    if not doc_is_accessible(doc, actor, role):
        return jsonify({'error': 'Forbidden'}), 403

    d_dict = doc.to_dict()
    d_dict['location_name'] = doc.board.name if doc.board else 'Unknown Space'
    return jsonify(d_dict), 200


@board_bp.route('/docs/<int:doc_id>', methods=['PUT'])
@jwt_required()
def update_workspace_doc(doc_id):
    actor, role = get_actor()
    if not actor:
        return jsonify({'error': 'Unauthorized'}), 401

    doc = WorkspaceDoc.query.get(doc_id)
    if not doc:
        return jsonify({'error': 'Document not found'}), 404

    if not doc_is_accessible(doc, actor, role):
        return jsonify({'error': 'Forbidden'}), 403

    data = request.get_json() or {}
    if 'title' in data:
        title = data['title'].strip()
        if title:
            doc.title = title
    if 'content_html' in data:
        doc.content_html = data['content_html']
    if 'position' in data:
        doc.position = data['position']

    db.session.commit()
    return jsonify(doc.to_dict()), 200


@board_bp.route('/docs/<int:doc_id>', methods=['DELETE'])
@jwt_required()
def delete_workspace_doc(doc_id):
    actor, role = get_actor()
    if not actor:
        return jsonify({'error': 'Unauthorized'}), 401

    doc = WorkspaceDoc.query.get(doc_id)
    if not doc:
        return jsonify({'error': 'Document not found'}), 404

    is_creator = (doc.created_by_name == actor.name)
    has_board_access = doc.board and board_is_accessible(doc.board, actor, role)
    if role != 'superadmin' and not is_creator and not has_board_access:
        return jsonify({'error': 'Forbidden'}), 403

    db.session.delete(doc)
    db.session.commit()
    return jsonify({'message': 'Document deleted'}), 200


@board_bp.route('/docs', methods=['GET'])
@jwt_required()
def get_all_workspace_docs():
    actor, role = get_actor()
    if not actor:
        return jsonify({'error': 'Unauthorized'}), 401

    all_docs = WorkspaceDoc.query.all()
    accessible_docs = []
    
    # Get staff departments
    dept_ids = []
    if role == 'staff':
        dept_ids = [d.id for d in actor.departments] if hasattr(actor, 'departments') else []

    import json
    for doc in all_docs:
        if role == 'superadmin':
            accessible_docs.append(doc)
            continue
        if board_is_accessible(doc.board, actor, role):
            accessible_docs.append(doc)
            continue
        if doc.is_public:
            accessible_docs.append(doc)
            continue
        try:
            shared_users = json.loads(doc.shared_user_ids) if doc.shared_user_ids else []
        except:
            shared_users = []
        if actor.id in shared_users:
            accessible_docs.append(doc)
            continue
        try:
            shared_depts = json.loads(doc.shared_dept_ids) if doc.shared_dept_ids else []
        except:
            shared_depts = []
        if any(d_id in shared_depts for d_id in dept_ids):
            accessible_docs.append(doc)
            continue

    result = []
    for d in accessible_docs:
        d_dict = d.to_dict()
        d_dict['location_name'] = d.board.name if d.board else 'Unknown Space'
        result.append(d_dict)

    return jsonify(result), 200


# ─── Workspace Documents Comments & Sharing ───────────────────────────

@board_bp.route('/docs/<int:doc_id>/comments', methods=['GET'])
@jwt_required()
def get_doc_comments(doc_id):
    actor, role = get_actor()
    if not actor:
        return jsonify({'error': 'Unauthorized'}), 401
    
    doc = WorkspaceDoc.query.get(doc_id)
    if not doc:
        return jsonify({'error': 'Document not found'}), 404
        
    if not doc_is_accessible(doc, actor, role):
        return jsonify({'error': 'Forbidden'}), 403
        
    comments = WorkspaceDocComment.query.filter_by(doc_id=doc_id).order_by(WorkspaceDocComment.created_at.asc()).all()
    return jsonify([c.to_dict() for c in comments]), 200


@board_bp.route('/docs/<int:doc_id>/comments', methods=['POST'])
@jwt_required()
def create_doc_comment(doc_id):
    actor, role = get_actor()
    if not actor:
        return jsonify({'error': 'Unauthorized'}), 401
    
    doc = WorkspaceDoc.query.get(doc_id)
    if not doc:
        return jsonify({'error': 'Document not found'}), 404
        
    if not doc_is_accessible(doc, actor, role):
        return jsonify({'error': 'Forbidden'}), 403
        
    data = request.get_json() or {}
    content = data.get('content', '').strip()
    if not content:
        return jsonify({'error': 'Comment content is required'}), 400
        
    assigned_to = data.get('assigned_to_user_id')
    
    comment = WorkspaceDocComment(
        doc_id=doc_id,
        content=content,
        created_by_name=actor.name,
        assigned_to_user_id=assigned_to
    )
    db.session.add(comment)
    db.session.commit()
    return jsonify(comment.to_dict()), 201


@board_bp.route('/docs/comments/<int:comment_id>/resolve', methods=['PUT'])
@jwt_required()
def resolve_doc_comment(comment_id):
    actor, role = get_actor()
    if not actor:
        return jsonify({'error': 'Unauthorized'}), 401
        
    comment = WorkspaceDocComment.query.get(comment_id)
    if not comment:
        return jsonify({'error': 'Comment not found'}), 404
        
    if not doc_is_accessible(comment.doc, actor, role):
        return jsonify({'error': 'Forbidden'}), 403

    data = request.get_json() or {}
    resolved = data.get('resolved', True)
    comment.resolved = resolved
    
    db.session.commit()
    return jsonify(comment.to_dict()), 200


@board_bp.route('/docs/<int:doc_id>/share', methods=['PUT'])
@jwt_required()
def share_workspace_doc(doc_id):
    actor, role = get_actor()
    if not actor:
        return jsonify({'error': 'Unauthorized'}), 401
        
    doc = WorkspaceDoc.query.get(doc_id)
    if not doc:
        return jsonify({'error': 'Document not found'}), 404
        
    is_creator = (doc.created_by_name == actor.name)
    has_board_access = doc.board and board_is_accessible(doc.board, actor, role)
    if role != 'superadmin' and not is_creator and not has_board_access:
        return jsonify({'error': 'Forbidden'}), 403
        
    data = request.get_json() or {}
    
    # Update sharing settings
    if 'is_public' in data:
        doc.is_public = bool(data['is_public'])
    if 'shared_user_ids' in data:
        import json
        doc.shared_user_ids = json.dumps(data['shared_user_ids'])
    if 'shared_dept_ids' in data:
        import json
        doc.shared_dept_ids = json.dumps(data['shared_dept_ids'])
        
    db.session.commit()
    return jsonify(doc.to_dict()), 200


# ─── Workspace Templates Endpoints ────────────────────────────────────

@board_bp.route('/templates', methods=['GET'])
@jwt_required()
def get_board_templates():
    actor, role = get_actor()
    templates = Board.query.filter_by(is_template=True, is_archived=False).order_by(Board.created_at.desc()).all()
    visible_templates = [t.to_dict() for t in templates if board_is_accessible(t, actor, role)]
    return jsonify(visible_templates), 200

@board_bp.route('/<string:board_id>/save-as-template', methods=['POST'])
@jwt_required()
def save_board_as_template(board_id):
    actor, role = get_actor()
    board = get_board_or_404_with_access(board_id, actor, role)
    if not board:
        return jsonify({"error": "Forbidden"}), 403
        
    data = request.get_json() or {}
    template_name = data.get('template_name', f"Template: {board.name}")
    
    # Create a new Board template copy
    new_template = Board(
        name=template_name,
        description=board.description,
        is_private=board.is_private,
        custom_statuses=board.custom_statuses,
        is_folder=board.is_folder,
        color=board.color or '#673de6',
        icon=board.icon or '📋',
        is_template=True,
        is_archived=False,
        status=board.status,
        priority=board.priority,
        category=board.category,
        budget_amount=board.budget_amount
    )
    db.session.add(new_template)
    db.session.flush()
    
    # Copy access members
    for member in board.access_members:
        db.session.add(BoardAccessMember(
            board_id=new_template.id,
            staff_id=member.staff_id,
            super_admin_id=member.super_admin_id
        ))
        
    # Copy groups and tasks
    for g in board.groups:
        new_group = BoardGroup(
            board_id=new_template.id,
            name=g.name,
            color=g.color,
            position=g.position
        )
        db.session.add(new_group)
        db.session.flush()
        for t in g.tasks:
            new_task = BoardTask(
                group_id=new_group.id,
                title=t.title,
                status=t.status,
                priority=t.priority,
                notes=t.notes,
                category=t.category,
                tags=t.tags,
                description_html=t.description_html,
                position=t.position
            )
            db.session.add(new_task)
            
    # Copy custom fields
    from app.models.board_model_extensions import BoardCustomField
    custom_fields = BoardCustomField.query.filter_by(board_id=board.id).all()
    for cf in custom_fields:
        new_cf = BoardCustomField(
            board_id=new_template.id,
            name=cf.name,
            type=cf.type,
            config_json=cf.config_json
        )
        db.session.add(new_cf)
        
    db.session.commit()
    return jsonify(new_template.to_dict()), 201

@board_bp.route('/create-from-template/<int:template_id>', methods=['POST'])
@jwt_required()
def create_board_from_template(template_id):
    actor, role = get_actor()
    template = Board.query.get_or_404(template_id)
    if not template.is_template:
        return jsonify({"error": "Selected board is not a template"}), 400
        
    data = request.get_json() or {}
    new_name = data.get('name', f"New {template.name}")
    
    new_board = Board(
        name=new_name,
        description=template.description,
        is_private=template.is_private,
        custom_statuses=template.custom_statuses,
        is_folder=template.is_folder,
        color=template.color,
        icon=template.icon,
        is_template=False,
        is_archived=False,
        status='Not Started',
        priority=template.priority,
        category=template.category,
        budget_amount=template.budget_amount
    )
    db.session.add(new_board)
    db.session.flush()
    
    # Copy access members
    if template.is_private:
        sync_board_access(new_board, actor, role, [])
        
    # Copy groups and tasks
    for g in template.groups:
        new_group = BoardGroup(
            board_id=new_board.id,
            name=g.name,
            color=g.color,
            position=g.position
        )
        db.session.add(new_group)
        db.session.flush()
        for t in g.tasks:
            new_task = BoardTask(
                group_id=new_group.id,
                title=t.title,
                status=t.status,
                priority=t.priority,
                notes=t.notes,
                category=t.category,
                tags=t.tags,
                description_html=t.description_html,
                position=t.position
            )
            db.session.add(new_task)
            
    # Copy custom fields
    from app.models.board_model_extensions import BoardCustomField
    custom_fields = BoardCustomField.query.filter_by(board_id=template.id).all()
    for cf in custom_fields:
        new_cf = BoardCustomField(
            board_id=new_board.id,
            name=cf.name,
            type=cf.type,
            config_json=cf.config_json
        )
        db.session.add(new_cf)
        
    db.session.commit()
    return jsonify(new_board.to_dict()), 201


# ─── Soft-Archiving Endpoints ─────────────────────────────────────────

@board_bp.route('/<string:board_id>/archive', methods=['PUT'])
@jwt_required()
def archive_board(board_id):
    actor, role = get_actor()
    board = get_board_or_404_with_access(board_id, actor, role)
    if not board:
        return jsonify({"error": "Forbidden"}), 403
    board.is_archived = True
    db.session.commit()
    return jsonify(board.to_dict()), 200

@board_bp.route('/<string:board_id>/unarchive', methods=['PUT'])
@jwt_required()
def unarchive_board(board_id):
    actor, role = get_actor()
    board = get_board_or_404_with_access(board_id, actor, role)
    if not board:
        return jsonify({"error": "Forbidden"}), 403
    board.is_archived = False
    db.session.commit()
    return jsonify(board.to_dict()), 200


# ─── Milestones Endpoints ─────────────────────────────────────────────

@board_bp.route('/<string:board_id>/milestones', methods=['GET'])
@jwt_required()
def get_board_milestones(board_id):
    actor, role = get_actor()
    board = get_board_or_404_with_access(board_id, actor, role)
    if not board:
        return jsonify({"error": "Forbidden"}), 403
        
    milestones = BoardMilestone.query.filter_by(board_id=board.id).order_by(BoardMilestone.due_date.asc()).all()
    return jsonify([m.to_dict() for m in milestones]), 200

@board_bp.route('/<string:board_id>/milestones', methods=['POST'])
@jwt_required()
def create_board_milestone(board_id):
    actor, role = get_actor()
    board = get_board_or_404_with_access(board_id, actor, role)
    if not board:
        return jsonify({"error": "Forbidden"}), 403
        
    data = request.get_json() or {}
    title = data.get('title')
    due_date_str = data.get('due_date')
    
    if not title or not due_date_str:
        return jsonify({"error": "Title and due date are required"}), 400
        
    try:
        due_date = datetime.strptime(due_date_str.split('T')[0], "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "Invalid date format. Expected YYYY-MM-DD"}), 400
        
    milestone = BoardMilestone(
        board_id=board.id,
        title=title,
        description=data.get('description'),
        due_date=due_date,
        status=data.get('status', 'Uncompleted')
    )
    db.session.add(milestone)
    db.session.commit()
    return jsonify(milestone.to_dict()), 201

@board_bp.route('/milestones/<int:milestone_id>', methods=['PUT'])
@jwt_required()
def update_board_milestone(milestone_id):
    actor, role = get_actor()
    milestone = BoardMilestone.query.get_or_404(milestone_id)
    if not ensure_board_access(milestone.board, actor, role):
        return jsonify({"error": "Forbidden"}), 403
        
    data = request.get_json() or {}
    milestone.title = data.get('title', milestone.title)
    milestone.description = data.get('description', milestone.description)
    milestone.status = data.get('status', milestone.status)
    
    if data.get('due_date'):
        try:
            milestone.due_date = datetime.strptime(data['due_date'].split('T')[0], "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"error": "Invalid date format"}), 400
            
    db.session.commit()
    return jsonify(milestone.to_dict()), 200

@board_bp.route('/milestones/<int:milestone_id>', methods=['DELETE'])
@jwt_required()
def delete_board_milestone(milestone_id):
    actor, role = get_actor()
    milestone = BoardMilestone.query.get_or_404(milestone_id)
    if not ensure_board_access(milestone.board, actor, role):
        return jsonify({"error": "Forbidden"}), 403
        
    db.session.delete(milestone)
    db.session.commit()
    return jsonify({"message": "Milestone deleted"}), 200


@board_bp.route('/tasks/bulk-move', methods=['POST'])
@jwt_required()
def bulk_move_tasks():
    actor, role = get_actor()
    data = request.get_json() or {}
    task_ids = data.get('task_ids', [])
    target_group_id = data.get('target_group_id')

    if not task_ids or not target_group_id:
        return jsonify({"error": "task_ids and target_group_id are required"}), 400

    from app.models.board_model import BoardGroup, BoardTask, BoardTaskHistory
    target_group = BoardGroup.query.get_or_404(target_group_id)
    if not ensure_board_access(target_group.board, actor, role):
        return jsonify({"error": "Forbidden - No access to target board"}), 403

    new_status = data.get('target_status') or data.get('new_status')

    tasks = BoardTask.query.filter(BoardTask.id.in_(task_ids)).all()
    for task in tasks:
        # Verify access to old board
        if not ensure_board_access(task.group.board, actor, role):
            continue
        
        current_group_name = task.group.name
        resolved_group_id = target_group.id

        # Log change
        change = f"Moved task to group '{target_group.name}' on board '{target_group.board.name}'"
        db.session.add(BoardTaskHistory(task_id=task.id, actor_name=actor.name, action=change))
        
        # Update group, sync status, and merge custom fields across boards
        merge_task_into_target_board(task, resolved_group_id, new_status=new_status)

    db.session.commit()
    return jsonify({"message": f"Successfully moved {len(tasks)} task(s)"}), 200


@board_bp.route('/integrations/jotform', methods=['POST', 'OPTIONS'])
def jotform_webhook():
    if request.method == 'OPTIONS':
        return jsonify({}), 200

    import json
    import re
    import os
    from app.models.board_model import Board, BoardGroup, BoardTask, BoardTaskHistory
    from app.models.board_model_extensions import BoardCustomField, TaskCustomFieldValue

    # 1. Retrieve configuration from query params
    board_id = request.args.get('board_id')
    group_id = request.args.get('group_id', type=int)
    token = request.args.get('token')

    # Optional security validation token
    expected_token = os.getenv('JOTFORM_WEBHOOK_TOKEN')
    if expected_token and token != expected_token:
        return jsonify({"error": "Unauthorized webhook token"}), 401

    # 2. Resolve Board
    if not board_id:
        board = Board.query.filter_by(is_folder=False).first()
        if not board:
            return jsonify({"error": "No board found in system"}), 404
        board_id = board.id
    else:
        board = Board.get_by_id_or_public_id(board_id)
        if not board:
            return jsonify({"error": "Board not found"}), 404
        board_id = board.id

    # 3. Resolve Status Group (Defaults to the first group/Todo column on the board)
    if not group_id:
        group = BoardGroup.query.filter_by(board_id=board_id).order_by(BoardGroup.position.asc()).first()
        if not group:
            return jsonify({"error": "No status group found on this board"}), 404
        group_id = group.id
    else:
        group = BoardGroup.query.get(group_id)
        if not group or group.board_id != board_id:
            return jsonify({"error": "Group not found or not on this board"}), 404

    # 3. Parse Jotform POST data
    raw_request_str = request.form.get('rawRequest')
    raw_data = {}
    if raw_request_str:
        try:
            raw_data = json.loads(raw_request_str)
        except Exception as e:
            current_app.logger.error(f"Jotform failed to parse rawRequest: {e}")

    # Merge top-level form fields with rawRequest details
    form_data = {k: v for k, v in request.form.items()}
    if raw_data:
        form_data.update(raw_data)

    submission_id = request.form.get('submissionID')
    form_title = request.form.get('formTitle', 'Jotform Submission')

    # Helper function to clean keys into labels
    def clean_key_name(key_str):
        # Remove q123_ prefixes
        clean_key = re.sub(r'^q\d+_', '', key_str)
        # Split camelCase and snake_case
        words = re.findall(r'[A-Z]?[a-z0-9]+|[A-Z]+(?=[A-Z][a-z0-9]|\b)', clean_key)
        if not words:
            words = clean_key.split('_')
        return " ".join(w.capitalize() for w in words if w)

    # Metadata fields to exclude from notes and custom fields
    metadata_fields = {
        # Core Jotform submission metadata
        'formID', 'submissionID', 'webhookURL', 'ip', 'formTitle',
        'event_id', 'rawRequest', 'slug', 'submit',
        # Jotform internal tracking
        'username', 'type', 'pretty', 'path',
        'jsExecutionTracker', 'js_execution_tracker',
        'submitSource', 'submit_source',
        'submitDate', 'submit_date',
        'buildDate', 'build_date',
        'uploadServerUrl', 'upload_server_url',
        'eventObserver', 'event_observer',
        'timeToSubmit', 'time_to_submit',
        # Payment internals
        'paymentVersion', 'payment_version',
        'paymentTotalChecksum', 'payment_total_checksum',
        'paymentDiscountValue', 'payment_discount_value',
        # Validation internals
        'validatedNewRequiredFieldIds', 'validated_new_required_field_ids',
    }

    # Pattern-based exclusion for dynamic Jotform internal keys
    def is_jotform_internal(key):
        """Returns True if the key looks like a Jotform internal/system field."""
        if not key:
            return True
        if key in metadata_fields:
            return True
        k_lower = key.lower().replace('_', '').replace('-', '').replace(' ', '')
        internal_patterns = [
            'jsexecution', 'executiontracker', 'submitsource', 'submitdate',
            'builddate', 'uploadserver', 'eventobserver', 'timetosubmit',
            'paymentversion', 'paymenttotal', 'paymentchecksum', 'paymentdiscount',
            'validatednew', 'requiredfieldid', 'webhookurl',
        ]
        for pattern in internal_patterns:
            if pattern in k_lower:
                return True
        # Jotform payment widget fields (e.g. q5_payment3)
        if re.match(r'^q\d+_payment\d*$', key, re.IGNORECASE):
            return True
        return False

    # 4. Extract clean question entries from form data & Jotform 'pretty' string
    task_title = None
    submitter_email = None
    entries = []  # list of (label, value) tuples

    # Flatten dictionaries (like full name/address fields in Jotform)
    def flatten_value(val):
        if isinstance(val, dict):
            # Check for name fields (first, last)
            if 'first' in val or 'last' in val:
                return f"{val.get('first', '')} {val.get('last', '')}".strip()
            # Address fields
            if 'addr_line1' in val or 'city' in val or 'state' in val:
                parts = [val.get('addr_line1', ''), val.get('addr_line2', ''),
                         val.get('city', ''), val.get('state', ''), val.get('postal', '')]
                return ", ".join(p for p in parts if p)
            # Skip dicts that look like Jotform internal structures (special_*, item_*)
            if any(k.startswith('special_') or k.startswith('item_') for k in val.keys()):
                return None
            # Skip deeply nested dicts (validation internals, etc.)
            if any(isinstance(v, dict) for v in val.values()):
                return None
            # General dict - join only meaningful values
            meaningful = {k: v for k, v in val.items() if v and str(v).strip()}
            if not meaningful:
                return None
            return ", ".join(f"{k}: {v}" for k, v in meaningful.items())
        if isinstance(val, str):
            stripped = val.strip()
            if stripped.startswith('{') and ('special_' in stripped or 'item_' in stripped):
                return None
        return val

    # First, try to parse Jotform's 'pretty' field which has the real human question labels
    pretty_str = request.form.get('pretty') or raw_data.get('pretty')
    parsed_pretty = False
    if pretty_str and isinstance(pretty_str, str):
        try:
            # Format is typically "Label 1:Value 1, Label 2:Value 2"
            parts = re.split(r',\s*(?=[^:]+:)', pretty_str.strip())
            for part in parts:
                if ':' in part:
                    p_key, p_val = part.split(':', 1)
                    p_key = p_key.strip()
                    p_val = p_val.strip()
                    if p_key and p_val and not is_jotform_internal(p_key):
                        entries.append((p_key, p_val))
            if entries:
                parsed_pretty = True
        except Exception as e:
            current_app.logger.warning(f"Failed parsing Jotform pretty string: {e}")

    # Fallback to form_data keys if pretty wasn't available
    if not parsed_pretty:
        for raw_k, raw_v in form_data.items():
            if is_jotform_internal(raw_k):
                continue
            flat_val = flatten_value(raw_v)
            if not flat_val:
                continue
            cleaned_k = clean_key_name(raw_k)
            # Remove trailing numbers like "Fullname0" -> "Full Name"
            cleaned_k = re.sub(r'(\D+)\d+$', r'\1', cleaned_k).strip()
            entries.append((cleaned_k, flat_val))

    # Identify task title & submitter email
    notes_list = []
    student_name = None
    parent_name = None
    general_name = None

    for label, val in entries:
        notes_list.append(f"**{label}**: {val}")
        lbl_lower = label.lower()

        # Email
        if 'email' in lbl_lower and not submitter_email:
            submitter_email = str(val).strip()

        # Title keywords
        if ('title' in lbl_lower or 'subject' in lbl_lower or 'topic' in lbl_lower) and not task_title:
            task_title = str(val).strip()

        # Name tracking for fallback title
        if 'student' in lbl_lower and 'name' in lbl_lower and not student_name:
            student_name = str(val).strip()
        elif 'parent' in lbl_lower and 'name' in lbl_lower and not parent_name:
            parent_name = str(val).strip()
        elif 'name' in lbl_lower and not general_name and not is_jotform_internal(label):
            general_name = str(val).strip()

    # Determine best Task Title
    if not task_title:
        if student_name:
            task_title = f"{student_name} - {form_title}"
        elif parent_name:
            task_title = f"{parent_name} - {form_title}"
        elif general_name:
            task_title = f"{general_name} - {form_title}"
        else:
            task_title = f"{form_title} #{submission_id or ''}"

    # Compile task notes
    notes_content = f"### Jotform Submission Details\n"
    notes_content += f"- **Form Name**: {form_title}\n"
    notes_content += f"- **Submission ID**: {submission_id or 'N/A'}\n\n"
    notes_content += "\n".join(f"- {item}" for item in notes_list)

    # 5. Create the BoardTask
    last_task = BoardTask.query.filter_by(group_id=group_id).order_by(BoardTask.position.desc()).first()
    position = (last_task.position + 1) if last_task else 0

    new_task = BoardTask(
        group_id=group_id,
        title=task_title,
        notes=notes_content,
        submitter_email=submitter_email,
        position=position
    )
    db.session.add(new_task)
    db.session.flush()

    # 6. Log in task history
    db.session.add(BoardTaskHistory(task_id=new_task.id, actor_name="Jotform Integration", action="Created task via Webhook"))

    # 7. Smart Custom Fields Mapping
    # Load all existing custom fields for this board
    existing_fields = BoardCustomField.query.filter_by(board_id=board_id).all()

    def normalize_str(s):
        """Normalizes a string for fuzzy comparison (e.g. 'Student\'s Full Name' -> 'studentfullname')"""
        return re.sub(r'[^a-z0-9]', '', (s or '').lower())

    def find_best_field_match(entry_label):
        """Finds the best matching existing custom field on the board."""
        norm_label = normalize_str(entry_label)
        lbl_lower = entry_label.lower()

        # 1. Exact or normalized exact match
        for f in existing_fields:
            if normalize_str(f.name) == norm_label:
                return f

        # 2. Semantic / keyword match
        for f in existing_fields:
            f_norm = normalize_str(f.name)
            f_lower = f.name.lower()

            # Student Name
            if 'student' in lbl_lower and ('student' in f_lower or f_norm == 'studentname'):
                return f

            # Parent Name
            if 'parent' in lbl_lower and ('parent' in f_lower or f_norm == 'parentname'):
                return f

            # Attendees / Party / Number of people
            if any(w in lbl_lower for w in ['attendee', 'party', 'people', 'number of', 'attendees', 'how many', 'count']) and \
               any(w in f_lower for w in ['attendee', 'party', 'people', '#', 'count', 'number', 'size']):
                return f

            # Payment / Amount / Total / Fee
            if any(w in lbl_lower for w in ['payment', 'price', 'amount', 'total', 'fee', 'cost']) and \
               any(w in f_lower for w in ['payment', 'price', 'amount', 'total', 'fee', 'cost', '$']):
                return f

            # Phone / Contact number
            if ('phone' in lbl_lower or 'cell' in lbl_lower) and ('phone' in f_lower or 'contact' in f_lower):
                return f

            # Email
            if 'email' in lbl_lower and 'email' in f_lower:
                return f

            # Address / Location
            if ('address' in lbl_lower or 'location' in lbl_lower) and ('address' in f_lower or 'location' in f_lower):
                return f

            # Grade / Class
            if 'grade' in lbl_lower and 'grade' in f_lower:
                return f

        # 3. Substring containment match
        for f in existing_fields:
            f_norm = normalize_str(f.name)
            if f_norm and (f_norm in norm_label or norm_label in f_norm):
                return f

        return None

    mapped_field_ids = set()

    for label, val in entries:
        matched_field = find_best_field_match(label)

        if matched_field:
            target_field = matched_field
        else:
            # Clean up the label for creating a new field (avoid raw Q2... names)
            clean_label = label.strip()
            if re.match(r'^q\d+_', clean_label, re.IGNORECASE):
                clean_label = clean_key_name(clean_label)
            clean_label = re.sub(r'(\D+)\d+$', r'\1', clean_label).strip()

            if not clean_label or is_jotform_internal(clean_label):
                continue

            target_field = BoardCustomField(board_id=board_id, name=clean_label, type='text')
            db.session.add(target_field)
            db.session.flush()
            existing_fields.append(target_field)

        # Avoid setting the same custom field multiple times in a single submission
        if target_field.id in mapped_field_ids:
            continue
        mapped_field_ids.add(target_field.id)

        # Insert value
        value_json_str = json.dumps(val)
        field_val = TaskCustomFieldValue(task_id=new_task.id, field_id=target_field.id, value_json=value_json_str)
        db.session.add(field_val)

    db.session.commit()
    return jsonify(new_task.to_dict()), 201

