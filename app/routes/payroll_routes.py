from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime, date, timedelta
import json
import re
import uuid

from app.models import db
from app.models.staff_model import Staff
from app.models.department_model import Department
from app.models.super_admin_model import SuperAdmin
from app.models.activity_log_model import log_activity
from app.models.payroll_models import (
    TimeTrackingSettings,
    StaffTimeTrackingAssignment,
    TimeEntry,
    PayrollRun,
    PayrollPaymentItem,
    HRDocument,
    StaffDocumentAssignment,
    PerformanceGoal,
    PerformanceReview,
)

payroll_bp = Blueprint('payroll', __name__)


def _is_hr_or_admin_dept(name):
    if not name:
        return False
    clean = name.strip().lower()
    return bool(re.search(r'\b(it|information technology|info tech|tech|accounting|admin|administration|human resource|hr|payroll)\b', clean))


def _get_admin_actor():
    email = get_jwt_identity()
    admin = SuperAdmin.query.filter_by(email=email).first()
    if admin:
        return admin
    staff = Staff.query.filter_by(email=email).first()
    if staff and getattr(staff, 'is_active', True):
        for dept in staff.departments:
            if _is_hr_or_admin_dept(dept.name):
                return staff
    return None


def _get_current_user():
    email = get_jwt_identity()
    staff = Staff.query.filter_by(email=email).first()
    if staff:
        return staff, 'staff'
    admin = SuperAdmin.query.filter_by(email=email).first()
    if admin:
        # Auto-provision a Staff profile for SuperAdmin so they can log hours,
        # set timezone, sign documents, and use /time-tracking identically to staff.
        staff = Staff(
            name=getattr(admin, 'name', None) or email.split('@')[0],
            email=admin.email,
            work_email=admin.email,
            personal_email=admin.email,
            role='staff',
            job_title='Administrator',
            worker_type='Employee',
            worker_status='Active',
            is_active=True,
        )
        staff.set_password(uuid.uuid4().hex)
        db.session.add(staff)
        db.session.commit()
        return staff, 'staff'
    return None, None


def _get_or_create_settings():
    settings = TimeTrackingSettings.query.first()
    if not settings:
        settings = TimeTrackingSettings(
            workweek_start='Sunday',
            allowed_days=json.dumps(['Mon', 'Tue', 'Wed', 'Thu', 'Fri']),
            require_whole_hours=False,
            overtime_weekly_threshold=40.0,
            non_us_tracking_enabled=True,
            next_invoice_number=72,
            bank_account_label='Cooper Galvis Checking •••• 4821',
        )
        db.session.add(settings)
        db.session.commit()
    return settings


def _seed_default_documents_if_empty():
    if HRDocument.query.count() > 0:
        return
    defaults = [
        HRDocument(
            title='Taxpayer Identification (Form W-9)',
            category='Tax Documents',
            description='Request for Taxpayer Identification Number and Certification for US independent contractors.',
            doc_type='shared',
            status='active',
            requires_signature=True,
            is_fillable=True,
            fillable_fields=json.dumps([
                {'key': 'legal_name', 'label': 'Full Legal Name', 'type': 'text', 'required': True},
                {'key': 'tax_classification', 'label': 'Federal Tax Classification', 'type': 'select', 'options': ['Individual/sole proprietor', 'Single-member LLC', 'C Corporation', 'S Corporation', 'Partnership'], 'required': True},
                {'key': 'tin_last4', 'label': 'SSN or EIN (Last 4 digits)', 'type': 'text', 'required': True},
                {'key': 'address', 'label': 'Mailing Address', 'type': 'text', 'required': True},
            ]),
            future_hires_rule='us',
            uploaded_by='Cooper Galvis Enterprises LLC',
        ),
        HRDocument(
            title='Alternate Authorized Payment Form',
            category='Payroll & Payment',
            description='Submit your preferred international or alternate payout method (Remitly, Brebe, Wise, Direct Bank Draft, or ACH).',
            doc_type='shared',
            status='active',
            requires_signature=True,
            is_fillable=True,
            fillable_fields=json.dumps([
                {'key': 'payout_platform', 'label': 'Payment Platform / Method', 'type': 'select', 'options': ['Remitly', 'Brebe (Colombia)', 'Wise / Bank Draft', 'US Direct Deposit (ACH)'], 'required': True},
                {'key': 'account_holder', 'label': 'Account Holder Full Name', 'type': 'text', 'required': True},
                {'key': 'bank_or_wallet', 'label': 'Bank Name or Mobile Wallet / Phone', 'type': 'text', 'required': True},
                {'key': 'account_or_routing', 'label': 'Account Number / Routing / IBAN', 'type': 'text', 'required': True},
                {'key': 'country', 'label': 'Recipient Country', 'type': 'text', 'required': True},
            ]),
            future_hires_rule='all',
            uploaded_by='Cooper Galvis Enterprises LLC',
        ),
        HRDocument(
            title='2026 Pay Schedule',
            category='Pay Schedules',
            description='Bi-weekly pay periods, timesheet submission deadlines, and Friday pay dates for US contractors and employees (2026).',
            doc_type='shared',
            status='active',
            requires_signature=True,
            is_fillable=False,
            content_body='2026 Pay Schedule (US Contractors & Employees)\n\nPay Periods run Sunday through Saturday (2-week cycles). Timesheets must be submitted by Monday 10:00 AM following the close of the pay period. Payroll is finalized on Wednesday/Thursday and disbursed every other Friday.',
            future_hires_rule='us',
            uploaded_by='Cooper Galvis Enterprises LLC',
        ),
        HRDocument(
            title='2026 Pay Schedule International',
            category='Pay Schedules',
            description='Bi-weekly pay periods and international Remitly / bank transfer disbursement schedule for non-US contractors (2026).',
            doc_type='shared',
            status='active',
            requires_signature=True,
            is_fillable=False,
            content_body='2026 International Pay Schedule\n\nInternational contractors log hours on the same 2-week cycle. Payments are processed via Remitly, Brebe, or direct bank draft following timesheet verification.',
            future_hires_rule='non_us',
            uploaded_by='Cooper Galvis Enterprises LLC',
        ),
        HRDocument(
            title='ELA Academy Employee Handbook',
            category='Company Policies',
            description='Staff standards of conduct, class time-logging expectations, and school operating procedures.',
            doc_type='shared',
            status='active',
            requires_signature=True,
            is_fillable=False,
            content_body='ELA Academy Employee & Contractor Handbook\n\n1. Time Tracking: All teachers must log whole-hour blocks per class taught with the class name and grade level in the shift note.\n2. Workdays: Standard instructional days are Monday through Friday.\n3. Communication: Notify your department supervisor at least 24 hours before any schedule change.',
            future_hires_rule='all',
            uploaded_by='Cooper Galvis Enterprises LLC',
        ),
        HRDocument(
            title='ELA Academy Student Handbook',
            category='Company Policies',
            description='School-wide student expectations and classroom guidelines for instructors.',
            doc_type='shared',
            status='active',
            requires_signature=True,
            is_fillable=False,
            future_hires_rule='all',
            uploaded_by='Cooper Galvis Enterprises LLC',
        ),
    ]
    for doc in defaults:
        db.session.add(doc)
    db.session.commit()


def _parse_hhmm_to_minutes(hhmm):
    parts = hhmm.strip().split(':')
    if len(parts) != 2:
        raise ValueError("Time must be in HH:MM format")
    h, m = int(parts[0]), int(parts[1])
    if h < 0 or h > 23 or m < 0 or m > 59:
        raise ValueError("Invalid time value")
    return h * 60 + m


def _compute_duration_minutes(start_time, end_time):
    s = _parse_hhmm_to_minutes(start_time)
    e = _parse_hhmm_to_minutes(end_time)
    if e <= s:
        raise ValueError("End time must be after start time")
    return e - s


def _parse_date(date_str):
    if not date_str:
        return None
    return datetime.strptime(date_str[:10], '%Y-%m-%d').date()


def _get_or_create_assignment(staff_id):
    assign = StaffTimeTrackingAssignment.query.filter_by(staff_id=staff_id).first()
    if not assign:
        assign = StaffTimeTrackingAssignment(staff_id=staff_id, is_assigned=True)
        db.session.add(assign)
        db.session.commit()
    return assign


def _create_staff_notification(staff_id, title, message):
    try:
        from app.models.notification_model import Notification
        notif = Notification(
            recipient_staff_id=staff_id,
            title=title,
            message=message,
            is_read=False,
        )
        db.session.add(notif)
    except Exception:
        pass


# ==============================================================================
# 1. EMPLOYEE / CONTRACTOR PORTAL (/api/payroll/my/*)
# ==============================================================================

@payroll_bp.route('/my/time-entries', methods=['GET'])
@jwt_required()
def get_my_time_entries():
    user, role = _get_current_user()
    if not user:
        return jsonify({'error': 'Unauthorized'}), 401

    settings = _get_or_create_settings()
    week_start_str = request.args.get('week_start')
    today = date.today()
    if week_start_str:
        week_start = _parse_date(week_start_str)
    else:
        # Sunday-start current week
        days_since_sunday = (today.weekday() + 1) % 7
        week_start = today - timedelta(days=days_since_sunday)
    week_end = week_start + timedelta(days=6)

    assign = _get_or_create_assignment(user.id)
    entries = TimeEntry.query.filter(
        TimeEntry.staff_id == user.id,
        TimeEntry.work_date >= week_start,
        TimeEntry.work_date <= week_end,
    ).order_by(TimeEntry.work_date.asc(), TimeEntry.start_time.asc()).all()

    is_locked = any(e.status in ('synced', 'paid') for e in entries)

    # Build last 12 weeks summary for the week selector dropdown
    days_since_sunday = (today.weekday() + 1) % 7
    current_sunday = today - timedelta(days=days_since_sunday)
    weeks_summary = []
    all_recent = TimeEntry.query.filter(
        TimeEntry.staff_id == user.id,
        TimeEntry.work_date >= current_sunday - timedelta(weeks=15),
    ).all()
    for i in range(12):
        ws = current_sunday - timedelta(weeks=i)
        we = ws + timedelta(days=6)
        w_entries = [e for e in all_recent if ws <= e.work_date <= we]
        total_mins = sum(e.duration_minutes for e in w_entries)
        locked = any(e.status in ('synced', 'paid') for e in w_entries)
        paid = any(e.status == 'paid' for e in w_entries)
        weeks_summary.append({
            'week_start': ws.isoformat(),
            'week_end': we.isoformat(),
            'is_current': i == 0,
            'total_minutes': total_mins,
            'is_locked': locked,
            'is_paid': paid,
        })

    return jsonify({
        'week_start': week_start.isoformat(),
        'week_end': week_end.isoformat(),
        'entries': [e.to_dict() for e in entries],
        'is_locked': is_locked,
        'timezone': assign.timezone,
        'is_assigned': assign.is_assigned,
        'settings': settings.to_dict(),
        'weeks_summary': weeks_summary,
    }), 200


@payroll_bp.route('/my/time-entries', methods=['POST'])
@jwt_required()
def create_my_time_entry():
    user, role = _get_current_user()
    if not user or role != 'staff':
        return jsonify({'error': 'Staff account required to log hours'}), 403

    settings = _get_or_create_settings()
    assign = _get_or_create_assignment(user.id)
    if not assign.is_assigned:
        return jsonify({'error': 'Time tracking is not enabled for your account. Contact HR/Admin.'}), 403

    data = request.get_json(silent=True) or {}
    try:
        work_date = _parse_date(data.get('work_date'))
        if not work_date:
            return jsonify({'error': 'work_date is required (YYYY-MM-DD)'}), 400
        start_time = data.get('start_time', '').strip()
        end_time = data.get('end_time', '').strip()
        duration_minutes = _compute_duration_minutes(start_time, end_time)
    except ValueError as err:
        return jsonify({'error': str(err)}), 400

    # Check allowed days (Mon-Fri enforcement from transcript)
    day_abbrev = work_date.strftime('%a')  # Mon, Tue, ..., Sun
    allowed_days = settings.to_dict()['allowed_days']
    if allowed_days and day_abbrev not in allowed_days:
        return jsonify({'error': f'Hours cannot be logged on {work_date.strftime("%A")}. Allowed workdays: {", ".join(allowed_days)}.'}), 400

    # Check if week is already synced/paid
    days_since_sunday = (work_date.weekday() + 1) % 7
    ws = work_date - timedelta(days=days_since_sunday)
    we = ws + timedelta(days=6)
    locked_exists = TimeEntry.query.filter(
        TimeEntry.staff_id == user.id,
        TimeEntry.work_date >= ws,
        TimeEntry.work_date <= we,
        TimeEntry.status.in_(['synced', 'paid']),
    ).first()
    if locked_exists:
        return jsonify({'error': 'This timesheet has already been synced/paid and cannot be edited.'}), 409

    # Check overlapping shifts on same day
    new_s = _parse_hhmm_to_minutes(start_time)
    new_e = _parse_hhmm_to_minutes(end_time)
    same_day = TimeEntry.query.filter_by(staff_id=user.id, work_date=work_date).all()
    for existing in same_day:
        ex_s = _parse_hhmm_to_minutes(existing.start_time)
        ex_e = _parse_hhmm_to_minutes(existing.end_time)
        if max(new_s, ex_s) < min(new_e, ex_e):
            return jsonify({'error': f'Shift overlaps with existing entry ({existing.start_time} - {existing.end_time}).'}), 409

    entry = TimeEntry(
        staff_id=user.id,
        work_date=work_date,
        start_time=start_time,
        end_time=end_time,
        duration_minutes=duration_minutes,
        note=(data.get('note') or '').strip(),
        timezone=data.get('timezone') or assign.timezone,
        status='draft',
    )
    db.session.add(entry)
    db.session.commit()
    return jsonify(entry.to_dict()), 201


@payroll_bp.route('/my/time-entries/<int:entry_id>', methods=['PUT'])
@jwt_required()
def update_my_time_entry(entry_id):
    user, role = _get_current_user()
    if not user or role != 'staff':
        return jsonify({'error': 'Unauthorized'}), 403

    entry = TimeEntry.query.filter_by(id=entry_id, staff_id=user.id).first_or_404()
    if entry.status in ('synced', 'paid'):
        return jsonify({'error': 'This timesheet has already been synced/paid and cannot be edited.'}), 409

    data = request.get_json(silent=True) or {}
    start_time = data.get('start_time', entry.start_time)
    end_time = data.get('end_time', entry.end_time)
    try:
        duration_minutes = _compute_duration_minutes(start_time, end_time)
    except ValueError as err:
        return jsonify({'error': str(err)}), 400

    entry.start_time = start_time
    entry.end_time = end_time
    entry.duration_minutes = duration_minutes
    if 'note' in data:
        entry.note = (data.get('note') or '').strip()
    db.session.commit()
    return jsonify(entry.to_dict()), 200


@payroll_bp.route('/my/time-entries/<int:entry_id>', methods=['DELETE'])
@jwt_required()
def delete_my_time_entry(entry_id):
    user, role = _get_current_user()
    if not user or role != 'staff':
        return jsonify({'error': 'Unauthorized'}), 403

    entry = TimeEntry.query.filter_by(id=entry_id, staff_id=user.id).first_or_404()
    if entry.status in ('synced', 'paid'):
        return jsonify({'error': 'This timesheet has already been synced/paid and cannot be deleted.'}), 409

    db.session.delete(entry)
    db.session.commit()
    return jsonify({'message': 'Deleted'}), 200


@payroll_bp.route('/my/timezone', methods=['PUT'])
@jwt_required()
def update_my_timezone():
    user, role = _get_current_user()
    if not user or role != 'staff':
        return jsonify({'error': 'Unauthorized'}), 403
    data = request.get_json(silent=True) or {}
    tz = data.get('timezone')
    if not tz:
        return jsonify({'error': 'timezone is required'}), 400
    assign = _get_or_create_assignment(user.id)
    assign.timezone = tz
    db.session.commit()
    return jsonify(assign.to_dict()), 200


@payroll_bp.route('/my/pay-history', methods=['GET'])
@jwt_required()
def get_my_pay_history():
    user, role = _get_current_user()
    if not user:
        return jsonify({'error': 'Unauthorized'}), 401
    if role != 'staff':
        return jsonify({'payments': []}), 200
    items = PayrollPaymentItem.query.filter_by(staff_id=user.id).order_by(PayrollPaymentItem.paid_at.desc()).all()
    return jsonify({'payments': [i.to_dict() for i in items]}), 200


@payroll_bp.route('/my/documents', methods=['GET'])
@jwt_required()
def get_my_documents():
    user, role = _get_current_user()
    if not user:
        return jsonify({'error': 'Unauthorized'}), 401
    _seed_default_documents_if_empty()
    if role != 'staff':
        return jsonify({'documents': []}), 200
    assignments = StaffDocumentAssignment.query.filter_by(staff_id=user.id).order_by(StaffDocumentAssignment.assigned_at.desc()).all()
    return jsonify({'documents': [a.to_dict() for a in assignments]}), 200


@payroll_bp.route('/my/documents/<int:assignment_id>/sign', methods=['POST'])
@jwt_required()
def sign_my_document(assignment_id):
    user, role = _get_current_user()
    if not user or role != 'staff':
        return jsonify({'error': 'Unauthorized'}), 403
    assignment = StaffDocumentAssignment.query.filter_by(id=assignment_id, staff_id=user.id).first_or_404()
    data = request.get_json(silent=True) or {}
    assignment.signature_name = (data.get('signature_name') or user.name).strip()
    if 'filled_data' in data:
        assignment.filled_data = json.dumps(data['filled_data'])
    assignment.signed_at = datetime.utcnow()
    assignment.status = 'Submitted' if assignment.document and assignment.document.is_fillable else 'Signed'
    db.session.commit()
    return jsonify(assignment.to_dict()), 200


@payroll_bp.route('/my/performance', methods=['GET'])
@jwt_required()
def get_my_performance():
    user, role = _get_current_user()
    if not user:
        return jsonify({'error': 'Unauthorized'}), 401
    if role != 'staff':
        return jsonify({'goals': [], 'reviews': []}), 200
    goals = PerformanceGoal.query.filter_by(staff_id=user.id).order_by(PerformanceGoal.created_at.desc()).all()
    reviews = PerformanceReview.query.filter_by(staff_id=user.id).order_by(PerformanceReview.created_at.desc()).all()
    return jsonify({
        'goals': [g.to_dict() for g in goals],
        'reviews': [r.to_dict() for r in reviews],
    }), 200


@payroll_bp.route('/my/performance/reviews/<int:review_id>/submit', methods=['POST'])
@jwt_required()
def submit_my_review(review_id):
    user, role = _get_current_user()
    if not user or role != 'staff':
        return jsonify({'error': 'Unauthorized'}), 403
    review = PerformanceReview.query.filter_by(id=review_id, staff_id=user.id).first_or_404()
    data = request.get_json(silent=True) or {}
    review.responses_json = json.dumps(data.get('responses', {}))
    if data.get('rating') is not None:
        review.rating = int(data['rating'])
    review.status = 'Completed'
    review.submitted_at = datetime.utcnow()
    db.session.commit()
    return jsonify(review.to_dict()), 200


# ==============================================================================
# 2. ADMIN TIME TRACKING & SETTINGS (/api/payroll/admin/settings, timesheets)
# ==============================================================================

@payroll_bp.route('/admin/settings', methods=['GET'])
@jwt_required()
def get_admin_settings():
    if not _get_admin_actor():
        return jsonify({'error': 'Unauthorized'}), 401
    settings = _get_or_create_settings()
    return jsonify(settings.to_dict()), 200


@payroll_bp.route('/admin/settings', methods=['PUT'])
@jwt_required()
def update_admin_settings():
    if not _get_admin_actor():
        return jsonify({'error': 'Unauthorized'}), 401
    settings = _get_or_create_settings()
    data = request.get_json(silent=True) or {}
    if 'workweek_start' in data:
        settings.workweek_start = data['workweek_start']
    if 'allowed_days' in data:
        settings.allowed_days = json.dumps(data['allowed_days'])
    if 'require_whole_hours' in data:
        settings.require_whole_hours = bool(data['require_whole_hours'])
    if 'overtime_weekly_threshold' in data:
        settings.overtime_weekly_threshold = float(data['overtime_weekly_threshold'])
    if 'overtime_daily_double' in data:
        settings.overtime_daily_double = float(data['overtime_daily_double']) if data['overtime_daily_double'] is not None else None
    if 'geolocation_enabled' in data:
        settings.geolocation_enabled = bool(data['geolocation_enabled'])
    if 'kiosk_enabled' in data:
        settings.kiosk_enabled = bool(data['kiosk_enabled'])
    if 'non_us_tracking_enabled' in data:
        settings.non_us_tracking_enabled = bool(data['non_us_tracking_enabled'])
    if 'next_invoice_number' in data:
        settings.next_invoice_number = int(data['next_invoice_number'])
    db.session.commit()
    return jsonify(settings.to_dict()), 200


@payroll_bp.route('/admin/time-assignments', methods=['GET'])
@jwt_required()
def get_time_assignments():
    if not _get_admin_actor():
        return jsonify({'error': 'Unauthorized'}), 401
    all_staff = Staff.query.order_by(Staff.name.asc()).all()
    assignments = {a.staff_id: a for a in StaffTimeTrackingAssignment.query.all()}
    result = []
    for s in all_staff:
        a = assignments.get(s.id)
        addr = {}
        try:
            addr = json.loads(s.address) if s.address else {}
        except Exception:
            addr = {}
        result.append({
            'staff_id': s.id,
            'name': s.name,
            'state': addr.get('state') or '-',
            'country': s.country or 'United States of America',
            'employment_type': s.worker_type or 'Contractor',
            'worker_status': s.worker_status or 'Active',
            'is_assigned': a.is_assigned if a else True,
            'overtime_policy': a.overtime_policy if a else 'Default',
            'break_policy': a.break_policy if a else '-',
            'timezone': a.timezone if a else 'Eastern Time (US & Canada) (EDT, GMT-4)',
        })
    return jsonify({'assignments': result}), 200


@payroll_bp.route('/admin/time-assignments', methods=['PUT'])
@jwt_required()
def update_time_assignment():
    if not _get_admin_actor():
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json(silent=True) or {}
    items = data.get('assignments')
    if items is None and 'staff_id' in data:
        items = [data]
    for item in (items or []):
        sid = item.get('staff_id')
        if not sid:
            continue
        assign = _get_or_create_assignment(sid)
        if 'is_assigned' in item:
            assign.is_assigned = bool(item['is_assigned'])
        if 'overtime_policy' in item:
            assign.overtime_policy = item['overtime_policy']
        if 'break_policy' in item:
            assign.break_policy = item['break_policy']
    db.session.commit()
    return jsonify({'message': 'Time tracking assignments updated'}), 200


@payroll_bp.route('/admin/timesheets', methods=['GET'])
@jwt_required()
def get_admin_timesheets():
    if not _get_admin_actor():
        return jsonify({'error': 'Unauthorized'}), 401

    settings = _get_or_create_settings()
    start_str = request.args.get('start_date')
    end_str = request.args.get('end_date')
    today = date.today()
    start_date = _parse_date(start_str) if start_str else (today - timedelta(days=13))
    end_date = _parse_date(end_str) if end_str else today

    all_staff = Staff.query.order_by(Staff.name.asc()).all()
    entries = TimeEntry.query.filter(
        TimeEntry.work_date >= start_date,
        TimeEntry.work_date <= end_date,
    ).order_by(TimeEntry.work_date.asc(), TimeEntry.start_time.asc()).all()

    entries_by_staff = {}
    for e in entries:
        entries_by_staff.setdefault(e.staff_id, []).append(e)

    assignments = {a.staff_id: a for a in StaffTimeTrackingAssignment.query.all()}
    timesheets = []
    active_count = 0
    for s in all_staff:
        a = assignments.get(s.id)
        is_assigned = a.is_assigned if a else True
        if is_assigned and (s.worker_status or 'Active') == 'Active':
            active_count += 1
        s_entries = entries_by_staff.get(s.id, [])
        total_mins = sum(e.duration_minutes for e in s_entries)
        synced_count = sum(1 for e in s_entries if e.status == 'synced')
        paid_count = sum(1 for e in s_entries if e.status == 'paid')
        draft_count = sum(1 for e in s_entries if e.status == 'draft')

        is_us = 'united states' in (s.country or 'United States of America').lower() or (s.country or '').upper() in ('US', 'USA')
        timesheets.append({
            'staff_id': s.id,
            'name': s.name,
            'email': s.email,
            'department': s.departments[0].name if s.departments else 'Teacher Department',
            'worker_type': s.worker_type or 'Contractor',
            'worker_status': s.worker_status or 'Active',
            'is_us': is_us,
            'country': s.country or 'United States of America',
            'hourly_rate': s.hourly_rate or 25.0,
            'timezone': a.timezone if a else 'Eastern Time (US & Canada) (EDT, GMT-4)',
            'total_minutes': total_mins,
            'total_hours': round(total_mins / 60.0, 2),
            'shift_count': len(s_entries),
            'draft_count': draft_count,
            'synced_count': synced_count,
            'paid_count': paid_count,
            # Full shift details visible to admin (fixing Gusto's privacy restriction!)
            'shifts': [e.to_dict() for e in s_entries],
        })

    return jsonify({
        'start_date': start_date.isoformat(),
        'end_date': end_date.isoformat(),
        'active_on_time_tracking': active_count,
        'last_synced_at': settings.last_synced_at.isoformat() if settings.last_synced_at else None,
        'last_synced_start': settings.last_synced_start.isoformat() if settings.last_synced_start else None,
        'last_synced_end': settings.last_synced_end.isoformat() if settings.last_synced_end else None,
        'timesheets': timesheets,
    }), 200


@payroll_bp.route('/admin/timesheets/<int:staff_id>', methods=['GET'])
@jwt_required()
def get_admin_staff_timesheet(staff_id):
    if not _get_admin_actor():
        return jsonify({'error': 'Unauthorized'}), 401
    staff = Staff.query.get_or_404(staff_id)
    start_str = request.args.get('start_date')
    end_str = request.args.get('end_date')
    today = date.today()
    start_date = _parse_date(start_str) if start_str else (today - timedelta(days=13))
    end_date = _parse_date(end_str) if end_str else today

    entries = TimeEntry.query.filter(
        TimeEntry.staff_id == staff.id,
        TimeEntry.work_date >= start_date,
        TimeEntry.work_date <= end_date,
    ).order_by(TimeEntry.work_date.asc(), TimeEntry.start_time.asc()).all()

    assign = _get_or_create_assignment(staff.id)
    return jsonify({
        'staff': staff.to_dict(),
        'timezone': assign.timezone,
        'start_date': start_date.isoformat(),
        'end_date': end_date.isoformat(),
        'total_minutes': sum(e.duration_minutes for e in entries),
        'shifts': [e.to_dict() for e in entries],
    }), 200


@payroll_bp.route('/admin/timesheets/<int:staff_id>/shifts', methods=['POST'])
@jwt_required()
def admin_add_shift(staff_id):
    actor = _get_admin_actor()
    if not actor:
        return jsonify({'error': 'Unauthorized'}), 401
    staff = Staff.query.get_or_404(staff_id)
    data = request.get_json(silent=True) or {}
    try:
        work_date = _parse_date(data.get('work_date'))
        start_time = data.get('start_time', '').strip()
        end_time = data.get('end_time', '').strip()
        duration_minutes = _compute_duration_minutes(start_time, end_time)
    except ValueError as err:
        return jsonify({'error': str(err)}), 400

    assign = _get_or_create_assignment(staff.id)
    entry = TimeEntry(
        staff_id=staff.id,
        work_date=work_date,
        start_time=start_time,
        end_time=end_time,
        duration_minutes=duration_minutes,
        note=(data.get('note') or '').strip(),
        timezone=assign.timezone,
        status=data.get('status', 'draft'),
        admin_override_note=(data.get('admin_override_note') or f"Added by {actor.name}").strip(),
        updated_by_admin=True,
    )
    db.session.add(entry)
    db.session.commit()
    return jsonify(entry.to_dict()), 201


@payroll_bp.route('/admin/timesheets/shifts/<int:shift_id>', methods=['PUT'])
@jwt_required()
def admin_update_shift(shift_id):
    actor = _get_admin_actor()
    if not actor:
        return jsonify({'error': 'Unauthorized'}), 401
    entry = TimeEntry.query.get_or_404(shift_id)
    data = request.get_json(silent=True) or {}
    start_time = data.get('start_time', entry.start_time)
    end_time = data.get('end_time', entry.end_time)
    try:
        duration_minutes = _compute_duration_minutes(start_time, end_time)
    except ValueError as err:
        return jsonify({'error': str(err)}), 400

    entry.start_time = start_time
    entry.end_time = end_time
    entry.duration_minutes = duration_minutes
    if 'note' in data:
        entry.note = data['note']
    if 'admin_override_note' in data:
        entry.admin_override_note = data['admin_override_note']
    else:
        entry.admin_override_note = f"Adjusted by {actor.name}"
    entry.updated_by_admin = True
    db.session.commit()
    return jsonify(entry.to_dict()), 200


@payroll_bp.route('/admin/timesheets/shifts/<int:shift_id>', methods=['DELETE'])
@jwt_required()
def admin_delete_shift(shift_id):
    if not _get_admin_actor():
        return jsonify({'error': 'Unauthorized'}), 401
    entry = TimeEntry.query.get_or_404(shift_id)
    db.session.delete(entry)
    db.session.commit()
    return jsonify({'message': 'Shift deleted'}), 200


@payroll_bp.route('/admin/timesheets/sync', methods=['POST'])
@jwt_required()
def admin_sync_timesheets():
    actor = _get_admin_actor()
    if not actor:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json(silent=True) or {}
    start_date = _parse_date(data.get('start_date'))
    end_date = _parse_date(data.get('end_date'))
    if not start_date or not end_date:
        return jsonify({'error': 'start_date and end_date are required'}), 400

    query = TimeEntry.query.filter(
        TimeEntry.work_date >= start_date,
        TimeEntry.work_date <= end_date,
        TimeEntry.status == 'draft',
    )
    if data.get('staff_ids'):
        query = query.filter(TimeEntry.staff_id.in_(data['staff_ids']))

    entries = query.all()
    for e in entries:
        e.status = 'synced'
        e.pay_period_start = start_date
        e.pay_period_end = end_date

    settings = _get_or_create_settings()
    settings.last_synced_at = datetime.utcnow()
    settings.last_synced_start = start_date
    settings.last_synced_end = end_date
    db.session.commit()
    log_activity(actor, f"Synced {len(entries)} time entries for {start_date} to {end_date}")
    return jsonify({
        'synced_count': len(entries),
        'last_synced_at': settings.last_synced_at.isoformat(),
        'last_synced_start': settings.last_synced_start.isoformat(),
        'last_synced_end': settings.last_synced_end.isoformat(),
    }), 200


# ==============================================================================
# 3. ADMIN PAYROLL RUNS & HISTORY (/api/payroll/admin/pay/*)
# ==============================================================================

@payroll_bp.route('/admin/pay/preview', methods=['GET'])
@jwt_required()
def preview_payroll_run():
    if not _get_admin_actor():
        return jsonify({'error': 'Unauthorized'}), 401

    settings = _get_or_create_settings()
    worker_group = request.args.get('worker_group', 'all')  # 'us_contractor' | 'non_us_contractor' | 'employee' | 'all'
    start_str = request.args.get('start_date')
    end_str = request.args.get('end_date')
    include_drafts = request.args.get('include_drafts', 'true').lower() == 'true'

    statuses = ['synced', 'draft'] if include_drafts else ['synced']
    query = TimeEntry.query.filter(TimeEntry.status.in_(statuses))
    if start_str:
        query = query.filter(TimeEntry.work_date >= _parse_date(start_str))
    if end_str:
        query = query.filter(TimeEntry.work_date <= _parse_date(end_str))

    entries = query.all()
    mins_by_staff = {}
    for e in entries:
        mins_by_staff[e.staff_id] = mins_by_staff.get(e.staff_id, 0) + e.duration_minutes

    # CRITICAL: Include ALL staff (even Dismissed!) if they have unpaid hours, or active staff in group
    all_staff = Staff.query.order_by(Staff.name.asc()).all()
    rows = []
    inv_num = settings.next_invoice_number
    for s in all_staff:
        unpaid_mins = mins_by_staff.get(s.id, 0)
        is_dismissed = (s.worker_status or 'Active').lower() == 'dismissed'
        # Dismissed staff ONLY appear if they have unpaid hours (Caitlin's rule!)
        if is_dismissed and unpaid_mins == 0:
            continue

        is_us = 'united states' in (s.country or 'United States of America').lower() or (s.country or '').upper() in ('US', 'USA')
        w_type = (s.worker_type or 'Contractor').lower()
        if worker_group == 'employee' and w_type != 'employee':
            continue
        elif worker_group == 'us_contractor' and (w_type == 'employee' or not is_us):
            continue
        elif worker_group == 'non_us_contractor' and (w_type == 'employee' or is_us):
            continue

        hours = round(unpaid_mins / 60.0, 2)
        is_emp = w_type == 'employee'
        if is_emp and hours == 0:
            # Employees (like Marcus) default to 80h for a 2-week pay period and admin adjusts down
            hours = 80.0
        rate = float(s.hourly_rate or 25.0)
        regular_pay = round(hours * rate, 2)
        emp_taxes = round(regular_pay * 0.13396, 2) if is_emp else 0.0
        comp_taxes = round(regular_pay * 0.0765, 2) if is_emp else 0.0
        rows.append({
            'staff_id': s.id,
            'staff_name': s.name,
            'worker_type': s.worker_type or 'Contractor',
            'worker_status': s.worker_status or 'Active',
            'is_dismissed_with_unpaid_hours': is_dismissed and unpaid_mins > 0,
            'is_us': is_us,
            'country': s.country or 'United States of America',
            'payment_method': s.payment_method or ('Direct deposit' if is_us else 'Check'),
            'hourly_rate': rate,
            'hours_worked': hours,
            'regular_pay': regular_pay,
            'employee_taxes': emp_taxes,
            'company_taxes': comp_taxes,
            'bonus': 0.0,
            'reimbursement': 0.0,
            'memo': '',
            'invoice_number': str(inv_num).zfill(3),
            'selected': hours > 0 or is_emp,
        })
        inv_num += 1

    return jsonify({
        'rows': rows,
        'next_invoice_number': settings.next_invoice_number,
        'paid_from': settings.bank_account_label,
    }), 200


@payroll_bp.route('/admin/pay/run', methods=['POST'])
@jwt_required()
def submit_payroll_run():
    actor = _get_admin_actor()
    if not actor:
        return jsonify({'error': 'Unauthorized'}), 401

    settings = _get_or_create_settings()
    data = request.get_json(silent=True) or {}
    items_data = data.get('items', [])
    if not items_data:
        return jsonify({'error': 'No payment items provided'}), 400

    pay_period_start = _parse_date(data.get('pay_period_start'))
    pay_period_end = _parse_date(data.get('pay_period_end'))
    pay_date = _parse_date(data.get('pay_date')) or date.today()
    run_type = data.get('run_type', 'us_contractor')

    run = PayrollRun(
        run_type=run_type,
        pay_period_start=pay_period_start,
        pay_period_end=pay_period_end,
        pay_date=pay_date,
        status='paid',
        total_amount=0.0,
        paid_from=data.get('paid_from') or settings.bank_account_label,
        created_by=getattr(actor, 'name', 'Admin'),
    )
    db.session.add(run)
    db.session.flush()

    total_amount = 0.0
    max_inv = settings.next_invoice_number - 1
    paid_staff_ids = []

    for row in items_data:
        sid = row.get('staff_id')
        staff = Staff.query.get(sid)
        if not staff:
            continue
        hours = float(row.get('hours_worked', 0))
        rate = float(row.get('hourly_rate', staff.hourly_rate or 0))
        bonus = float(row.get('bonus', 0))
        reimbursement = float(row.get('reimbursement', 0))
        regular_pay = round(hours * rate, 2)
        gross_taxable = regular_pay + bonus
        is_emp = (staff.worker_type or 'Contractor').lower() == 'employee'
        emp_taxes = round(gross_taxable * 0.13396, 2) if is_emp else 0.0
        comp_taxes = round(gross_taxable * 0.0765, 2) if is_emp else 0.0
        net_pay = round(gross_taxable - emp_taxes + reimbursement, 2)
        if regular_pay + bonus + reimbursement <= 0:
            continue

        inv_str = str(row.get('invoice_number') or str(settings.next_invoice_number).zfill(3))
        try:
            inv_int = int(re.sub(r'\D', '', inv_str))
            if inv_int > max_inv:
                max_inv = inv_int
        except Exception:
            pass

        item = PayrollPaymentItem(
            payroll_run_id=run.id,
            staff_id=staff.id,
            staff_name=staff.name,
            invoice_number=inv_str,
            payment_method=row.get('payment_method') or staff.payment_method or 'Direct deposit',
            worker_type=staff.worker_type or 'Contractor',
            country=staff.country or 'United States of America',
            hourly_rate=rate,
            hours_worked=hours,
            regular_pay=regular_pay,
            bonus=bonus,
            reimbursement=reimbursement,
            employee_taxes=emp_taxes,
            company_taxes=comp_taxes,
            memo=(row.get('memo') or '').strip(),
            net_pay=net_pay,
            status='paid',
        )
        db.session.add(item)
        total_amount += round(gross_taxable + reimbursement + comp_taxes, 2)
        paid_staff_ids.append(staff.id)

    run.total_amount = round(total_amount, 2)
    settings.next_invoice_number = max_inv + 1

    # Mark matching time entries as paid
    if paid_staff_ids:
        entry_query = TimeEntry.query.filter(
            TimeEntry.staff_id.in_(paid_staff_ids),
            TimeEntry.status.in_(['draft', 'synced']),
        )
        if pay_period_start:
            entry_query = entry_query.filter(TimeEntry.work_date >= pay_period_start)
        if pay_period_end:
            entry_query = entry_query.filter(TimeEntry.work_date <= pay_period_end)
        for e in entry_query.all():
            e.status = 'paid'
            e.payroll_run_id = run.id

    db.session.commit()
    log_activity(actor, f"Submitted payroll run #{run.id} totaling ${run.total_amount:.2f}")
    return jsonify(run.to_dict(include_items=True)), 201


@payroll_bp.route('/admin/pay/history', methods=['GET'])
@jwt_required()
def get_payroll_history():
    if not _get_admin_actor():
        return jsonify({'error': 'Unauthorized'}), 401
    runs = PayrollRun.query.order_by(PayrollRun.created_at.desc()).all()
    items = PayrollPaymentItem.query.order_by(PayrollPaymentItem.paid_at.desc()).all()
    return jsonify({
        'runs': [r.to_dict(include_items=True) for r in runs],
        'items': [i.to_dict() for i in items],
    }), 200


@payroll_bp.route('/admin/pay/items/<int:item_id>', methods=['GET'])
@jwt_required()
def get_payment_item_detail(item_id):
    if not _get_admin_actor():
        return jsonify({'error': 'Unauthorized'}), 401
    item = PayrollPaymentItem.query.get_or_404(item_id)
    return jsonify(item.to_dict()), 200


# ==============================================================================
# 4. ADMIN PEOPLE: ONBOARDING, DISMISSAL (NEVER DELETE), REACTIVATION
# ==============================================================================

@payroll_bp.route('/admin/people/onboard', methods=['POST'])
@jwt_required()
def onboard_staff_member():
    actor = _get_admin_actor()
    if not actor:
        return jsonify({'error': 'Unauthorized'}), 401
    _seed_default_documents_if_empty()

    data = request.get_json(silent=True) or {}
    first_name = (data.get('first_name') or '').strip()
    middle_initial = (data.get('middle_initial') or '').strip()
    last_name = (data.get('last_name') or '').strip()
    full_name = data.get('name') or ' '.join(p for p in [first_name, middle_initial, last_name] if p)
    email = (data.get('personal_email') or data.get('email') or '').strip()

    if not full_name or not email:
        return jsonify({'error': 'Name and email are required'}), 400
    if Staff.query.filter_by(email=email).first():
        return jsonify({'error': 'A team member with this email already exists'}), 409

    country = data.get('country') or 'United States of America'
    is_us = 'united states' in country.lower() or country.upper() in ('US', 'USA')
    payment_method = data.get('payment_method') or ('Direct deposit' if is_us else 'Check')

    new_staff = Staff(
        name=full_name,
        preferred_name=(data.get('preferred_name') or first_name or full_name).strip(),
        legal_name=full_name,
        email=email,
        personal_email=email,
        work_email=(data.get('work_email') or email).strip(),
        role='teacher' if 'teacher' in (data.get('job_title') or '').lower() else 'staff',
        job_title=data.get('job_title') or 'Teacher',
        worker_type=data.get('worker_type') or 'Contractor',
        worker_status='Onboarding' if data.get('send_invite', True) else 'Active',
        hourly_rate=float(data.get('hourly_rate') or 25.0),
        manager_name=data.get('manager_name') or 'Marcus Cooper',
        supervisor_name=data.get('supervisor_name') or 'Marcus Cooper',
        country=country,
        start_date=data.get('start_date') or date.today().strftime('%b %d, %Y'),
        payment_method=payment_method,
        checkin_schedule=data.get('checkin_schedule') or '30,60,90',
        is_active=True,
    )
    new_staff.set_password(data.get('password') or uuid.uuid4().hex)

    # Department assignment (select or create new)
    dept_id = data.get('department_id')
    new_dept_name = (data.get('new_department_name') or '').strip()
    if new_dept_name:
        dept = Department.query.filter(db.func.lower(Department.name) == new_dept_name.lower()).first()
        if not dept:
            dept = Department(name=new_dept_name)
            db.session.add(dept)
            db.session.flush()
        new_staff.departments.append(dept)
    elif dept_id:
        dept = Department.query.get(dept_id)
        if dept:
            new_staff.departments.append(dept)

    db.session.add(new_staff)
    db.session.flush()

    # Auto-assign time tracking (Caitlin's rule: everyone tracks time!)
    assign = StaffTimeTrackingAssignment(
        staff_id=new_staff.id,
        is_assigned=bool(data.get('assign_time_tracking', True)),
    )
    db.session.add(assign)

    # Assign selected documents (plus auto-W9 for US contractors or future_hires_rule)
    doc_ids = set(data.get('document_ids') or [])
    active_docs = HRDocument.query.filter_by(status='active').all()
    for doc in active_docs:
        auto_match = (
            doc.id in doc_ids
            or doc.future_hires_rule == 'all'
            or (doc.future_hires_rule == 'us' and is_us)
            or (doc.future_hires_rule == 'non_us' and not is_us)
        )
        if auto_match:
            db.session.add(StaffDocumentAssignment(document_id=doc.id, staff_id=new_staff.id))

    db.session.commit()
    log_activity(actor, f"Onboarded '{new_staff.name}' ({new_staff.worker_type})", new_staff)
    return jsonify(new_staff.to_dict()), 201


@payroll_bp.route('/admin/people/<int:staff_id>/dismiss', methods=['POST'])
@jwt_required()
def dismiss_staff_member(staff_id):
    actor = _get_admin_actor()
    if not actor:
        return jsonify({'error': 'Unauthorized'}), 401
    staff = Staff.query.get_or_404(staff_id)
    data = request.get_json(silent=True) or {}
    staff.worker_status = 'Dismissed'
    staff.dismissed_effective_date = data.get('effective_date') or date.today().isoformat()
    staff.dismissal_reason = (data.get('reason') or '').strip()
    staff.is_active = False
    db.session.commit()
    log_activity(actor, f"Dismissed '{staff.name}' effective {staff.dismissed_effective_date}", staff)
    return jsonify(staff.to_dict()), 200


@payroll_bp.route('/admin/people/<int:staff_id>/reactivate', methods=['POST'])
@jwt_required()
def reactivate_staff_member(staff_id):
    actor = _get_admin_actor()
    if not actor:
        return jsonify({'error': 'Unauthorized'}), 401
    staff = Staff.query.get_or_404(staff_id)
    staff.worker_status = 'Active'
    staff.is_active = True
    staff.dismissed_effective_date = None
    staff.dismissal_reason = None
    db.session.commit()
    log_activity(actor, f"Reactivated '{staff.name}'", staff)
    return jsonify(staff.to_dict()), 200


# ==============================================================================
# 5. ADMIN DOCUMENTS (/api/payroll/admin/documents/*)
# ==============================================================================

@payroll_bp.route('/admin/documents', methods=['GET'])
@jwt_required()
def get_admin_documents():
    if not _get_admin_actor():
        return jsonify({'error': 'Unauthorized'}), 401
    _seed_default_documents_if_empty()
    docs = HRDocument.query.order_by(HRDocument.created_at.desc()).all()
    artifacts = StaffDocumentAssignment.query.order_by(StaffDocumentAssignment.assigned_at.desc()).all()
    return jsonify({
        'documents': [d.to_dict() for d in docs],
        'artifacts': [a.to_dict() for a in artifacts],
    }), 200


@payroll_bp.route('/admin/documents', methods=['POST'])
@jwt_required()
def create_admin_document():
    actor = _get_admin_actor()
    if not actor:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json(silent=True) or {}
    title = (data.get('title') or '').strip()
    if not title:
        return jsonify({'error': 'Document title is required'}), 400

    doc = HRDocument(
        title=title,
        category=data.get('category') or 'Company Policies',
        description=(data.get('description') or '').strip(),
        doc_type=data.get('doc_type') or 'shared',
        status=data.get('status') or 'active',
        file_name=data.get('file_name'),
        content_body=data.get('content_body') or '',
        requires_signature=bool(data.get('requires_signature', True)),
        is_fillable=bool(data.get('is_fillable', False)),
        fillable_fields=json.dumps(data.get('fillable_fields') or []),
        future_hires_rule=data.get('future_hires_rule') or 'none',
        uploaded_by=getattr(actor, 'name', 'Cooper Galvis Enterprises LLC'),
    )
    db.session.add(doc)
    db.session.commit()
    return jsonify(doc.to_dict()), 201


@payroll_bp.route('/admin/documents/<int:doc_id>', methods=['PUT'])
@jwt_required()
def update_admin_document(doc_id):
    if not _get_admin_actor():
        return jsonify({'error': 'Unauthorized'}), 401
    doc = HRDocument.query.get_or_404(doc_id)
    data = request.get_json(silent=True) or {}
    for field in ('title', 'category', 'description', 'status', 'content_body', 'future_hires_rule'):
        if field in data:
            setattr(doc, field, data[field])
    if 'requires_signature' in data:
        doc.requires_signature = bool(data['requires_signature'])
    db.session.commit()
    return jsonify(doc.to_dict()), 200


@payroll_bp.route('/admin/documents/<int:doc_id>/recipients', methods=['POST'])
@jwt_required()
def update_document_recipients(doc_id):
    actor = _get_admin_actor()
    if not actor:
        return jsonify({'error': 'Unauthorized'}), 401
    doc = HRDocument.query.get_or_404(doc_id)
    data = request.get_json(silent=True) or {}

    if 'future_hires_rule' in data:
        doc.future_hires_rule = data['future_hires_rule']
    dept_ids = data.get('department_ids') or []
    staff_ids = set(data.get('staff_ids') or [])

    if dept_ids:
        doc.target_department_ids = json.dumps(dept_ids)
        depts = Department.query.filter(Department.id.in_(dept_ids)).all()
        for d in depts:
            for s in d.staff_members:
                staff_ids.add(s.id)

    existing_sids = {a.staff_id for a in StaffDocumentAssignment.query.filter_by(document_id=doc.id).all()}
    added = 0
    for sid in staff_ids:
        if sid not in existing_sids:
            db.session.add(StaffDocumentAssignment(document_id=doc.id, staff_id=sid))
            _create_staff_notification(sid, f"New document assigned: {doc.title}", f"Please review and sign/acknowledge '{doc.title}'.")
            added += 1

    db.session.commit()
    return jsonify({'document': doc.to_dict(), 'added_recipients': added}), 200


@payroll_bp.route('/admin/documents/assignments/<int:assignment_id>/remind', methods=['POST'])
@jwt_required()
def remind_document_assignment(assignment_id):
    actor = _get_admin_actor()
    if not actor:
        return jsonify({'error': 'Unauthorized'}), 401
    assignment = StaffDocumentAssignment.query.get_or_404(assignment_id)
    assignment.last_reminded_at = datetime.utcnow()
    title = assignment.document.title if assignment.document else 'Document'
    _create_staff_notification(
        assignment.staff_id,
        f"Reminder: Please sign '{title}'",
        f"HR sent a reminder to review and sign/acknowledge '{title}'.",
    )
    db.session.commit()
    return jsonify(assignment.to_dict()), 200


# ==============================================================================
# 6. ADMIN REPORTS (/api/payroll/admin/reports/*)
# ==============================================================================

@payroll_bp.route('/admin/reports/contractor-payments', methods=['GET'])
@jwt_required()
def report_contractor_payments():
    if not _get_admin_actor():
        return jsonify({'error': 'Unauthorized'}), 401
    start_str = request.args.get('start_date')
    end_str = request.args.get('end_date')
    staff_id = request.args.get('staff_id')

    query = PayrollPaymentItem.query.join(PayrollRun)
    if start_str:
        query = query.filter(PayrollRun.pay_date >= _parse_date(start_str))
    if end_str:
        query = query.filter(PayrollRun.pay_date <= _parse_date(end_str))
    if staff_id and staff_id != 'all':
        query = query.filter(PayrollPaymentItem.staff_id == int(staff_id))

    items = query.order_by(PayrollRun.pay_date.desc()).all()
    return jsonify({'rows': [i.to_dict() for i in items]}), 200


# ==============================================================================
# 7. ADMIN PERFORMANCE (/api/payroll/admin/performance/*)
# ==============================================================================

@payroll_bp.route('/admin/performance', methods=['GET'])
@jwt_required()
def get_admin_performance():
    if not _get_admin_actor():
        return jsonify({'error': 'Unauthorized'}), 401
    goals = PerformanceGoal.query.order_by(PerformanceGoal.created_at.desc()).all()
    reviews = PerformanceReview.query.order_by(PerformanceReview.created_at.desc()).all()
    return jsonify({
        'goals': [g.to_dict() for g in goals],
        'reviews': [r.to_dict() for r in reviews],
    }), 200


@payroll_bp.route('/admin/performance/goals', methods=['POST'])
@jwt_required()
def create_performance_goal():
    actor = _get_admin_actor()
    if not actor:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json(silent=True) or {}
    goal = PerformanceGoal(
        staff_id=int(data['staff_id']),
        title=data['title'],
        description=data.get('description', ''),
        due_date=data.get('due_date'),
        progress=int(data.get('progress', 0)),
        status=data.get('status', 'In Progress'),
        created_by=getattr(actor, 'name', 'Supervisor'),
    )
    db.session.add(goal)
    db.session.commit()
    return jsonify(goal.to_dict()), 201


@payroll_bp.route('/admin/performance/reviews', methods=['POST'])
@jwt_required()
def create_performance_review():
    actor = _get_admin_actor()
    if not actor:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json(silent=True) or {}
    default_qs = [
        'What were your key accomplishments this period?',
        'Which classes or responsibilities went best, and where do you need support?',
        'Rate your overall performance and alignment with school goals.',
    ]
    review = PerformanceReview(
        staff_id=int(data['staff_id']),
        review_type=data.get('review_type', 'self'),
        title=data.get('title') or 'Performance Appraisal',
        period=data.get('period') or 'Q4 2026',
        questions_json=json.dumps(data.get('questions') or default_qs),
        responses_json=json.dumps(data.get('responses') or {}),
        rating=data.get('rating'),
        status=data.get('status', 'Pending'),
        reviewer_name=getattr(actor, 'name', 'Marcus Cooper'),
    )
    db.session.add(review)
    db.session.commit()
    return jsonify(review.to_dict()), 201
