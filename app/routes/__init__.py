from flask import Blueprint
from app.utils.rbac import (
    protect_blueprint,
    INTERNAL_ROLES,
    ACCOUNTING_DEPTS,
    ADMISSIONS_DEPTS,
    IT_ADMIN_DEPTS,
)

def register_blueprints(app):
    # General blueprints
    from .auth_routes import auth_bp
    app.register_blueprint(auth_bp, url_prefix="/api/auth")

    # Super Admin auth blueprints
    from .superadmin_auth import super_admin_bp
    app.register_blueprint(super_admin_bp, url_prefix="/api/superadmin")

    # Board Workspace blueprints (internal staff/teachers/superadmin; public forms & Jotform whitelisted)
    from .board_routes import board_bp
    protect_blueprint(board_bp, allowed_roles=INTERNAL_ROLES, public_endpoints={'jotform_webhook'})
    app.register_blueprint(board_bp, url_prefix="/api/boards")

    from .board_extensions_routes import board_extensions_bp
    protect_blueprint(
        board_extensions_bp,
        allowed_roles=INTERNAL_ROLES,
        public_endpoints={'upload_public_form_file', 'get_public_form_details', 'submit_public_form_response'},
    )
    app.register_blueprint(board_extensions_bp, url_prefix="/api/board-extensions")
    app.register_blueprint(board_extensions_bp, url_prefix="/api/board_extensions", name="board_extensions_compat_bp")

    from .overview_routes import overview_bp
    protect_blueprint(overview_bp, allowed_roles=INTERNAL_ROLES)
    app.register_blueprint(overview_bp, url_prefix="/api")

    # Core Admin blueprints
    from .dashboard_routes import dashboard_bp
    protect_blueprint(dashboard_bp, allowed_roles=INTERNAL_ROLES)
    app.register_blueprint(dashboard_bp, url_prefix="/api/dashboard")

    from .department_routes import department_bp
    protect_blueprint(
        department_bp,
        allowed_roles={'superadmin'},
        read_allowed_roles=INTERNAL_ROLES,
    )
    app.register_blueprint(department_bp, url_prefix="/api/departments")

    from .staff_routes import staff_bp
    protect_blueprint(
        staff_bp,
        allowed_roles={'superadmin', 'staff', 'admin'},
        department_patterns=IT_ADMIN_DEPTS,
        allow_payroll_admin=True,
        read_allowed_roles=INTERNAL_ROLES,
    )
    app.register_blueprint(staff_bp, url_prefix="/api/staff")

    from .activity_routes import activity_bp
    protect_blueprint(activity_bp, allowed_roles={'superadmin', 'staff', 'admin'})
    app.register_blueprint(activity_bp, url_prefix="/api/activity")

    from .notification_routes import notification_bp
    protect_blueprint(notification_bp, allowed_roles=INTERNAL_ROLES | {'parent'})
    app.register_blueprint(notification_bp, url_prefix="/api/notifications")

    from .messaging_routes import messaging_bp
    protect_blueprint(messaging_bp, allowed_roles=INTERNAL_ROLES)
    app.register_blueprint(messaging_bp, url_prefix="/api/messaging")

    from .push_routes import push_bp
    protect_blueprint(push_bp, allowed_roles=INTERNAL_ROLES | {'parent'})
    app.register_blueprint(push_bp, url_prefix="/api/push")
    
    # Departmental blueprints
    from .admissions_routes import admissions_bp
    protect_blueprint(
        admissions_bp,
        allowed_roles={'superadmin', 'staff', 'admin'},
        department_patterns=ADMISSIONS_DEPTS,
        public_endpoints={'get_captcha_challenge', 'create_lead', 'create_live_look_in'},
    )
    app.register_blueprint(admissions_bp, url_prefix="/api/admissions")

    from .task_routes import task_bp
    protect_blueprint(task_bp, allowed_roles=INTERNAL_ROLES)
    app.register_blueprint(task_bp, url_prefix="/api/tasks")

    from .accounting_routes import accounting_bp
    protect_blueprint(
        accounting_bp,
        allowed_roles={'superadmin', 'staff', 'admin'},
        department_patterns=ACCOUNTING_DEPTS,
        allow_payroll_admin=True,
    )
    app.register_blueprint(accounting_bp, url_prefix="/api/accounting")

    from .administration_routes import administration_bp
    protect_blueprint(
        administration_bp,
        allowed_roles={'superadmin', 'staff', 'admin'},
        department_patterns=IT_ADMIN_DEPTS,
    )
    app.register_blueprint(administration_bp, url_prefix="/api/administration")

    from .profile_routes import profile_bp
    protect_blueprint(profile_bp, allowed_roles=INTERNAL_ROLES)
    app.register_blueprint(profile_bp, url_prefix="/api/profile")

    from .enrollment_routes import enrollment_bp
    protect_blueprint(
        enrollment_bp,
        allowed_roles={'superadmin', 'staff', 'admin'},
        department_patterns=ADMISSIONS_DEPTS,
        public_endpoints={
            'get_public_submission',
            'create_public_submission_payment_intent',
            'submit_public_form',
            'get_public_submission_view',
            'download_submission_contract_pdf',
        },
    )
    app.register_blueprint(enrollment_bp, url_prefix="/api/enrollment")

    from .student_routes import student_bp
    protect_blueprint(
        student_bp,
        allowed_roles={'superadmin', 'staff', 'admin'},
        read_allowed_roles=INTERNAL_ROLES,
        public_endpoints={'download_student_document'},
    )
    app.register_blueprint(student_bp, url_prefix="/api/students")

    from .billing_routes import billing_bp
    protect_blueprint(
        billing_bp,
        allowed_roles={'superadmin', 'staff', 'admin'},
        department_patterns=ACCOUNTING_DEPTS,
        allow_payroll_admin=True,
    )
    app.register_blueprint(billing_bp, url_prefix="/api/billing")

    from .subsidy_routes import subsidy_bp
    protect_blueprint(
        subsidy_bp,
        allowed_roles={'superadmin', 'staff', 'admin'},
        department_patterns=ACCOUNTING_DEPTS,
        allow_payroll_admin=True,
    )
    app.register_blueprint(subsidy_bp, url_prefix="/api/subsidies")

    from .parent_routes import parent_bp
    app.register_blueprint(parent_bp, url_prefix="/api/parent")

    from .stripe_webhook_routes import stripe_webhook_bp
    app.register_blueprint(stripe_webhook_bp, url_prefix="/api/stripe")

    from .report_routes import report_bp
    protect_blueprint(
        report_bp,
        allowed_roles={'superadmin', 'staff', 'admin'},
        department_patterns=ACCOUNTING_DEPTS,
        allow_payroll_admin=True,
    )
    app.register_blueprint(report_bp)

    from .payroll_routes import payroll_bp
    protect_blueprint(payroll_bp, allowed_roles=INTERNAL_ROLES)
    app.register_blueprint(payroll_bp, url_prefix="/api/payroll")