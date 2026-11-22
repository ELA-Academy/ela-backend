"""
Centralized allow-list Role-Based Access Control (RBAC) utilities.

Design principles:
- Allow-list by default: any unknown or future role (e.g. 'student') is
  automatically denied unless explicitly permitted.
- Keeps existing SuperAdmin, Staff, Teacher, and Parent JWTs working with
  zero database migrations or forced logouts.
"""

import re
from functools import wraps
from flask import jsonify, request
from flask_jwt_extended import verify_jwt_in_request, get_jwt, get_jwt_identity
from app.models.staff_model import Staff

STAFF_ROLES = frozenset({'staff', 'teacher', 'admin'})
NON_TEACHER_STAFF_ROLES = frozenset({'staff', 'admin'})
INTERNAL_ROLES = frozenset({'superadmin', 'staff', 'teacher', 'admin'})

# Department name regex patterns matching existing school departments
ACCOUNTING_DEPTS = (r'\baccounting\b', r'\bbilling\b', r'\bfinance\b', r'\badmin(?:istration)?\b')
ADMISSIONS_DEPTS = (r'\badmissions?\b', r'\benrollment\b', r'\baccounting\b', r'\badmin(?:istration)?\b')
IT_ADMIN_DEPTS = (r'\bit\b', r'\binformation technology\b', r'\binfo tech\b', r'\btech\b', r'\badmin(?:istration)?\b')


def is_staff_role(role):
    """Returns True for any employee stored in the `staff` table (staff, teacher, admin)."""
    return (role or '').strip().lower() in STAFF_ROLES


def is_internal_role(role):
    """Returns True for SuperAdmin or any Staff/Teacher role (never Parent or future Student)."""
    return (role or '').strip().lower() in INTERNAL_ROLES


def staff_matches_departments(staff_member, patterns):
    """Checks if a Staff member belongs to any department matching the regex patterns."""
    if not staff_member or not patterns:
        return False
    for dept in (staff_member.departments or []):
        dept_name = (dept.name or '').strip().lower()
        for pat in patterns:
            if re.search(pat, dept_name, re.IGNORECASE):
                return True
    return False


def check_current_access(allowed_roles=INTERNAL_ROLES, department_patterns=None, allow_payroll_admin=False):
    """
    Verifies the current JWT against an explicit allow-list of roles and
    optional department patterns. Returns None if authorized, or a Flask
    (response, status_code) tuple if denied.
    """
    if request.method == 'OPTIONS':
        return None

    verify_jwt_in_request()
    claims = get_jwt()
    role = (claims.get('role') or '').strip().lower()

    allowed_lower = {r.lower() for r in allowed_roles}
    if role not in allowed_lower:
        return jsonify({"error": "Forbidden: you do not have permission to access this resource."}), 403

    if role == 'superadmin':
        return None

    if department_patterns and role in STAFF_ROLES:
        email = get_jwt_identity()
        staff = Staff.query.filter_by(email=email).first()
        if not staff or not getattr(staff, 'is_active', True):
            return jsonify({"error": "Forbidden: inactive or unknown staff account."}), 403

        if staff_matches_departments(staff, department_patterns):
            return None

        if allow_payroll_admin:
            from app.models.payroll_models import PayrollAdminAccess
            access = PayrollAdminAccess.query.filter_by(staff_id=staff.id, is_active=True).first()
            if access:
                return None

        return jsonify({"error": "Forbidden: this action is restricted to authorized departments."}), 403

    return None


def require_access(allowed_roles=INTERNAL_ROLES, department_patterns=None, allow_payroll_admin=False):
    """Route decorator enforcing allow-list RBAC + optional department scoping."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            denied = check_current_access(
                allowed_roles=allowed_roles,
                department_patterns=department_patterns,
                allow_payroll_admin=allow_payroll_admin,
            )
            if denied is not None:
                return denied
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def protect_blueprint(bp, allowed_roles=INTERNAL_ROLES, department_patterns=None,
                      allow_payroll_admin=False, public_endpoints=None,
                      read_allowed_roles=None):
    """
    Registers a before_request guard on a Flask Blueprint.
    - public_endpoints: set of endpoint function names (e.g. {'create_lead'}) that bypass JWT/RBAC.
    - read_allowed_roles: optional wider allow-list for GET requests (e.g. letting teachers read lists).
    """
    public_set = set(public_endpoints or ())

    @bp.before_request
    def _rbac_guard():
        if request.method == 'OPTIONS':
            return None
        endpoint = (request.endpoint or '').split('.')[-1]
        if endpoint in public_set:
            return None

        if request.method == 'GET' and read_allowed_roles is not None:
            return check_current_access(allowed_roles=read_allowed_roles)

        return check_current_access(
            allowed_roles=allowed_roles,
            department_patterns=department_patterns,
            allow_payroll_admin=allow_payroll_admin,
        )
