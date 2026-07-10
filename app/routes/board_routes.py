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
    board = Board.query.get_or_404(board_id)
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
        if not assignee_id or assignee_role not in {'staff', 'superadmin'}:
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
            )
        )
        if index == 0:
            if assignee_role == 'superadmin':
                task.responsible_super_admin_id = assignee_id
            else:
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
    parent_id = data.get('parent_id')
    is_folder = data.get('is_folder', False)

    if not name:
        return jsonify({"error": "Board name is required"}), 400

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


@board_bp.route('/<int:board_id>', methods=['GET'])
@jwt_required()
def get_board(board_id):
    actor, role = get_actor()
    board = get_board_or_404_with_access(board_id, actor, role)
    if not board:
        return jsonify({"error": "Forbidden"}), 403
    return jsonify(serialize_board_with_groups(board)), 200


@board_bp.route('/<int:board_id>', methods=['PUT'])
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
        board.parent_id = data.get('parent_id')
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


@board_bp.route('/<int:board_id>', methods=['DELETE'])
@jwt_required()
def delete_board(board_id):
    actor, role = get_actor()
    board = get_board_or_404_with_access(board_id, actor, role)
    if not board:
        return jsonify({"error": "Forbidden"}), 403
    db.session.delete(board)
    db.session.commit()
    return jsonify({"message": "Board deleted successfully"}), 200


@board_bp.route('/<int:board_id>/groups', methods=['POST'])
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
        return jsonify(new_update.to_dict()), 201
    except Exception as e:
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
    actor, role = get_actor()
    update = TaskUpdate.query.get_or_404(update_id)
    if not ensure_board_access(update.task.group.board, actor, role):
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json() or {}
    content = data.get('content')
    if not content:
        return jsonify({"error": "Reply content is required"}), 400

    reply = TaskUpdateReply(update_id=update.id, content=content, sender_name=actor.name)
    if role == 'superadmin':
        reply.sender_super_admin_id = actor.id
    else:
        reply.sender_staff_id = actor.id

    db.session.add(reply)
    db.session.commit()
    return jsonify(reply.to_dict()), 201


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
    board_id = request.args.get('board_id', type=int)

    query = CalendarEvent.query
    if board_id:
        query = query.filter_by(board_id=board_id)
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

    event = CalendarEvent(
        board_id=data.get('board_id'),
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

    board_id = request.args.get('board_id', type=int)
    start = request.args.get('start')
    end = request.args.get('end')

    query = BoardTask.query.join(BoardGroup)
    if board_id:
        query = query.filter(BoardGroup.board_id == board_id)

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

@board_bp.route('/<int:board_id>/docs', methods=['GET'])
@jwt_required()
def get_workspace_docs(board_id):
    actor, role = get_actor()
    if not actor:
        return jsonify({'error': 'Unauthorized'}), 401

    board = Board.query.get(board_id)
    if not board:
        return jsonify({'error': 'Space not found'}), 404

    if not board_is_accessible(board, actor, role):
        return jsonify({'error': 'Forbidden'}), 403

    docs = WorkspaceDoc.query.filter_by(board_id=board_id).order_by(WorkspaceDoc.position.asc()).all()
    return jsonify([d.to_dict() for d in docs]), 200


@board_bp.route('/<int:board_id>/docs', methods=['POST'])
@jwt_required()
def create_workspace_doc(board_id):
    actor, role = get_actor()
    if not actor:
        return jsonify({'error': 'Unauthorized'}), 401

    board = Board.query.get(board_id)
    if not board:
        return jsonify({'error': 'Space not found'}), 404

    if not board_is_accessible(board, actor, role):
        return jsonify({'error': 'Forbidden'}), 403

    data = request.get_json() or {}
    title = data.get('title', '').strip()
    if not title:
        return jsonify({'error': 'Title is required'}), 400

    doc = WorkspaceDoc(
        board_id=board_id,
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

@board_bp.route('/<int:board_id>/save-as-template', methods=['POST'])
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

@board_bp.route('/<int:board_id>/archive', methods=['PUT'])
@jwt_required()
def archive_board(board_id):
    actor, role = get_actor()
    board = get_board_or_404_with_access(board_id, actor, role)
    if not board:
        return jsonify({"error": "Forbidden"}), 403
    board.is_archived = True
    db.session.commit()
    return jsonify(board.to_dict()), 200

@board_bp.route('/<int:board_id>/unarchive', methods=['PUT'])
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

@board_bp.route('/<int:board_id>/milestones', methods=['GET'])
@jwt_required()
def get_board_milestones(board_id):
    actor, role = get_actor()
    board = get_board_or_404_with_access(board_id, actor, role)
    if not board:
        return jsonify({"error": "Forbidden"}), 403
        
    milestones = BoardMilestone.query.filter_by(board_id=board_id).order_by(BoardMilestone.due_date.asc()).all()
    return jsonify([m.to_dict() for m in milestones]), 200

@board_bp.route('/<int:board_id>/milestones', methods=['POST'])
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
        board_id=board_id,
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

    DEFAULT_STATUSES = {"not started", "in progress", "done", "to do", "completed", "complete", "list"}

    tasks = BoardTask.query.filter(BoardTask.id.in_(task_ids)).all()
    for task in tasks:
        # Verify access to old board
        if not ensure_board_access(task.group.board, actor, role):
            continue
        
        current_group_name = task.group.name
        is_custom = current_group_name.lower() not in DEFAULT_STATUSES

        if is_custom:
            dest_board = target_group.board
            existing_group = BoardGroup.query.filter_by(board_id=dest_board.id, name=current_group_name).first()
            if not existing_group:
                last_group = BoardGroup.query.filter_by(board_id=dest_board.id).order_by(BoardGroup.position.desc()).first()
                next_pos = (last_group.position + 1) if last_group else 0
                new_group = BoardGroup(
                    board_id=dest_board.id,
                    name=current_group_name,
                    color=task.group.color or "#673de6",
                    position=next_pos
                )
                db.session.add(new_group)
                db.session.flush()
                resolved_group_id = new_group.id
            else:
                resolved_group_id = existing_group.id
        else:
            resolved_group_id = target_group.id

        # Log change
        change = f"Moved task to group '{current_group_name}' on board '{target_group.board.name}'"
        db.session.add(BoardTaskHistory(task_id=task.id, actor_name=actor.name, action=change))
        
        # Update group
        task.group_id = resolved_group_id

    db.session.commit()
    return jsonify({"message": f"Successfully moved {len(tasks)} task(s)"}), 200

