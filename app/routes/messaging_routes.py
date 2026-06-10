import os
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required
from sqlalchemy import func, or_

from app.models import db
from app.models.conversation_model import (
    Conversation,
    ConversationParticipant,
    Message,
    StaffMessage,
    SuperAdminMessage,
)
from app.models.department_model import Department
from app.models.message_log_model import MessageLog
from app.models.notification_model import Notification
from app.models.staff_model import Staff
from app.models.super_admin_model import SuperAdmin
from app.utils.notifications import send_email_in_background, send_push_notification


messaging_bp = Blueprint('messaging', __name__)

DEFAULT_CHANNELS = [
    "Welcome",
    "School Updates",
    "Cross-Department Ops",
]


def get_current_user():
    claims = get_jwt()
    email = get_jwt_identity()
    role = claims.get('role')

    if role == 'superadmin':
        return SuperAdmin.query.filter_by(email=email).first(), role
    if role == 'staff':
        return Staff.query.filter_by(email=email).first(), role
    return None, None


def participant_filters_for_user(conversation_id, user, role):
    return {
        'conversation_id': conversation_id,
        'staff_id': user.id if role == 'staff' else None,
        'super_admin_id': user.id if role == 'superadmin' else None,
    }


def get_participant_entry(conversation_id, user, role):
    return ConversationParticipant.query.filter_by(
        **participant_filters_for_user(conversation_id, user, role)
    ).first()


def get_or_create_participant_entry(conversation, user, role):
    participant_entry = get_participant_entry(conversation.id, user, role)
    if participant_entry:
        return participant_entry

    participant_entry = ConversationParticipant(
        conversation_id=conversation.id,
        staff=user if role == 'staff' else None,
        super_admin=user if role == 'superadmin' else None,
        last_read_at=datetime.now(timezone.utc)
    )
    db.session.add(participant_entry)
    db.session.flush()
    return participant_entry


def format_department_thread_name(department):
    clean_name = department.name.replace('Department', '').strip()
    clean_name = clean_name or department.name
    return f"{clean_name} Dept"


def ensure_workspace_threads():
    created = False

    for channel_name in DEFAULT_CHANNELS:
        existing = Conversation.query.filter_by(
            conversation_type='channel',
            name=channel_name
        ).first()
        if existing:
            continue
        db.session.add(Conversation(conversation_type='channel', name=channel_name))
        created = True

    departments = Department.query.filter_by(is_active=True).all()
    for department in departments:
        existing = Conversation.query.filter_by(
            conversation_type='department',
            department_id=department.id
        ).first()
        if existing:
            if not existing.name:
                existing.name = format_department_thread_name(department)
                created = True
            continue

        db.session.add(
            Conversation(
                conversation_type='department',
                name=format_department_thread_name(department),
                department_id=department.id
            )
        )
        created = True

    if created:
        db.session.commit()


def can_access_conversation(user, role, conversation):
    if not user or not conversation:
        return False

    if role == 'superadmin':
        return True

    if conversation.conversation_type == 'channel':
        return True

    if conversation.conversation_type == 'department':
        return any(dept.id == conversation.department_id for dept in user.departments)

    return any(item.id == conversation.id for item in user.conversations)


def get_accessible_conversations(user, role):
    ensure_workspace_threads()
    conversations = Conversation.query.order_by(Conversation.updated_at.desc()).all()
    return [conversation for conversation in conversations if can_access_conversation(user, role, conversation)]


def get_conversation_title(conversation, user, role):
    if conversation.conversation_type == 'direct':
        other_participants = []
        for participant in conversation.get_participants():
            participant_role = 'superadmin' if isinstance(participant, SuperAdmin) else 'staff'
            if participant_role == role and participant.id == user.id:
                continue
            other_participants.append(participant.name)
        return ", ".join(other_participants) or "Yourself"
    return conversation.name or conversation.display_name()


def get_conversation_unread_count(conversation, user, role):
    participant_entry = get_participant_entry(conversation.id, user, role)
    if not participant_entry:
        return 0

    last_read_at = participant_entry.last_read_at or datetime.min.replace(tzinfo=timezone.utc)
    return db.session.query(func.count(Message.id)).filter(
        Message.conversation_id == conversation.id,
        Message.created_at > last_read_at,
        or_(Message.sender_type != role, Message.sender_id != user.id)
    ).scalar()


def recipients_for_conversation(conversation, sender, sender_role):
    sender_key = f"{sender_role}_{sender.id}"
    recipients = []

    if conversation.conversation_type == 'direct':
        for participant in conversation.participants:
            recipient = participant.staff or participant.super_admin
            if not recipient:
                continue
            recipient_role = 'staff' if participant.staff_id else 'superadmin'
            if f"{recipient_role}_{recipient.id}" == sender_key:
                continue
            recipients.append((recipient, recipient_role))
        return recipients

    if conversation.conversation_type == 'department' and conversation.department:
        for staff_member in conversation.department.staff_members:
            if f"staff_{staff_member.id}" != sender_key:
                recipients.append((staff_member, 'staff'))

    if conversation.conversation_type == 'channel':
        for staff_member in Staff.query.filter_by(is_active=True).all():
            if f"staff_{staff_member.id}" != sender_key:
                recipients.append((staff_member, 'staff'))

    for admin in SuperAdmin.query.filter_by(is_active=True).all():
        if f"superadmin_{admin.id}" != sender_key:
            recipients.append((admin, 'superadmin'))

    unique_recipients = {}
    for recipient, recipient_role in recipients:
        unique_recipients[f"{recipient_role}_{recipient.id}"] = (recipient, recipient_role)
    return list(unique_recipients.values())


@messaging_bp.route('/conversations/unread-count', methods=['GET'])
@jwt_required()
def get_unread_count():
    user, role = get_current_user()
    if not user:
        return jsonify({"count": 0}), 200

    total_unread = 0
    for conversation in get_accessible_conversations(user, role):
        total_unread += get_conversation_unread_count(conversation, user, role)

    return jsonify({"count": total_unread}), 200


@messaging_bp.route('/users', methods=['GET'])
@jwt_required()
def get_users_for_messaging():
    current_user, role = get_current_user()
    users = []

    current_user_staff_id = current_user.id if role == 'staff' else None
    current_user_admin_id = current_user.id if role == 'superadmin' else None

    all_staff = Staff.query.filter(Staff.id != current_user_staff_id).all()
    for staff in all_staff:
        users.append({'id': f'staff_{staff.id}', 'name': staff.name, 'role': 'Staff', 'email': staff.email})

    all_super_admins = SuperAdmin.query.filter(SuperAdmin.id != current_user_admin_id).all()
    for admin in all_super_admins:
        users.append({'id': f'superadmin_{admin.id}', 'name': admin.name, 'role': 'Super Admin', 'email': admin.email})

    return jsonify(users), 200


@messaging_bp.route('/conversations', methods=['GET'])
@jwt_required()
def get_conversations():
    user, role = get_current_user()
    if not user:
        return jsonify({"error": "User not found"}), 404

    response_data = []
    for conversation in get_accessible_conversations(user, role):
        last_message = conversation.messages.order_by(db.desc(Message.created_at)).first()
        title = get_conversation_title(conversation, user, role)
        unread_count = get_conversation_unread_count(conversation, user, role)

        response_data.append({
            'id': conversation.id,
            'title': title,
            'participant_names': title,
            'last_message': last_message.content if last_message else "No messages yet.",
            'last_message_time': (
                last_message.created_at.isoformat() + 'Z'
                if last_message
                else conversation.created_at.isoformat() + 'Z'
            ),
            'unread_count': unread_count,
            'conversation_type': conversation.conversation_type,
            'department_id': conversation.department_id,
            'department_name': conversation.department.name if conversation.department else None,
            'is_restricted': conversation.conversation_type == 'department'
        })

    response_data.sort(key=lambda item: item['last_message_time'], reverse=True)
    return jsonify(response_data), 200


@messaging_bp.route('/conversations', methods=['POST'])
@jwt_required()
def start_conversation():
    user, role = get_current_user()
    data = request.get_json() or {}
    participant_ids = data.get('participant_ids', [])

    if not participant_ids:
        return jsonify({'error': 'No participants provided'}), 400

    new_conversation = Conversation(conversation_type='direct')
    if role == 'superadmin':
        new_conversation.participants.append(ConversationParticipant(super_admin=user))
    else:
        new_conversation.participants.append(ConversationParticipant(staff=user))

    for participant_token in participant_ids:
        participant_role, participant_id_str = participant_token.split('_', 1)
        participant_id = int(participant_id_str)

        if participant_role == 'staff':
            participant = Staff.query.get(participant_id)
            if participant:
                new_conversation.participants.append(ConversationParticipant(staff=participant))
        elif participant_role == 'superadmin':
            participant = SuperAdmin.query.get(participant_id)
            if participant:
                new_conversation.participants.append(ConversationParticipant(super_admin=participant))

    db.session.add(new_conversation)
    db.session.commit()

    return jsonify({'message': 'Conversation created', 'conversation_id': new_conversation.id}), 201


@messaging_bp.route('/channels', methods=['POST'])
@jwt_required()
def create_channel():
    user, role = get_current_user()
    if role != 'superadmin':
        return jsonify({"error": "Only super admins can create channels."}), 403

    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    conversation_type = data.get('conversation_type', 'channel')
    department_id = data.get('department_id')

    if conversation_type not in {'channel', 'department'}:
        return jsonify({"error": "Invalid conversation type."}), 400

    if not name:
        return jsonify({"error": "Channel name is required."}), 400

    if conversation_type == 'department':
        if not department_id:
            return jsonify({"error": "Department is required for department threads."}), 400
        department = Department.query.get(department_id)
        if not department:
            return jsonify({"error": "Department not found."}), 404
    else:
        department = None
        department_id = None

    existing = Conversation.query.filter_by(
        conversation_type=conversation_type,
        name=name,
        department_id=department_id
    ).first()
    if existing:
        return jsonify({"error": "A channel with that name already exists."}), 409

    channel = Conversation(
        conversation_type=conversation_type,
        name=name,
        department_id=department_id
    )
    db.session.add(channel)
    db.session.commit()

    return jsonify({
        "id": channel.id,
        "title": channel.name,
        "participant_names": channel.name,
        "last_message": "No messages yet.",
        "last_message_time": channel.created_at.isoformat() + 'Z',
        "unread_count": 0,
        "conversation_type": channel.conversation_type,
        "department_id": channel.department_id,
        "department_name": department.name if department else None,
        "is_restricted": channel.conversation_type == 'department'
    }), 201


@messaging_bp.route('/conversations/<int:conversation_id>/messages', methods=['GET'])
@jwt_required()
def get_messages(conversation_id):
    user, role = get_current_user()
    conversation = Conversation.query.get_or_404(conversation_id)

    if not can_access_conversation(user, role, conversation):
        return jsonify({"error": "Forbidden"}), 403

    participant_entry = get_or_create_participant_entry(conversation, user, role)
    participant_entry.last_read_at = datetime.now(timezone.utc)
    db.session.commit()

    messages = conversation.messages.all()
    return jsonify([message.to_dict() for message in messages]), 200


@messaging_bp.route('/conversations/<int:conversation_id>/messages', methods=['POST'])
@jwt_required()
def send_message(conversation_id):
    user, role = get_current_user()
    data = request.get_json() or {}
    content = (data.get('content') or '').strip()

    if not content:
        return jsonify({'error': 'Message content cannot be empty'}), 400

    conversation = Conversation.query.get_or_404(conversation_id)
    if not can_access_conversation(user, role, conversation):
        return jsonify({"error": "Forbidden"}), 403

    get_or_create_participant_entry(conversation, user, role)

    if role == 'superadmin':
        new_message = SuperAdminMessage(content=content, sender_id=user.id)
    else:
        new_message = StaffMessage(content=content, sender_id=user.id)

    conversation.messages.append(new_message)

    if conversation.conversation_type == 'direct':
        participant_names = sorted([participant.name for participant in conversation.get_participants()])
        recipient_names = ", ".join(participant_names)
    else:
        recipient_names = conversation.name or conversation.display_name()

    db.session.add(
        MessageLog(
            conversation_id=conversation_id,
            sender_id=user.id,
            sender_type=role,
            sender_name=user.name,
            recipient_names=recipient_names,
            content=content
        )
    )

    now = datetime.now(timezone.utc)
    notification_cooldown = timedelta(minutes=15)

    for recipient, recipient_role in recipients_for_conversation(conversation, user, role):
        if recipient_role == 'staff':
            db.session.add(
                Notification(
                    staff_id=recipient.id,
                    message=f"You have a new message in {conversation.name or 'a conversation'}.",
                    target_type="Conversation",
                    target_id=conversation_id,
                    target_link=f"/admin/messaging?conversation={conversation_id}"
                )
            )
        else:
            db.session.add(
                Notification(
                    super_admin_id=recipient.id,
                    message=f"You have a new message in {conversation.name or 'a conversation'}.",
                    target_type="Conversation",
                    target_id=conversation_id,
                    target_link=f"/admin/messaging?conversation={conversation_id}"
                )
            )

        recipient_entry = get_participant_entry(conversation_id, recipient, recipient_role)
        if not recipient_entry:
            recipient_entry = ConversationParticipant(
                conversation_id=conversation_id,
                staff=recipient if recipient_role == 'staff' else None,
                super_admin=recipient if recipient_role == 'superadmin' else None,
                last_read_at=datetime.min.replace(tzinfo=timezone.utc)
            )
            db.session.add(recipient_entry)
            db.session.flush()

        if conversation.conversation_type != 'direct':
            continue

        should_send_realtime_notification = (
            recipient_entry.last_notified_at is None
            or (now - recipient_entry.last_notified_at.replace(tzinfo=timezone.utc)) > notification_cooldown
        )
        if not should_send_realtime_notification:
            continue

        frontend_url = os.getenv('FRONTEND_URL', 'http://localhost:5173')
        action_link = f"{frontend_url}/admin/messaging?conversation={conversation_id}"
        email_data = {
            'message': f"You have a new message from {user.name} in one of your conversations.",
            'action_link': action_link
        }
        send_email_in_background(
            subject="You have a new message",
            recipients=[recipient.email],
            template_data=email_data
        )
        push_payload = {
            "title": f"New Message from {user.name}",
            "body": new_message.content,
            "url": f"/admin/messaging?conversation={conversation_id}"
        }
        send_push_notification(recipient, push_payload)
        recipient_entry.last_notified_at = now

    db.session.commit()
    return jsonify(new_message.to_dict()), 201
