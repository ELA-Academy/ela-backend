from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required, get_jwt
from app.models.message_log_model import MessageLog
from collections import OrderedDict

administration_bp = Blueprint('administration', __name__)

@administration_bp.route('/overview', methods=['GET'])
@jwt_required()
def get_administration_overview():
    """
    Provides a summary of key metrics for the Administration dashboard.
    """
    try:
        total_staff_onboarded = 0
        upcoming_events = 0
        facility_requests = 0
        open_support_tickets = 0

        data = {
            "total_staff_onboarded": total_staff_onboarded,
            "upcoming_events": upcoming_events,
            "facility_requests": facility_requests,
            "open_support_tickets": open_support_tickets,
        }

        return jsonify(data), 200
    except Exception as e:
        return jsonify({"error": "An error occurred while fetching administration data."}), 500

@administration_bp.route('/message-log', methods=['GET'])
@jwt_required()
def get_message_log():
    claims = get_jwt()
    if 'Administration Department' not in claims.get('departmentNames', []):
        return jsonify({"error": "Unauthorized"}), 403

    logs = MessageLog.query.order_by(MessageLog.conversation_id, MessageLog.created_at).all()
    
    conversations = OrderedDict()
    for log in logs:
        if log.conversation_id not in conversations:
            conversations[log.conversation_id] = {
                'id': log.conversation_id,
                'participant_names': log.recipient_names,
                'messages': []
            }
        conversations[log.conversation_id]['messages'].append({
            'id': log.id,
            'content': log.content,
            'created_at': log.created_at.isoformat() + 'Z',
            'sender_name': log.sender_name,
            'sender_id': log.sender_id,
            'sender_type': log.sender_type
        })

    response_data = []
    for convo_id, convo_data in conversations.items():
        if convo_data['messages']:
            last_msg = convo_data['messages'][-1]
            convo_data['last_message_time'] = last_msg['created_at']
            convo_data['last_message'] = last_msg['content']
        else:
            convo_data['last_message_time'] = None 
            convo_data['last_message'] = "No messages yet."
        response_data.append(convo_data)
    
    response_data.sort(key=lambda x: x['last_message_time'], reverse=True)

    return jsonify(response_data), 200