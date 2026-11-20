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


def _resolve_space(space_id):
    """Resolve space board by either public_id (string/hex) or integer id."""
    if not space_id:
        return None
    return Board.get_by_id_or_public_id(space_id)


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

@overview_bp.route('/boards/<string:space_id>/overview-cards', methods=['GET', 'OPTIONS'])
@jwt_required(optional=True)
def get_overview_cards(space_id):
    """List all overview cards for a space."""
    if request.method == 'OPTIONS':
        return jsonify({}), 200

    space = _resolve_space(space_id)
    if not space:
        return jsonify({"error": "Space not found"}), 404

    cards = OverviewCard.query.filter_by(board_id=space.id).order_by(OverviewCard.position).all()
    return jsonify([c.to_dict() for c in cards])


@overview_bp.route('/boards/<string:space_id>/overview-cards', methods=['POST', 'OPTIONS'])
@jwt_required(optional=True)
def create_overview_card(space_id):
    """Create a new overview card."""
    if request.method == 'OPTIONS':
        return jsonify({}), 200

    space = _resolve_space(space_id)
    if not space:
        return jsonify({"error": "Space not found"}), 404

    user, role, staff_id, admin_id = _get_user_ids()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json()
    if not data or not data.get('name') or not data.get('card_type'):
        return jsonify({"error": "name and card_type are required"}), 400

    # Position: append at the end
    max_pos = db.session.query(func.max(OverviewCard.position)).filter_by(board_id=space.id).scalar() or 0

    data_source_id = None
    if data.get('data_source_board_id'):
        src_board = Board.get_by_id_or_public_id(data['data_source_board_id'])
        data_source_id = src_board.id if src_board else None

    measure_id = int(data['measure_field_id']) if data.get('measure_field_id') else None

    card = OverviewCard(
        board_id=space.id,
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


@overview_bp.route('/boards/<string:space_id>/overview-cards/<int:card_id>', methods=['PUT', 'OPTIONS'])
@jwt_required(optional=True)
def update_overview_card(space_id, card_id):
    """Update an overview card's settings."""
    if request.method == 'OPTIONS':
        return jsonify({}), 200

    space = _resolve_space(space_id)
    if not space:
        return jsonify({"error": "Space not found"}), 404

    card = OverviewCard.query.filter_by(id=card_id, board_id=space.id).first()
    if not card:
        return jsonify({"error": "Card not found"}), 404

    data = request.get_json()
    if 'name' in data and data['name']:
        card.name = data['name']
    if 'data_source_board_id' in data:
        if data['data_source_board_id']:
            src_board = Board.get_by_id_or_public_id(data['data_source_board_id'])
            card.data_source_board_id = src_board.id if src_board else None
        else:
            card.data_source_board_id = None
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


@overview_bp.route('/boards/<string:space_id>/overview-cards/<int:card_id>', methods=['DELETE', 'OPTIONS'])
@jwt_required(optional=True)
def delete_overview_card(space_id, card_id):
    """Delete an overview card."""
    if request.method == 'OPTIONS':
        return jsonify({}), 200

    space = _resolve_space(space_id)
    if not space:
        return jsonify({"error": "Space not found"}), 404

    card = OverviewCard.query.filter_by(id=card_id, board_id=space.id).first()
    if not card:
        return jsonify({"error": "Card not found"}), 404
    db.session.delete(card)
    db.session.commit()
    return jsonify({"message": "Card deleted"}), 200


# ─── Card Aggregation ─────────────────────────────────────────────────────────

def _collect_card_tasks(space, data_source_board_id, filters):
    """Collect tasks for an overview card from a specific list or all lists in the space."""
    if data_source_board_id:
        board_ids = [data_source_board_id]
    else:
        board_ids = _get_all_descendant_list_ids(space.id)
        if space.id not in board_ids:
            board_ids.append(space.id)

    if not board_ids:
        return []

    group_ids = [g.id for g in BoardGroup.query.filter(BoardGroup.board_id.in_(board_ids)).all()]
    if not group_ids:
        return []

    task_query = BoardTask.query.filter(BoardTask.group_id.in_(group_ids))
    show_closed = bool(filters.get('show_closed', filters.get('showClosed', False)))
    status_filter = filters.get('status_filter') or ''
    priority_filter = filters.get('priority_filter') or ''

    if status_filter == 'completed':
        task_query = task_query.filter(BoardTask.status == 'Done')
    elif status_filter and status_filter not in ('all', 'open'):
        task_query = task_query.filter(BoardTask.status == status_filter)
    elif status_filter == 'open' or not show_closed:
        if status_filter != 'all':
            task_query = task_query.filter(BoardTask.status != 'Done')

    if priority_filter and priority_filter != 'all':
        task_query = task_query.filter(BoardTask.priority == priority_filter)

    return task_query.order_by(BoardTask.created_at.desc()).all()


def _extract_numeric_values(tasks, measure_field_id):
    import re
    if not tasks or not measure_field_id:
        return [], {}
    task_ids = [t.id for t in tasks]
    field_values = TaskCustomFieldValue.query.filter(
        TaskCustomFieldValue.task_id.in_(task_ids),
        TaskCustomFieldValue.field_id == measure_field_id
    ).all()
    numeric_values = []
    by_task = {}
    for fv in field_values:
        if not fv.value_json:
            continue
        raw_val = fv.value_json
        try:
            raw_val = json.loads(fv.value_json)
        except Exception:
            pass
        by_task[fv.task_id] = raw_val
        if isinstance(raw_val, (int, float)):
            numeric_values.append(float(raw_val))
        elif isinstance(raw_val, str):
            cleaned = re.sub(r'[^\d.-]', '', raw_val)
            if cleaned and cleaned not in ('.', '-'):
                try:
                    numeric_values.append(float(cleaned))
                except ValueError:
                    pass
    return numeric_values, by_task


@overview_bp.route('/boards/<string:space_id>/overview-cards/<int:card_id>/aggregate', methods=['GET', 'OPTIONS'])
@jwt_required(optional=True)
def get_card_aggregate(space_id, card_id):
    """Compute a card's aggregated value or breakdown (supports live preview query params)."""
    if request.method == 'OPTIONS':
        return jsonify({}), 200

    space = _resolve_space(space_id)
    if not space:
        return jsonify({"error": "Space not found"}), 404

    card = OverviewCard.query.filter_by(id=card_id, board_id=space.id).first()
    if not card:
        return jsonify({"error": "Card not found"}), 404

    filters = {}
    if card.filters_json:
        try:
            parsed = json.loads(card.filters_json)
            if isinstance(parsed, dict):
                filters = parsed
        except Exception:
            pass

    # Support live preview overrides from CardSettingsModal query params
    src_board_id = card.data_source_board_id
    if 'data_source_board_id' in request.args:
        raw_src = request.args.get('data_source_board_id')
        if raw_src in ('', 'all', 'null', 'None'):
            src_board_id = None
        else:
            b = Board.get_by_id_or_public_id(raw_src)
            src_board_id = b.id if b else None

    measure_field_id = card.measure_field_id
    if 'measure_field_id' in request.args:
        raw_mf = request.args.get('measure_field_id')
        measure_field_id = int(raw_mf) if raw_mf and raw_mf.isdigit() else None

    calc_type = (request.args.get('calculation') or card.calculation or 'sum').lower()
    if 'show_closed' in request.args:
        filters['show_closed'] = request.args.get('show_closed', '').lower() == 'true'
    if 'status_filter' in request.args:
        filters['status_filter'] = request.args.get('status_filter')
    if 'priority_filter' in request.args:
        filters['priority_filter'] = request.args.get('priority_filter')

    if card.card_type == 'notes':
        return jsonify({
            "note_content": filters.get('note_content', ''),
            "refreshed_at": datetime.utcnow().isoformat() + 'Z'
        })

    tasks = _collect_card_tasks(space, src_board_id, filters)
    now_iso = datetime.utcnow().isoformat() + 'Z'

    if card.card_type == 'status_breakdown':
        counts = {}
        for t in tasks:
            st = t.status or 'Not Started'
            counts[st] = counts.get(st, 0) + 1
        total = len(tasks)
        colors = {'Done': '#10b981', 'In Progress': '#2563eb', 'Not Started': '#94a3b8'}
        breakdown = [
            {
                'label': k,
                'count': v,
                'pct': round((v / total) * 100, 1) if total else 0,
                'color': colors.get(k, '#6366f1')
            }
            for k, v in sorted(counts.items(), key=lambda x: -x[1])
        ]
        return jsonify({"breakdown": breakdown, "total": total, "refreshed_at": now_iso})

    if card.card_type == 'priority_breakdown':
        order = ['Urgent', 'High', 'Normal', 'Low']
        colors = {'Urgent': '#ef4444', 'High': '#f59e0b', 'Normal': '#2563eb', 'Low': '#94a3b8'}
        counts = {p: 0 for p in order}
        for t in tasks:
            p = t.priority or 'Normal'
            counts[p] = counts.get(p, 0) + 1
        total = len(tasks)
        breakdown = [
            {
                'label': k,
                'count': counts[k],
                'pct': round((counts[k] / total) * 100, 1) if total else 0,
                'color': colors.get(k, '#6366f1')
            }
            for k in order if counts.get(k, 0) > 0
        ]
        return jsonify({"breakdown": breakdown, "total": total, "refreshed_at": now_iso})

    if card.card_type == 'assignee_workload':
        counts = {}
        for t in tasks:
            name = (
                t.responsible_staff.name if t.responsible_staff
                else (t.responsible_super_admin.name if t.responsible_super_admin else 'Unassigned')
            )
            counts[name] = counts.get(name, 0) + 1
        total = len(tasks)
        breakdown = [
            {
                'label': k,
                'count': v,
                'pct': round((v / total) * 100, 1) if total else 0,
                'color': '#2563eb'
            }
            for k, v in sorted(counts.items(), key=lambda x: -x[1])[:10]
        ]
        return jsonify({"breakdown": breakdown, "total": total, "refreshed_at": now_iso})

    if card.card_type == 'task_list':
        numeric_values, by_task = _extract_numeric_values(tasks, measure_field_id)
        measure_field_name = None
        if measure_field_id:
            fld = BoardCustomField.query.get(measure_field_id)
            if fld:
                measure_field_name = fld.name
        rows = []
        for t in tasks[:25]:
            board = Board.query.get(t.group.board_id) if t.group else None
            rows.append({
                'id': t.id,
                'title': t.title,
                'status': t.status,
                'priority': t.priority,
                'due_date': t.due_date.isoformat() if t.due_date else None,
                'board_id': (board.public_id or str(board.id)) if board else None,
                'board_name': board.name if board else '',
                'measure_value': by_task.get(t.id),
            })
        return jsonify({
            "tasks": rows,
            "total": len(tasks),
            "sum_value": round(sum(numeric_values), 2) if numeric_values else 0,
            "measure_field_name": measure_field_name,
            "refreshed_at": now_iso
        })

    # Default: calculation card (sum / average / min / max / count)
    if calc_type == 'count':
        value = len(tasks)
    elif measure_field_id:
        numeric_values, _ = _extract_numeric_values(tasks, measure_field_id)
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

    if isinstance(value, float):
        value = round(value, 2)

    target_budget = filters.get('target_budget')
    try:
        target_budget = float(target_budget) if target_budget not in (None, '') else None
    except (ValueError, TypeError):
        target_budget = None

    return jsonify({
        "value": value,
        "count": len(tasks),
        "target_budget": target_budget,
        "refreshed_at": now_iso
    })


@overview_bp.route('/boards/<string:space_id>/overview-cards/<int:card_id>/data', methods=['GET', 'OPTIONS'])
@jwt_required(optional=True)
def get_card_data(space_id, card_id):
    """Return raw task rows that feed a calculation card."""
    if request.method == 'OPTIONS':
        return jsonify({}), 200

    space = _resolve_space(space_id)
    if not space:
        return jsonify({"error": "Space not found"}), 404

    card = OverviewCard.query.filter_by(id=card_id, board_id=space.id).first()
    if not card or card.card_type != 'calculation' or not card.data_source_board_id:
        return jsonify({"tasks": [], "total": 0})

    filters = {}
    if card.filters_json:
        try:
            parsed = json.loads(card.filters_json)
            if isinstance(parsed, dict):
                filters = parsed
        except Exception:
            pass

    group_ids = [g.id for g in BoardGroup.query.filter_by(board_id=card.data_source_board_id).all()]
    if not group_ids:
        return jsonify({"tasks": [], "total": 0})

    task_query = BoardTask.query.filter(BoardTask.group_id.in_(group_ids))
    if not filters.get('show_closed', False):
        task_query = task_query.filter(BoardTask.status != 'Done')

    tasks = task_query.order_by(BoardTask.created_at.desc()).all()

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

@overview_bp.route('/boards/<string:space_id>/overview/children', methods=['GET', 'OPTIONS'])
@jwt_required(optional=True)
def get_space_children(space_id):
    """List all child folders and lists for the space."""
    if request.method == 'OPTIONS':
        return jsonify({}), 200

    space = _resolve_space(space_id)
    if not space:
        return jsonify({"error": "Space not found"}), 404

    children = Board.query.filter_by(parent_id=space.id, is_archived=False).order_by(Board.name).all()

    folders = []
    lists = []
    for child in children:
        item = {
            'id': child.public_id or str(child.id),
            'internal_id': child.id,
            'name': child.name,
            'is_folder': child.is_folder,
            'is_private': child.is_private,
            'color': child.color,
            'icon': child.icon,
            'tasks_count': sum(len(g.tasks) for g in child.groups) if not child.is_folder else 0,
        }
        if child.is_folder:
            sub_lists = Board.query.filter_by(parent_id=child.id, is_archived=False).all()
            item['children'] = [{
                'id': sl.public_id or str(sl.id),
                'internal_id': sl.id,
                'name': sl.name,
                'is_private': sl.is_private,
                'color': sl.color,
                'tasks_count': sum(len(g.tasks) for g in sl.groups)
            } for sl in sub_lists]
            folders.append(item)
        else:
            lists.append(item)

    return jsonify({"folders": folders, "lists": lists})


@overview_bp.route('/boards/<string:space_id>/overview/recent', methods=['GET', 'OPTIONS'])
@jwt_required(optional=True)
def get_space_recent(space_id):
    """Return recently visited/accessed lists, folders, tasks, and docs under the space."""
    if request.method == 'OPTIONS':
        return jsonify({}), 200

    space = _resolve_space(space_id)
    if not space:
        return jsonify({"error": "Space not found"}), 404

    # 1. Collect all folders and lists under this space to show in Recent Card
    space_items = []
    child_folders = Board.query.filter_by(parent_id=space.id, is_folder=True, is_archived=False).order_by(Board.created_at.desc()).all()
    child_lists = Board.query.filter_by(parent_id=space.id, is_folder=False, is_archived=False).order_by(Board.created_at.desc()).all()

    for f in child_folders:
        space_items.append({
            'id': f.public_id or str(f.id),
            'internal_id': f.id,
            'name': f.name,
            'type': 'folder',
            'is_folder': True,
            'is_private': f.is_private,
            'parent_name': space.name,
            'color': f.color,
            'icon': f.icon,
        })
        sub_lists = Board.query.filter_by(parent_id=f.id, is_folder=False, is_archived=False).order_by(Board.created_at.desc()).all()
        for sl in sub_lists:
            space_items.append({
                'id': sl.public_id or str(sl.id),
                'internal_id': sl.id,
                'name': sl.name,
                'type': 'list',
                'is_folder': False,
                'is_private': sl.is_private,
                'parent_name': f.name,
                'color': sl.color,
                'icon': sl.icon,
            })

    for l in child_lists:
        space_items.append({
            'id': l.public_id or str(l.id),
            'internal_id': l.id,
            'name': l.name,
            'type': 'list',
            'is_folder': False,
            'is_private': l.is_private,
            'parent_name': space.name,
            'color': l.color,
            'icon': l.icon,
        })

    # 2. Collect descendant list IDs for tasks and docs
    list_ids = _get_all_descendant_list_ids(space.id)
    list_ids.append(space.id)

    # 3. Recent tasks
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
                'board_id': (board.public_id or str(board.id)) if board else None,
                'created_at': t.created_at.isoformat() + 'Z',
            })

    # 4. Recent docs
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
                'board_id': (board.public_id or str(board.id)) if board else None,
                'updated_at': d.updated_at.isoformat() + 'Z',
            })
    except Exception:
        pass

    return jsonify({
        "items": space_items,
        "tasks": recent_tasks,
        "docs": recent_docs
    })


@overview_bp.route('/boards/<string:space_id>/overview/docs', methods=['GET', 'OPTIONS'])
@jwt_required(optional=True)
def get_space_docs(space_id):
    """Return all docs in the space."""
    if request.method == 'OPTIONS':
        return jsonify({}), 200

    space = _resolve_space(space_id)
    if not space:
        return jsonify({"error": "Space not found"}), 404

    list_ids = _get_all_descendant_list_ids(space.id)
    list_ids.append(space.id)

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

@overview_bp.route('/boards/<string:space_id>/overview/bookmarks', methods=['GET', 'OPTIONS'])
@jwt_required(optional=True)
def get_bookmarks(space_id):
    """Get the current user's bookmarks for this space."""
    if request.method == 'OPTIONS':
        return jsonify({}), 200

    space = _resolve_space(space_id)
    if not space:
        return jsonify({"error": "Space not found"}), 404

    user, role, staff_id, admin_id = _get_user_ids()
    if not user:
        return jsonify([])

    query = SpaceBookmark.query.filter_by(board_id=space.id)
    if staff_id:
        query = query.filter_by(staff_id=staff_id)
    else:
        query = query.filter_by(super_admin_id=admin_id)

    bookmarks = query.order_by(SpaceBookmark.position).all()
    return jsonify([b.to_dict() for b in bookmarks])


@overview_bp.route('/boards/<string:space_id>/overview/bookmarks', methods=['POST', 'OPTIONS'])
@jwt_required(optional=True)
def create_bookmark(space_id):
    """Add a bookmark for the current user."""
    if request.method == 'OPTIONS':
        return jsonify({}), 200

    space = _resolve_space(space_id)
    if not space:
        return jsonify({"error": "Space not found"}), 404

    user, role, staff_id, admin_id = _get_user_ids()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json()
    if not data or not data.get('title'):
        return jsonify({"error": "title is required"}), 400

    max_pos = db.session.query(func.max(SpaceBookmark.position)).filter_by(board_id=space.id).scalar() or 0

    bookmark = SpaceBookmark(
        board_id=space.id,
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


@overview_bp.route('/boards/<string:space_id>/overview/bookmarks/<int:bookmark_id>', methods=['DELETE', 'OPTIONS'])
@jwt_required(optional=True)
def delete_bookmark(space_id, bookmark_id):
    """Delete a bookmark."""
    if request.method == 'OPTIONS':
        return jsonify({}), 200

    space = _resolve_space(space_id)
    if not space:
        return jsonify({"error": "Space not found"}), 404

    user, role, staff_id, admin_id = _get_user_ids()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    query = SpaceBookmark.query.filter_by(id=bookmark_id, board_id=space.id)
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

@overview_bp.route('/boards/<string:space_id>/overview/generate-report', methods=['POST', 'OPTIONS'])
@jwt_required(optional=True)
def generate_report(space_id):
    """Generate a formatted report doc from overview card data."""
    if request.method == 'OPTIONS':
        return jsonify({}), 200

    space = _resolve_space(space_id)
    if not space:
        return jsonify({"error": "Space not found"}), 404

    user, role, staff_id, admin_id = _get_user_ids()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    cards = OverviewCard.query.filter_by(board_id=space.id).order_by(OverviewCard.position).all()

    now = datetime.utcnow().strftime('%B %d, %Y at %I:%M %p')
    html_parts = [
        f'<h1>{space.name} — Overview Report</h1>',
        f'<p style="color: #666;">Generated on {now}</p>',
        '<hr/>',
    ]

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

    for card in cards:
        if card.card_type != 'calculation' or not card.data_source_board_id:
            continue

        html_parts.append(f'<h3>{card.name}</h3>')
        source_board = Board.query.get(card.data_source_board_id)
        if source_board:
            html_parts.append(f'<p style="color:#666;">Data source: {source_board.name}</p>')

        group_ids = [g.id for g in BoardGroup.query.filter_by(board_id=card.data_source_board_id).all()]
        if not group_ids:
            html_parts.append('<p><em>No data</em></p>')
            continue

        tasks = BoardTask.query.filter(BoardTask.group_id.in_(group_ids)).order_by(BoardTask.created_at.desc()).limit(100).all()

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
        board_id=space.id,
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
