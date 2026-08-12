from flask import Blueprint

def register_blueprints(app):
    # General blueprints
    from .auth_routes import auth_bp
    app.register_blueprint(auth_bp, url_prefix="/api/auth")

    # Super Admin auth blueprints
    from .superadmin_auth import super_admin_bp
    app.register_blueprint(super_admin_bp, url_prefix="/api/superadmin")

    # Board Workspace blueprints
    from .board_routes import board_bp
    app.register_blueprint(board_bp, url_prefix="/api/boards")
    from .board_extensions_routes import board_extensions_bp
    app.register_blueprint(board_extensions_bp, url_prefix="/api/board-extensions")
    from .overview_routes import overview_bp
    app.register_blueprint(overview_bp, url_prefix="/api")

    # Core Admin blueprints
    from .dashboard_routes import dashboard_bp
    app.register_blueprint(dashboard_bp, url_prefix="/api/dashboard")
    from .department_routes import department_bp
    app.register_blueprint(department_bp, url_prefix="/api/departments")
    from .staff_routes import staff_bp
    app.register_blueprint(staff_bp, url_prefix="/api/staff")
    from .activity_routes import activity_bp
    app.register_blueprint(activity_bp, url_prefix="/api/activity")
    from .notification_routes import notification_bp
    app.register_blueprint(notification_bp, url_prefix="/api/notifications")
    from .messaging_routes import messaging_bp
    app.register_blueprint(messaging_bp, url_prefix="/api/messaging")
    from .push_routes import push_bp
    app.register_blueprint(push_bp, url_prefix="/api/push")
    
    # Departmental blueprints
    from .admissions_routes import admissions_bp
    app.register_blueprint(admissions_bp, url_prefix="/api/admissions")
    from .task_routes import task_bp
    app.register_blueprint(task_bp, url_prefix="/api/tasks")
    from .accounting_routes import accounting_bp
    app.register_blueprint(accounting_bp, url_prefix="/api/accounting")
    from .administration_routes import administration_bp
    app.register_blueprint(administration_bp, url_prefix="/api/administration")
    from .profile_routes import profile_bp
    app.register_blueprint(profile_bp, url_prefix="/api/profile")
    from .enrollment_routes import enrollment_bp
    app.register_blueprint(enrollment_bp, url_prefix="/api/enrollment")
    from .student_routes import student_bp
    app.register_blueprint(student_bp, url_prefix="/api/students")
    from .billing_routes import billing_bp
    app.register_blueprint(billing_bp, url_prefix="/api/billing")
    from .subsidy_routes import subsidy_bp
    app.register_blueprint(subsidy_bp, url_prefix="/api/subsidies")
    from .report_routes import report_bp
    app.register_blueprint(report_bp)