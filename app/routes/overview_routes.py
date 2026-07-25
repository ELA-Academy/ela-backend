import json
from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required
from app.models import db
from app.models.overview_card_model import OverviewCard, SpaceBookmark
from app.models.board_model import Board, BoardGroup, BoardTask, WorkspaceDoc
from app.models.board_model_extensions import BoardCustomField, TaskCustomFieldValue
from app.routes.messaging_routes import get_current_user
from sqlalchemy import func
from datetime import datetime

overview_bp = Blueprint('overview', __name__)


def _get_user_ids():
    """Return (staff_id, super_admin_id) tuple for the current user."""
    user, role = get_current_user()
    if not user:
        return None, None, None, None
    if role == 'staff':
        return user, role, user.id, None
    return user, role, None, user.id


def _get_all_descendant_list_ids(space_id):
    """Recursively collect IDs of all lists (non-folder boards) under a space."""
    list_ids = []
    queue = [space_id]
    visited = set()

    while queue:
        parent_id = queue.pop(0)
        if parent_id in visited:
            continue
        visited.add(parent_id)

        children = Board.query.filter_by(parent_id=parent_id, is_archived=False).all()
        for child in children:
            if child.is_folder:
                queue.append(child.id)
            else:
                list_ids.append(child.id)

    return list_ids


# ─── Overview Cards CRUD ──────────────────────────────────────────────────────

@overview_bp.route('/boards/<int:space_id>/overview-cards', methods=['GET'])
@jwt_required()
def get_overview_cards(space_id):
    """List all overview cards for a space."""
    cards = OverviewCard.query.filter_by(board_id=space_id).order_by(OverviewCard.position).all()
    return jsonify([c.to_dict() for c in cards])


@overview_bp.route('/boards/<int:space_id>/overview-cards', methods=['POST'])
@jwt_required()
def create_overview_card(space_id):
    """Create a new overview card."""
    user, role, staff_id, admin_id = _get_user_ids()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json()
    if not data or not data.get('name') or not data.get('card_type'):
        return jsonify({"error": "name and card_type are required"}), 400

    # Position: append at the end
    max_pos = db.session.query(func.max(OverviewCard.position)).filter_by(board_id=space_id).scalar() or 0

    data_source_id = int(data['data_source_board_id']) if data.get('data_source_board_id') else None
    measure_id = int(data['measure_field_id']) if data.get('measure_field_id') else None

    card = OverviewCard(
        board_id=space_id,
        name=data['name'],
        card_type=data['card_type'],
        position=max_pos + 1,
        data_source_board_id=data_source_id,
        measure_field_id=measure_id,
        calculation=data.get('calculation', 'sum'),
        units=data.get('units', 'None'),
        filters_json=json.dumps(data.get('filters', {})) if data.get('filters') else None,
        created_by_staff_id=staff_id,
        created_by_super_admin_id=admin_id,
    )
    db.session.add(card)
    db.session.commit()
    return jsonify(card.to_dict()), 201


@overview_bp.route('/boards/<int:space_id>/overview-cards/<int:card_id>', methods=['PUT'])
@jwt_required()
def update_overview_card(space_id, card_id):
    """Update an overview card's settings."""
    card = OverviewCard.query.filter_by(id=card_id, board_id=space_id).first()
    if not card:
        return jsonify({"error": "Card not found"}), 404

    data = request.get_json()
    if 'name' in data and data['name']:
        card.name = data['name']
    if 'data_source_board_id' in data:
        card.data_source_board_id = int(data['data_source_board_id']) if data['data_source_board_id'] else None
    if 'measure_field_id' in data:
        card.measure_field_id = int(data['measure_field_id']) if data['measure_field_id'] else None
    if 'calculation' in data:
        card.calculation = str(data['calculation']).lower()
    if 'units' in data:
        card.units = data['units']
    if 'filters' in data:
        card.filters_json = json.dumps(data['filters'])
    if 'position' in data:
        card.position = data['position']

    db.session.commit()
    return jsonify(card.to_dict())


@overview_bp.route('/boards/<int:space_id>/overview-cards/<int:card_id>', methods=['DELETE'])
@jwt_required()
def delete_overview_card(space_id, card_id):
    """Delete an overview card."""
    card = OverviewCard.query.filter_by(id=card_id, board_id=space_id).first()
    if not card:
        return jsonify({"error": "Card not found"}), 404
    db.session.delete(card)
    db.session.commit()
    return jsonify({"message": "Card deleted"}), 200


# ─── Card Aggregation ─────────────────────────────────────────────────────────

@overview_bp.route('/boards/<int:space_id>/overview-cards/<int:card_id>/aggregate', methods=['GET'])
@jwt_required()
def get_card_aggregate(space_id, card_id):
    """Compute a calculation card's aggregated value."""
    card = OverviewCard.query.filter_by(id=card_id, board_id=space_id).first()
    if not card:
        return jsonify({"error": "Card not found"}), 404

    if card.card_type != 'calculation':
        return jsonify({"value": None, "error": "Not a calculation card"}), 400

    if not card.data_source_board_id:
        return jsonify({"value": 0, "refreshed_at": datetime.utcnow().isoformat() + 'Z'})

    # Get all tasks in the data source board
    filters = {}
    if card.filters_json:
        try:
            filters = json.loads(card.filters_json)
        except Exception:
            pass

    group_ids = [g.id for g in BoardGroup.query.filter_by(board_id=card.data_source_board_id).all()]
    if not group_ids:
        return jsonify({"value": 0, "count": 0, "refreshed_at": datetime.utcnow().isoformat() + 'Z'})

    task_query = BoardTask.query.filter(BoardTask.group_id.in_(group_ids))

    # Apply filters
    if not filters.get('show_closed', False):
        task_query = task_query.filter(BoardTask.status != 'Done')
    if not filters.get('show_archived', True):
        pass  # Board-level archiving is handled elsewhere

    tasks = task_query.all()

    if card.calculation == 'count':
        value = len(tasks)
    elif card.measure_field_id:
        # Get custom field values for the measure field
        task_ids = [t.id for t in tasks]
        if not task_ids:
            return jsonify({"value": 0, "count": 0, "refreshed_at": datetime.utcnow().isoformat() + 'Z'})

        field_values = TaskCustomFieldValue.query.filter(
            TaskCustomFieldValue.task_id.in_(task_ids),
            TaskCustomFieldValue.field_id == card.measure_field_id
        ).all()

        import re
        numeric_values = []
        for fv in field_values:
            if not fv.value_json:
                continue
            raw_val = fv.value_json
            try:
                raw_val = json.loads(fv.value_json)
            except Exception:
                pass

            if isinstance(raw_val, (int, float)):
                numeric_values.append(float(raw_val))
            elif isinstance(raw_val, str):
                cleaned = re.sub(r'[^\d.-]', '', raw_val)
                if cleaned and cleaned != '.' and cleaned != '-':
                    try:
                        numeric_values.append(float(cleaned))
                    except ValueError:
                        pass

        calc_type = (card.calculation or 'sum').lower()
        if not numeric_values:
            value = 0
        elif calc_type == 'sum':
            value = sum(numeric_values)
        elif calc_type == 'average':
            value = sum(numeric_values) / len(numeric_values)
        elif calc_type == 'min':
            value = min(numeric_values)
        elif calc_type == 'max':
            value = max(numeric_values)
        else:
            value = sum(numeric_values)
    else:
        value = len(tasks)

    # Format value
    if isinstance(value, float):
        value = round(value, 2)

    return jsonify({
        "value": value,
        "count": len(tasks),
        "refreshed_at": datetime.utcnow().isoformat() + 'Z'
    })


@overview_bp.route('/boards/<int:space_id>/overview-cards/<int:card_id>/data', methods=['GET'])
@jwt_required()
def get_card_data(space_id, card_id):
    """Return raw task rows that feed a calculation card."""
    card = OverviewCard.query.filter_by(id=card_id, board_id=space_id).first()
    if not card or card.card_type != 'calculation' or not card.data_source_board_id:
        return jsonify({"tasks": [], "total": 0})

    filters = {}
    if card.filters_json:
        try:
            filters = json.loads(card.filters_json)
        except Exception:
            pass

    group_ids = [g.id for g in BoardGroup.query.filter_by(board_id=card.data_source_board_id).all()]
    if not group_ids:
        return jsonify({"tasks": [], "total": 0})

    task_query = BoardTask.query.filter(BoardTask.group_id.in_(group_ids))
    if not filters.get('show_closed', False):
        task_query = task_query.filter(BoardTask.status != 'Done')

    tasks = task_query.order_by(BoardTask.created_at.desc()).all()

    # Get measure field values
    measure_values = {}
    if card.measure_field_id:
        task_ids = [t.id for t in tasks]
        if task_ids:
            field_values = TaskCustomFieldValue.query.filter(
                TaskCustomFieldValue.task_id.in_(task_ids),
                TaskCustomFieldValue.field_id == card.measure_field_id
            ).all()
            for fv in field_values:
                try:
                    measure_values[fv.task_id] = json.loads(fv.value_json)
                except Exception:
                    measure_values[fv.task_id] = fv.value_json

    # Get measure field name
    measure_field_name = None
    if card.measure_field_id:
        field = BoardCustomField.query.get(card.measure_field_id)
        if field:
            measure_field_name = field.name

    result_tasks = []
    for t in tasks:
        result_tasks.append({
            'id': t.id,
            'title': t.title,
            'status': t.status,
            'priority': t.priority,
            'assignee_name': t.responsible_staff.name if t.responsible_staff else (t.responsible_super_admin.name if t.responsible_super_admin else ''),
            'due_date': t.due_date.isoformat() if t.due_date else None,
            'measure_value': measure_values.get(t.id),
        })

    return jsonify({
        "tasks": result_tasks,
        "total": len(result_tasks),
        "measure_field_name": measure_field_name,
    })


# ─── Space Children / Overview Info ───────────────────────────────────────────

@overview_bp.route('/boards/<int:space_id>/overview/children', methods=['GET'])
@jwt_required()
def get_space_children(space_id):
    """List all child folders and lists for the space."""
    children = Board.query.filter_by(parent_id=space_id, is_archived=False).order_by(Board.name).all()

    folders = []
    lists = []
    for child in children:
        item = {
            'id': child.id,
            'name': child.name,
            'is_folder': child.is_folder,
            'color': child.color,
            'icon': child.icon,
            'tasks_count': sum(len(g.tasks) for g in child.groups) if not child.is_folder else 0,
        }
        if child.is_folder:
            # Also get sub-lists in the folder
            sub_lists = Board.query.filter_by(parent_id=child.id, is_archived=False).all()
            item['children'] = [{
                'id': sl.id, 'name': sl.name, 'tasks_count': sum(len(g.tasks) for g in sl.groups)
            } for sl in sub_lists]
            folders.append(item)
        else:
            lists.append(item)

    return jsonify({"folders": folders, "lists": lists})


@overview_bp.route('/boards/<int:space_id>/overview/recent', methods=['GET'])
@jwt_required()
def get_space_recent(space_id):
    """Return recently modified tasks and docs in the space."""
    # Get all list IDs under this space
    list_ids = _get_all_descendant_list_ids(space_id)
    list_ids.append(space_id)

    # Recent tasks (last 10 modified)
    group_ids = [g.id for g in BoardGroup.query.filter(BoardGroup.board_id.in_(list_ids)).all()]
    recent_tasks = []
    if group_ids:
        tasks = BoardTask.query.filter(
            BoardTask.group_id.in_(group_ids)
        ).order_by(BoardTask.created_at.desc()).limit(10).all()

        for t in tasks:
            board = Board.query.get(t.group.board_id) if t.group else None
            recent_tasks.append({
                'id': t.id,
                'title': t.title,
                'type': 'task',
                'board_name': board.name if board else '',
                'board_id': board.id if board else None,
                'created_at': t.created_at.isoformat() + 'Z',
            })

    # Recent docs
    recent_docs = []
    try:
        docs = WorkspaceDoc.query.filter(
            WorkspaceDoc.board_id.in_(list_ids)
        ).order_by(WorkspaceDoc.updated_at.desc()).limit(10).all()
        for d in docs:
            board = Board.query.get(d.board_id) if d.board_id else None
            recent_docs.append({
                'id': d.id,
                'title': d.title,
                'type': 'doc',
                'board_name': board.name if board else '',
                'board_id': board.id if board else None,
                'updated_at': d.updated_at.isoformat() + 'Z',
            })
    except Exception:
        pass

    return jsonify({"tasks": recent_tasks, "docs": recent_docs})


@overview_bp.route('/boards/<int:space_id>/overview/docs', methods=['GET'])
@jwt_required()
def get_space_docs(space_id):
    """Return all docs in the space."""
    list_ids = _get_all_descendant_list_ids(space_id)
    list_ids.append(space_id)

    try:
        docs = WorkspaceDoc.query.filter(
            WorkspaceDoc.board_id.in_(list_ids)
        ).order_by(WorkspaceDoc.updated_at.desc()).all()
        return jsonify([{
            'id': d.id,
            'title': d.title,
            'board_id': d.board_id,
            'board_name': Board.query.get(d.board_id).name if d.board_id else '',
            'updated_at': d.updated_at.isoformat() + 'Z',
        } for d in docs])
    except Exception:
        return jsonify([])


# ─── Bookmarks (per-user) ────────────────────────────────────────────────────

@overview_bp.route('/boards/<int:space_id>/overview/bookmarks', methods=['GET'])
@jwt_required()
def get_bookmarks(space_id):
    """Get the current user's bookmarks for this space."""
    user, role, staff_id, admin_id = _get_user_ids()
    if not user:
        return jsonify([])

    query = SpaceBookmark.query.filter_by(board_id=space_id)
    if staff_id:
        query = query.filter_by(staff_id=staff_id)
    else:
        query = query.filter_by(super_admin_id=admin_id)

    bookmarks = query.order_by(SpaceBookmark.position).all()
    return jsonify([b.to_dict() for b in bookmarks])


@overview_bp.route('/boards/<int:space_id>/overview/bookmarks', methods=['POST'])
@jwt_required()
def create_bookmark(space_id):
    """Add a bookmark for the current user."""
    user, role, staff_id, admin_id = _get_user_ids()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json()
    if not data or not data.get('title'):
        return jsonify({"error": "title is required"}), 400

    max_pos = db.session.query(func.max(SpaceBookmark.position)).filter_by(board_id=space_id).scalar() or 0

    bookmark = SpaceBookmark(
        board_id=space_id,
        title=data['title'],
        url=data.get('url'),
        bookmark_type=data.get('bookmark_type', 'url'),
        target_id=data.get('target_id'),
        staff_id=staff_id,
        super_admin_id=admin_id,
        position=max_pos + 1,
    )
    db.session.add(bookmark)
    db.session.commit()
    return jsonify(bookmark.to_dict()), 201


@overview_bp.route('/boards/<int:space_id>/overview/bookmarks/<int:bookmark_id>', methods=['DELETE'])
@jwt_required()
def delete_bookmark(space_id, bookmark_id):
    """Delete a bookmark."""
    user, role, staff_id, admin_id = _get_user_ids()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    query = SpaceBookmark.query.filter_by(id=bookmark_id, board_id=space_id)
    if staff_id:
        query = query.filter_by(staff_id=staff_id)
    else:
        query = query.filter_by(super_admin_id=admin_id)

    bookmark = query.first()
    if not bookmark:
        return jsonify({"error": "Bookmark not found"}), 404

    db.session.delete(bookmark)
    db.session.commit()
    return jsonify({"message": "Bookmark deleted"}), 200


# ─── Report Generation ────────────────────────────────────────────────────────

@overview_bp.route('/boards/<int:space_id>/overview/generate-report', methods=['POST'])
@jwt_required()
def generate_report(space_id):
    """Generate a formatted report doc from overview card data."""
    user, role, staff_id, admin_id = _get_user_ids()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    space = Board.query.get(space_id)
    if not space:
        return jsonify({"error": "Space not found"}), 404

    cards = OverviewCard.query.filter_by(board_id=space_id).order_by(OverviewCard.position).all()

    # Build HTML report
    now = datetime.utcnow().strftime('%B %d, %Y at %I:%M %p')
    html_parts = [
        f'<h1>{space.name} — Overview Report</h1>',
        f'<p style="color: #666;">Generated on {now}</p>',
        '<hr/>',
    ]

    # Summary table
    if cards:
        html_parts.append('<h2>Summary</h2>')
        html_parts.append('<table style="width:100%; border-collapse:collapse; margin-bottom:24px;">')
        html_parts.append('<thead><tr style="background:#f1f5f9;">')
        html_parts.append('<th style="padding:8px 12px; text-align:left; border:1px solid #e2e8f0;">Card Name</th>')
        html_parts.append('<th style="padding:8px 12px; text-align:left; border:1px solid #e2e8f0;">Type</th>')
        html_parts.append('<th style="padding:8px 12px; text-align:right; border:1px solid #e2e8f0;">Value</th>')
        html_parts.append('</tr></thead><tbody>')

        for card in cards:
            if card.card_type != 'calculation':
                continue

            # Compute aggregate inline
            value = _compute_card_value(card)
            units_prefix = ''
            units_suffix = ''
            if card.units == '$':
                units_prefix = '$'
            elif card.units == '€':
                units_prefix = '€'
            elif card.units == '%':
                units_suffix = '%'

            html_parts.append(f'<tr>')
            html_parts.append(f'<td style="padding:8px 12px; border:1px solid #e2e8f0;">{card.name}</td>')
            html_parts.append(f'<td style="padding:8px 12px; border:1px solid #e2e8f0;">{card.calculation or "count"}</td>')
            html_parts.append(f'<td style="padding:8px 12px; border:1px solid #e2e8f0; text-align:right; font-weight:bold;">{units_prefix}{value:,.2f}{units_suffix}</td>')
            html_parts.append(f'</tr>')

        html_parts.append('</tbody></table>')

    # Detailed data per card
    for card in cards:
        if card.card_type != 'calculation' or not card.data_source_board_id:
            continue

        html_parts.append(f'<h3>{card.name}</h3>')
        source_board = Board.query.get(card.data_source_board_id)
        if source_board:
            html_parts.append(f'<p style="color:#666;">Data source: {source_board.name}</p>')

        # Get tasks
        group_ids = [g.id for g in BoardGroup.query.filter_by(board_id=card.data_source_board_id).all()]
        if not group_ids:
            html_parts.append('<p><em>No data</em></p>')
            continue

        tasks = BoardTask.query.filter(BoardTask.group_id.in_(group_ids)).order_by(BoardTask.created_at.desc()).limit(100).all()

        # Get measure values
        measure_values = {}
        measure_field_name = 'Value'
        if card.measure_field_id:
            field = BoardCustomField.query.get(card.measure_field_id)
            if field:
                measure_field_name = field.name
            task_ids = [t.id for t in tasks]
            if task_ids:
                fvs = TaskCustomFieldValue.query.filter(
                    TaskCustomFieldValue.task_id.in_(task_ids),
                    TaskCustomFieldValue.field_id == card.measure_field_id
                ).all()
                for fv in fvs:
                    try:
                        measure_values[fv.task_id] = json.loads(fv.value_json)
                    except Exception:
                        measure_values[fv.task_id] = fv.value_json

        html_parts.append('<table style="width:100%; border-collapse:collapse; margin-bottom:24px;">')
        html_parts.append('<thead><tr style="background:#f1f5f9;">')
        html_parts.append(f'<th style="padding:6px 10px; text-align:left; border:1px solid #e2e8f0;">Task</th>')
        html_parts.append(f'<th style="padding:6px 10px; text-align:right; border:1px solid #e2e8f0;">{measure_field_name}</th>')
        html_parts.append(f'<th style="padding:6px 10px; text-align:left; border:1px solid #e2e8f0;">Assignee</th>')
        html_parts.append(f'<th style="padding:6px 10px; text-align:left; border:1px solid #e2e8f0;">Due Date</th>')
        html_parts.append(f'<th style="padding:6px 10px; text-align:left; border:1px solid #e2e8f0;">Status</th>')
        html_parts.append('</tr></thead><tbody>')

        for t in tasks:
            assignee = t.responsible_staff.name if t.responsible_staff else (t.responsible_super_admin.name if t.responsible_super_admin else '-')
            due = t.due_date.strftime('%m/%d/%y') if t.due_date else '-'
            mv = measure_values.get(t.id, '-')
            if isinstance(mv, (int, float)):
                mv = f'{mv:,.2f}'
            html_parts.append(f'<tr>')
            html_parts.append(f'<td style="padding:6px 10px; border:1px solid #e2e8f0;">{t.title}</td>')
            html_parts.append(f'<td style="padding:6px 10px; border:1px solid #e2e8f0; text-align:right;">{mv}</td>')
            html_parts.append(f'<td style="padding:6px 10px; border:1px solid #e2e8f0;">{assignee}</td>')
            html_parts.append(f'<td style="padding:6px 10px; border:1px solid #e2e8f0;">{due}</td>')
            html_parts.append(f'<td style="padding:6px 10px; border:1px solid #e2e8f0;">{t.status}</td>')
            html_parts.append(f'</tr>')

        html_parts.append('</tbody></table>')

    html_content = '\n'.join(html_parts)

    creator_name = getattr(user, 'name', None) or f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip() or "System"
    doc = WorkspaceDoc(
        board_id=space_id,
        title=f"{space.name} — Overview Report ({datetime.utcnow().strftime('%Y-%m-%d')})",
        content_html=html_content,
        created_by_name=creator_name,
    )
    db.session.add(doc)
    db.session.commit()

    return jsonify({"doc_id": doc.id, "message": "Report generated successfully"}), 201


def _compute_card_value(card):
    """Helper to compute a card's aggregate value."""
    if not card.data_source_board_id:
        return 0

    group_ids = [g.id for g in BoardGroup.query.filter_by(board_id=card.data_source_board_id).all()]
    if not group_ids:
        return 0

    tasks = BoardTask.query.filter(BoardTask.group_id.in_(group_ids)).all()

    if card.calculation == 'count':
        return float(len(tasks))

    if not card.measure_field_id:
        return float(len(tasks))

    task_ids = [t.id for t in tasks]
    if not task_ids:
        return 0

    field_values = TaskCustomFieldValue.query.filter(
        TaskCustomFieldValue.task_id.in_(task_ids),
        TaskCustomFieldValue.field_id == card.measure_field_id
    ).all()

    numeric_values = []
    for fv in field_values:
        try:
            val = json.loads(fv.value_json)
            numeric_values.append(float(val))
        except (ValueError, TypeError, json.JSONDecodeError):
            continue

    if not numeric_values:
        return 0

    if card.calculation == 'sum':
        return sum(numeric_values)
    elif card.calculation == 'average':
        return sum(numeric_values) / len(numeric_values)
    elif card.calculation == 'min':
        return min(numeric_values)
    elif card.calculation == 'max':
        return max(numeric_values)

    return sum(numeric_values)
