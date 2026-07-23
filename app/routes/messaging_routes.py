import os
import uuid
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request, current_app
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required
from sqlalchemy import func, or_
from werkzeug.utils import secure_filename

from app.models import db
from app.models.conversation_model import (
    Conversation,
    ConversationParticipant,
    Message,
    StaffMessage,
    SuperAdminMessage,
    MessageReaction
)
from app.models.department_model import Department
from app.models.message_log_model import MessageLog
from app.models.notification_model import Notification
from app.models.staff_model import Staff
from app.models.super_admin_model import SuperAdmin
from app.models.announcement_model import Announcement
from app.utils.notifications import send_email_in_background, send_push_notification


messaging_bp = Blueprint('messaging', __name__)

DEFAULT_CHANNELS = []


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
        if not participant_entry.is_following:
            participant_entry.is_following = True
            db.session.flush()
        return participant_entry

    participant_entry = ConversationParticipant(
        conversation_id=conversation.id,
        staff=user if role == 'staff' else None,
        super_admin=user if role == 'superadmin' else None,
        last_read_at=datetime.now(timezone.utc),
        is_following=True
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

    # Clean up default channels if they exist in the DB (to get a clean slate)
    from sqlalchemy import text
    try:
        db.session.execute(text("""
            DELETE FROM messages 
            WHERE conversation_id IN (
                SELECT id FROM conversations 
                WHERE conversation_type='channel' AND name IN ('Welcome', 'School Updates', 'Cross-Department Ops')
            )
        """))
        db.session.execute(text("""
            DELETE FROM conversation_participants 
            WHERE conversation_id IN (
                SELECT id FROM conversations 
                WHERE conversation_type='channel' AND name IN ('Welcome', 'School Updates', 'Cross-Department Ops')
            )
        """))
        db.session.execute(text("DELETE FROM conversations WHERE conversation_type='channel' AND name IN ('Welcome', 'School Updates', 'Cross-Department Ops')"))
        db.session.commit()
    except Exception as e:
        db.session.rollback()

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

    part = get_participant_entry(conversation.id, user, role)
    if part and not part.is_following:
        return False

    if role == 'superadmin':
        return True

    if conversation.conversation_type in ('channel', 'department'):
        return True

    return any(item.id == conversation.id for item in user.conversations)


def get_accessible_conversations(user, role):
    ensure_workspace_threads()
    conversations = Conversation.query.order_by(Conversation.updated_at.desc()).all()
    if role == 'superadmin':
        filtered = []
        for conversation in conversations:
            part = get_participant_entry(conversation.id, user, role)
            if part and not part.is_following:
                continue
            if conversation.conversation_type != 'direct' or part:
                filtered.append(conversation)
        return filtered
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

    for participant in conversation.participants:
        admin = participant.super_admin
        if admin and f"superadmin_{admin.id}" != sender_key:
            recipients.append((admin, 'superadmin'))

    unique_recipients = {}
    for recipient, recipient_role in recipients:
        unique_recipients[f"{recipient_role}_{recipient.id}"] = (recipient, recipient_role)
    return list(unique_recipients.values())


def serialize_conversation_summary(conversation, user, role, audit=False):
    last_message = conversation.messages.order_by(db.desc(Message.created_at)).first()
    title = get_conversation_title(conversation, user, role)
    unread_count = 0 if audit else get_conversation_unread_count(conversation, user, role)

    participant_keys = []
    for p in conversation.participants:
        if p.super_admin_id:
            participant_keys.append(f"superadmin_{p.super_admin_id}")
        elif p.staff_id:
            participant_keys.append(f"staff_{p.staff_id}")

    return {
        'id': conversation.id,
        'title': title,
        'participant_names': title,
        'last_message': last_message.content if last_message else "No messages yet.",
        'last_message_time': (
            last_message.created_at.strftime('%Y-%m-%dT%H:%M:%SZ')
            if last_message
            else conversation.created_at.strftime('%Y-%m-%dT%H:%M:%SZ')
        ),
        'unread_count': unread_count,
        'conversation_type': conversation.conversation_type,
        'department_id': conversation.department_id,
        'department_name': conversation.department.name if conversation.department else None,
        'is_restricted': conversation.conversation_type == 'department',
        'audit_only': audit,
        'participant_keys': participant_keys
    }


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

    all_staff = Staff.query.all()
    for staff in all_staff:
        is_me = (role == 'staff' and staff.id == current_user.id)
        name = f"{staff.name} (You)" if is_me else staff.name
        users.append({'id': f'staff_{staff.id}', 'name': name, 'role': 'Staff', 'email': staff.email})

    all_super_admins = SuperAdmin.query.all()
    for admin in all_super_admins:
        is_me = (role == 'superadmin' and admin.id == current_user.id)
        name = f"{admin.name} (You)" if is_me else admin.name
        users.append({'id': f'superadmin_{admin.id}', 'name': name, 'role': 'Super Admin', 'email': admin.email})

    return jsonify(users), 200


@messaging_bp.route('/conversations', methods=['GET'])
@jwt_required()
def get_conversations():
    user, role = get_current_user()
    if not user:
        return jsonify({"error": "User not found"}), 404

    response_data = []
    for conversation in get_accessible_conversations(user, role):
        response_data.append(serialize_conversation_summary(conversation, user, role))

    response_data.sort(key=lambda item: item['last_message_time'], reverse=True)
    return jsonify(response_data), 200


@messaging_bp.route('/conversations/audit', methods=['GET'])
@jwt_required()
def get_audit_conversations():
    user, role = get_current_user()
    if not user:
        return jsonify({"error": "User not found"}), 404
    if role != 'superadmin':
        return jsonify({"error": "Forbidden"}), 403

    ensure_workspace_threads()
    conversations = Conversation.query.order_by(Conversation.updated_at.desc()).all()
    response_data = [
        serialize_conversation_summary(conversation, user, role, audit=True)
        for conversation in conversations
    ]
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

    added_keys = {f"{role}_{user.id}"}

    new_conversation = Conversation(conversation_type='direct')
    if role == 'superadmin':
        new_conversation.participants.append(ConversationParticipant(super_admin=user))
    else:
        new_conversation.participants.append(ConversationParticipant(staff=user))

    for participant_token in participant_ids:
        if participant_token in added_keys:
            continue
        added_keys.add(participant_token)
        
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


@messaging_bp.route('/channels', methods=['POST', 'OPTIONS'])
@jwt_required(optional=True)
def create_channel():
    if request.method == 'OPTIONS':
        return jsonify({}), 200

    user, role = get_current_user()
    if not user or role not in {'superadmin', 'staff'}:
        return jsonify({"error": "Only staff and super admins can create channels."}), 403

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


@messaging_bp.route('/channels/<int:channel_id>', methods=['PUT', 'PATCH', 'OPTIONS'])
@jwt_required(optional=True)
def rename_channel(channel_id):
    if request.method == 'OPTIONS':
        return jsonify({}), 200

    user, role = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    
    conversation = Conversation.query.get_or_404(channel_id)
    if conversation.conversation_type not in ('channel', 'department'):
        return jsonify({"error": "Can only rename channels or department threads"}), 400

    data = request.get_json() or {}
    new_name = (data.get('name') or data.get('title') or '').strip()
    if not new_name:
        return jsonify({"error": "Channel name cannot be empty"}), 400

    conversation.name = new_name
    conversation.updated_at = datetime.utcnow()
    db.session.commit()

    try:
        from app import socketio
        socketio.emit('conversation_updated', {
            'conversation_id': conversation.id,
            'name': new_name
        })
    except Exception as e:
        print("Socket emit failed on rename_channel:", e)

    return jsonify({
        "id": conversation.id,
        "title": conversation.name,
        "message": "Channel renamed successfully"
    }), 200


@messaging_bp.route('/conversations/<int:conversation_id>/unfollow', methods=['POST'])
@jwt_required()
def unfollow_conversation(conversation_id):
    """Remove the current user from a channel conversation so it no longer appears in their sidebar."""
    user, role = get_current_user()
    if not user:
        return jsonify({"error": "User not found"}), 404

    conversation = Conversation.query.get_or_404(conversation_id)
    if conversation.conversation_type not in ('channel', 'department'):
        return jsonify({"error": "You can only unfollow channels."}), 400

    participant = get_participant_entry(conversation_id, user, role)
    if not participant:
        participant = ConversationParticipant(
            conversation_id=conversation_id,
            staff=user if role == 'staff' else None,
            super_admin=user if role == 'superadmin' else None,
            last_read_at=datetime.now(timezone.utc)
        )
        db.session.add(participant)
    participant.is_following = False
    db.session.commit()

    return jsonify({"message": "Unfollowed channel successfully."}), 200


@messaging_bp.route('/conversations/<int:conversation_id>/mark-unread', methods=['POST'])
@jwt_required()
def mark_conversation_unread(conversation_id):
    """Reset the user's last_read_at so the conversation appears unread."""
    user, role = get_current_user()
    if not user:
        return jsonify({"error": "User not found"}), 404

    conversation = Conversation.query.get_or_404(conversation_id)
    if not can_access_conversation(user, role, conversation):
        return jsonify({"error": "Forbidden"}), 403

    participant = get_participant_entry(conversation_id, user, role)
    if participant:
        # Set last_read_at to epoch to make everything appear unread
        participant.last_read_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        db.session.commit()

    return jsonify({"message": "Marked as unread."}), 200


@messaging_bp.route('/conversations/<int:conversation_id>/favorite', methods=['POST'])
@jwt_required()
def toggle_favorite_conversation(conversation_id):
    """Placeholder for favorite toggle - returns success for frontend state management."""
    user, role = get_current_user()
    if not user:
        return jsonify({"error": "User not found"}), 404

    conversation = Conversation.query.get_or_404(conversation_id)
    if not can_access_conversation(user, role, conversation):
        return jsonify({"error": "Forbidden"}), 403

    return jsonify({"message": "Favorite toggled."}), 200


@messaging_bp.route('/conversations/<int:conversation_id>/messages', methods=['GET'])
@jwt_required()
def get_messages(conversation_id):
    user, role = get_current_user()
    conversation = Conversation.query.get_or_404(conversation_id)

    if not can_access_conversation(user, role, conversation):
        return jsonify({"error": "Forbidden"}), 403

    participant_entry = get_or_create_participant_entry(conversation, user, role)
    old_last_read_at = participant_entry.last_read_at
    participant_entry.last_read_at = datetime.now(timezone.utc)
    db.session.commit()

    messages = conversation.messages.all()
    return jsonify({
        'messages': [message.to_dict() for message in messages],
        'last_read_at': old_last_read_at.isoformat() + 'Z' if old_last_read_at else None
    }), 200


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

    reply_to_message_id = data.get('reply_to_message_id')

    if role == 'superadmin':
        new_message = SuperAdminMessage(content=content, sender_id=user.id, reply_to_message_id=reply_to_message_id)
    else:
        new_message = StaffMessage(content=content, sender_id=user.id, reply_to_message_id=reply_to_message_id)

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

    # Parse mentions from payload
    mentions = data.get('mentions', [])
    notified_mentions = set()
    from app.utils.notifications import enqueue_user_notification
    import time
    import hashlib

    for mention in mentions:
        m_id = mention.get('id')
        m_role = mention.get('role')
        if m_id and m_role:
            key = f"{m_role}_{m_id}"
            if key not in notified_mentions:
                notified_mentions.add(key)
                # 15s window to deduplicate consecutive mentions
                raw_key = f"mention:{conversation.id}:{m_role}:{m_id}:{int(time.time() / 15)}"
                idempotency_key = hashlib.md5(raw_key.encode('utf-8')).hexdigest()
                enqueue_user_notification(
                    user_id=m_id,
                    user_role=m_role,
                    message=f"{user.name} @mentioned you in conversation '{conversation.name or 'a conversation'}'.",
                    category='mention',
                    target_type="Conversation",
                    target_id=conversation.id,
                    target_link=f"/admin/messaging?conversation={conversation.id}",
                    idempotency_key=idempotency_key
                )

    for recipient, recipient_role in recipients_for_conversation(conversation, user, role):
        recipient_key = f"{recipient_role}_{recipient.id}"
        # Skip generic notification if already notified as a mention
        if recipient_key in notified_mentions:
            pass
        else:
            # 15s window to deduplicate consecutive messages
            raw_key = f"msg:{conversation.id}:{recipient_role}:{recipient.id}:{int(time.time() / 15)}"
            idempotency_key = hashlib.md5(raw_key.encode('utf-8')).hexdigest()
            
            enqueue_user_notification(
                user_id=recipient.id,
                user_role=recipient_role,
                message=f"You have a new message in {conversation.name or 'a conversation'}.",
                category='general',
                target_type="Conversation",
                target_id=conversation.id,
                target_link=f"/admin/messaging?conversation={conversation.id}",
                idempotency_key=idempotency_key
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

    conversation.updated_at = datetime.utcnow()
    db.session.commit()
    
    # Broadcast the message in real time to the room using Socket.IO!
    from app import socketio
    message_dict = new_message.to_dict()
    socketio.emit('new_message', message_dict, room=f"conversation_{conversation_id}")
    
    # Also notify all participants (including sender) to update their conversation lists/unread counts in real time
    for recipient, recipient_role in recipients_for_conversation(conversation, user, role):
        socketio.emit('conversation_updated', {
            'conversation_id': conversation_id,
            'recipient_id': recipient.id,
            'recipient_role': recipient_role
        })
    socketio.emit('conversation_updated', {
        'conversation_id': conversation_id,
        'recipient_id': user.id,
        'recipient_role': role
    })

    return jsonify(message_dict), 201

@messaging_bp.route('/conversations/<int:conversation_id>/upload', methods=['POST'])
@jwt_required()
def upload_message_file(conversation_id):
    user, role = get_current_user()
    if not user:
        return jsonify({'error': 'Unauthorized'}), 401

    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'Empty filename'}), 400

    conversation = Conversation.query.get_or_404(conversation_id)
    if not can_access_conversation(user, role, conversation):
        return jsonify({"error": "Forbidden"}), 403

    get_or_create_participant_entry(conversation, user, role)

    upload_folder = os.path.join(current_app.root_path, 'static', 'uploads', 'messaging')
    os.makedirs(upload_folder, exist_ok=True)
    filename = secure_filename(file.filename)
    unique_filename = f"{uuid.uuid4().hex}_{filename}"
    file_path = os.path.join(upload_folder, unique_filename)
    file.save(file_path)
    relative_path = f"/static/uploads/messaging/{unique_filename}"

    content = request.form.get('content', '').strip()
    if not content:
        content = f"Uploaded attachment: {filename}"

    reply_to_message_id_raw = request.form.get('reply_to_message_id')
    reply_to_message_id = None
    if reply_to_message_id_raw and reply_to_message_id_raw != 'null' and reply_to_message_id_raw != 'undefined':
        try:
            reply_to_message_id = int(reply_to_message_id_raw)
        except ValueError:
            pass

    if role == 'superadmin':
        new_message = SuperAdminMessage(
            content=content,
            sender_id=user.id,
            file_path=relative_path,
            filename=filename,
            reply_to_message_id=reply_to_message_id
        )
    else:
        new_message = StaffMessage(
            content=content,
            sender_id=user.id,
            file_path=relative_path,
            filename=filename,
            reply_to_message_id=reply_to_message_id
        )

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

    conversation.updated_at = datetime.utcnow()
    db.session.commit()

    from app import socketio
    message_dict = new_message.to_dict()
    socketio.emit('new_message', message_dict, room=f"conversation_{conversation_id}")

    for recipient, recipient_role in recipients_for_conversation(conversation, user, role):
        socketio.emit('conversation_updated', {
            'conversation_id': conversation_id,
            'recipient_id': recipient.id,
            'recipient_role': recipient_role
        })
    socketio.emit('conversation_updated', {
        'conversation_id': conversation_id,
        'recipient_id': user.id,
        'recipient_role': role
    })

    return jsonify(message_dict), 201

# Announcements
@messaging_bp.route('/announcements', methods=['GET'])
@jwt_required()
def get_announcements():
    try:
        announcements = Announcement.query.order_by(Announcement.created_at.desc()).all()
        return jsonify([a.to_dict() for a in announcements]), 200
    except Exception as e:
        print(f"Error fetching announcements: {e}")
        return jsonify({"error": "Failed to fetch announcements"}), 500

@messaging_bp.route('/announcements', methods=['POST'])
@jwt_required()
def create_announcement():
    user, role = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json() or {}
    title = data.get('title')
    content = data.get('content')

    if not title or not content:
        return jsonify({"error": "Title and content are required"}), 400

    try:
        announcement = Announcement(
            title=title,
            content=content,
            created_by_name=user.name
        )
        db.session.add(announcement)
        db.session.commit()
        return jsonify(announcement.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        print(f"Error creating announcement: {e}")
        return jsonify({"error": "Failed to create announcement"}), 500


@messaging_bp.route('/messages/<int:message_id>/react', methods=['POST'])
@jwt_required()
def toggle_message_reaction(message_id):
    user, role = get_current_user()
    if not user:
        return jsonify({"error": "User not found"}), 404

    message = Message.query.get_or_404(message_id)
    data = request.get_json() or {}
    emoji = data.get('emoji')

    if not emoji:
        return jsonify({"error": "Emoji is required"}), 400

    # Toggle reaction
    if role == 'superadmin':
        reaction = MessageReaction.query.filter_by(
            message_id=message_id,
            emoji=emoji,
            super_admin_id=user.id
        ).first()
    else:
        reaction = MessageReaction.query.filter_by(
            message_id=message_id,
            emoji=emoji,
            staff_id=user.id
        ).first()

    if reaction:
        db.session.delete(reaction)
        action = "removed"
    else:
        reaction = MessageReaction(
            message_id=message_id,
            emoji=emoji,
            staff_id=user.id if role == 'staff' else None,
            super_admin_id=user.id if role == 'superadmin' else None
        )
        db.session.add(reaction)
        action = "added"

    db.session.commit()

    # Emit socket event to notify other clients in the conversation
    from app import socketio
    socketio.emit('message_reaction_toggled', {
        'message_id': message_id,
        'reactions': [r.to_dict() for r in message.reactions]
    }, room=f"conversation_{message.conversation_id}")

    return jsonify({
        "message": f"Reaction {action}",
        "reactions": [r.to_dict() for r in message.reactions]
    }), 200


# Socket.IO Event Handlers
from flask_socketio import join_room, leave_room
from app import socketio

@socketio.on('join')
def on_join(data):
    room_id = data.get('conversation_id')
    if room_id:
        room = str(room_id) if str(room_id).startswith('user_') else f"conversation_{room_id}"
        join_room(room)

@socketio.on('leave')
def on_leave(data):
    room_id = data.get('conversation_id')
    if room_id:
        room = str(room_id) if str(room_id).startswith('user_') else f"conversation_{room_id}"
        leave_room(room)

# In-memory online user tracking
online_sids = {}

@socketio.on('user_online')
def on_user_online(data):
    user_id = data.get('id')
    user_role = data.get('role')
    if user_id and user_role:
        user_key = f"{user_role}_{user_id}"
        online_sids[request.sid] = user_key
        socketio.emit('online_users_list', list(set(online_sids.values())))

@socketio.on('disconnect')
def on_disconnect_presence():
    if request.sid in online_sids:
        del online_sids[request.sid]
        socketio.emit('online_users_list', list(set(online_sids.values())))
