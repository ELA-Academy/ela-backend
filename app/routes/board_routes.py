from datetime import datetime

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required

from app.models import db
from app.models.board_model import Board, BoardAccessMember, BoardGroup, BoardTask
from app.models.department_model import Department
from app.models.notification_model import Notification
from app.models.staff_model import Staff
from app.models.super_admin_model import SuperAdmin
from app.models.task_update_model import TaskUpdate, TaskUpdateLike, TaskUpdateReply


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

    if role == 'superadmin':
        return True

    if not board.is_private:
        return True

    return any(member.staff_id == actor.id for member in board.access_members)


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


@board_bp.route('', methods=['GET'])
@jwt_required()
def get_boards():
    actor, role = get_actor()
    boards = Board.query.order_by(Board.created_at.desc()).all()
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

    if not name:
        return jsonify({"error": "Board name is required"}), 400

    new_board = Board(name=name, description=description, is_private=bool(is_private))
    db.session.add(new_board)
    db.session.flush()

    sync_board_access(new_board, actor, role, access_members)

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
    actor, role = get_actor()
    board = get_board_or_404_with_access(board_id, actor, role)
    if not board:
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json() or {}
    board.name = data.get('name', board.name)
    board.description = data.get('description', board.description)
    board.is_private = data.get('is_private', board.is_private)
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
    db.session.add(new_task)
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
    old_assignee_staff = task.responsible_staff_id
    old_assignee_admin = task.responsible_super_admin_id

    if 'title' in data:
        task.title = data['title']
    if 'status' in data:
        task.status = data['status']
    if 'priority' in data:
        task.priority = data['priority']
    if 'notes' in data:
        task.notes = data['notes']
    if 'due_date' in data:
        date_str = data['due_date']
        if date_str:
            try:
                task.due_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                task.due_date = None
        else:
            task.due_date = None

    if 'assignee_id' in data and 'assignee_role' in data:
        assignee_id = data['assignee_id']
        assignee_role = data['assignee_role']
        if not assignee_id:
            task.responsible_staff_id = None
            task.responsible_super_admin_id = None
        elif assignee_role == 'superadmin':
            task.responsible_super_admin_id = assignee_id
            task.responsible_staff_id = None
        else:
            task.responsible_staff_id = assignee_id
            task.responsible_super_admin_id = None

    db.session.commit()

    new_assignee_staff = task.responsible_staff_id
    new_assignee_admin = task.responsible_super_admin_id
    changed = (new_assignee_staff != old_assignee_staff) or (new_assignee_admin != old_assignee_admin)

    if changed and (new_assignee_staff or new_assignee_admin):
        message = f"{actor.name} assigned you the task: '{task.title}' on board '{board.name}'"
        notification = Notification(
            message=message,
            category='assignment',
            target_type='Board',
            target_id=board.id,
            target_link=f"/admin/boards/{board.id}?task={task.id}"
        )
        if new_assignee_admin:
            notification.super_admin_id = new_assignee_admin
        else:
            notification.staff_id = new_assignee_staff

        db.session.add(notification)
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

        if mention_type == 'staff':
            key = f"staff_{mention_id}"
            if key not in notified_user_keys:
                notified_user_keys.add(key)
                db.session.add(
                    Notification(
                        message=message,
                        category='mention',
                        staff_id=mention_id,
                        target_type='Board',
                        target_id=board.id,
                        target_link=f"/admin/boards/{board.id}?task={task.id}"
                    )
                )
        elif mention_type == 'superadmin':
            key = f"superadmin_{mention_id}"
            if key not in notified_user_keys:
                notified_user_keys.add(key)
                db.session.add(
                    Notification(
                        message=message,
                        category='mention',
                        super_admin_id=mention_id,
                        target_type='Board',
                        target_id=board.id,
                        target_link=f"/admin/boards/{board.id}?task={task.id}"
                    )
                )
        elif mention_type == 'department':
            department = Department.query.get(mention_id)
            if department:
                for member in department.staff_members:
                    key = f"staff_{member.id}"
                    if key not in notified_user_keys and (role != 'staff' or actor.id != member.id):
                        notified_user_keys.add(key)
                        db.session.add(
                            Notification(
                                message=f"{actor.name} @mentioned your department ({department.name}) in task '{task.title}' on board '{board.name}'",
                                category='mention',
                                staff_id=member.id,
                                target_type='Board',
                                target_id=board.id,
                                target_link=f"/admin/boards/{board.id}?task={task.id}"
                            )
                        )

    db.session.commit()
    return jsonify(new_update.to_dict()), 201


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
