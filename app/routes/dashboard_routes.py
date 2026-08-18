from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required
from app.models.staff_model import Staff
from app.models.department_model import Department
from app.models.lead_model import Lead
from app.models.activity_log_model import ActivityLog # Import ActivityLog

dashboard_bp = Blueprint('dashboard', __name__)

@dashboard_bp.route('/overview', methods=['GET'])
@jwt_required()
def get_overview_data():
    """
    Provides a summary of key metrics for the Super Admin dashboard.
    """
    try:
        total_staff = Staff.query.count()
        total_departments = Department.query.count()
        total_leads = Lead.query.count()
        
        # --- NEW: Fetch recent activities and leads ---
        recent_leads = Lead.query.order_by(Lead.created_at.desc()).limit(5).all()
        recent_activities = ActivityLog.query.order_by(ActivityLog.created_at.desc()).limit(5).all()
        
        # This will be implemented later
        total_students = 0

        data = {
            "total_staff": total_staff,
            "total_students": total_students,
            "total_departments": total_departments,
            "total_leads": total_leads,
            # Add new data to the response
            "recent_leads": [lead.to_dict() for lead in recent_leads],
            "recent_activities": [activity.to_dict() for activity in recent_activities]
        }

        return jsonify(data), 200
    except Exception as e:
        # It's good practice to log the actual error
        print(f"Error fetching dashboard data: {e}")
        return jsonify({"error": "An error occurred while fetching dashboard data."}), 500

@dashboard_bp.route('/reports', methods=['GET'])
@jwt_required()
def run_report():
    from datetime import datetime
    import json
    from flask import request
    from app.models.task_model import Task
    from app.models.board_model import BoardTask, TaskTimeEntry
    from app.models.department_model import Department
    from app.models.staff_model import Staff
    
    dept_id = request.args.get('department_id')
    board_id = request.args.get('board_id')
    status = request.args.get('status')
    priority = request.args.get('priority')
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    
    # Parse dates
    start_date = None
    end_date = None
    if start_date_str:
        try:
            start_date = datetime.fromisoformat(start_date_str.replace('Z', '+00:00'))
        except:
            pass
    if end_date_str:
        try:
            end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
        except:
            pass
            
    filtered_tasks = []
    
    # 1. Query Lead Tasks (Task)
    lead_query = Task.query
    if status:
        lead_query = lead_query.filter(Task.status == status)
    if start_date:
        lead_query = lead_query.filter(Task.created_at >= start_date)
    if end_date:
        lead_query = lead_query.filter(Task.created_at <= end_date)
    if dept_id:
        lead_query = lead_query.filter(Task.assigned_departments.any(id=int(dept_id)))
        
    lead_tasks = lead_query.all()
    for lt in lead_tasks:
        # Custom reports priority mapping
        if priority and priority != 'Normal':
            continue
        
        filtered_tasks.append({
            'id': f"L-{lt.id}",
            'title': lt.title,
            'note': lt.note or '',
            'status': lt.status,
            'priority': 'Normal',
            'due_date': lt.due_date.isoformat() if lt.due_date else None,
            'task_type': 'lead',
            'assigned_departments': [d.name for d in lt.assigned_departments],
            'assigned_staff': [s.name for s in lt.assigned_staff],
            'created_at': lt.created_at.isoformat() if lt.created_at else None,
            'billable_hours': 0.0
        })
        
    # 2. Query Board Tasks (BoardTask)
    board_query = BoardTask.query
    if status:
        board_query = board_query.filter(BoardTask.status == status)
    if priority:
        board_query = board_query.filter(BoardTask.priority == priority)
    if start_date:
        board_query = board_query.filter(BoardTask.created_at >= start_date)
    if end_date:
        board_query = board_query.filter(BoardTask.created_at <= end_date)
    if board_id:
        from app.models.board_model import Board, BoardGroup
        board_record = Board.get_by_id_or_public_id(board_id)
        if board_record:
            board_query = board_query.join(BoardGroup).filter(BoardGroup.board_id == board_record.id)
        else:
            board_query = board_query.filter(BoardTask.id == -1)
        
    board_tasks = board_query.all()
    for bt in board_tasks:
        bt_dict = bt.to_dict()
        
        # Check department filter (if specified)
        if dept_id:
            dept_id_int = int(dept_id)
            has_dept = False
            for assignee in bt.assignees:
                if assignee.staff:
                    if dept_id_int in [d.id for d in assignee.staff.departments]:
                        has_dept = True
                        break
            if not has_dept:
                continue
                
        # Calculate billable hours
        billable_sec = sum(entry.duration_seconds for entry in bt.time_entries if entry.is_billable)
        billable_hrs = round(billable_sec / 3600, 2)
        
        filtered_tasks.append({
            'id': f"B-{bt.id}",
            'title': bt.title,
            'note': bt.notes or '',
            'status': bt.status,
            'priority': bt.priority,
            'due_date': bt.due_date.isoformat() if bt.due_date else None,
            'task_type': 'board',
            'board_name': bt.group.board.name if bt.group and bt.group.board else 'Workspace',
            'assigned_departments': [],
            'assigned_staff': bt_dict.get('assignee_names') or [],
            'created_at': bt.created_at.isoformat() if bt.created_at else None,
            'billable_hours': billable_hrs
        })
        
    # Calculate aggregations
    total_tasks = len(filtered_tasks)
    completed_tasks = len([t for t in filtered_tasks if t['status'] in {'Completed', 'Done'}])
    completion_rate = round((completed_tasks / total_tasks * 100), 1) if total_tasks > 0 else 0.0
    total_billable_hours = round(sum(t['billable_hours'] for t in filtered_tasks), 2)
    
    return jsonify({
        'tasks': filtered_tasks,
        'summary': {
            'total_tasks': total_tasks,
            'completed_tasks': completed_tasks,
            'completion_rate': completion_rate,
            'total_billable_hours': total_billable_hours
        }
    }), 200