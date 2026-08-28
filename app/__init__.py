try:
    import eventlet
    eventlet.monkey_patch()
except Exception:
    pass

import os
from dotenv import load_dotenv

# Load env variables before importing config or models
if os.getenv('FLASK_ENV') == 'production':
    dotenv_path = os.path.join(os.path.dirname(__file__), '..', 'production.env')
    load_dotenv(dotenv_path=dotenv_path)
elif os.getenv('FLASK_ENV') == 'staging':
    dotenv_path = os.path.join(os.path.dirname(__file__), '..', 'staging.env')
    load_dotenv(dotenv_path=dotenv_path)
else:
    load_dotenv()

from flask import Flask, jsonify, send_from_directory, request
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from flask_mail import Mail
from flask_migrate import Migrate
from app.models import db, init_db
from app.config import DevelopmentConfig, ProductionConfig, StagingConfig

from flask_socketio import SocketIO

mail = Mail()
socketio = SocketIO()

def create_app():
    app = Flask(__name__)
    app.url_map.strict_slashes = False

    if os.getenv('FLASK_ENV') == 'production':
        app.config.from_object(ProductionConfig)
    elif os.getenv('FLASK_ENV') == 'staging':
        app.config.from_object(StagingConfig)
    else:
        app.config.from_object(DevelopmentConfig)

    # The resource path must match all nested API routes.
    # The pattern r"/api/*" allows any path that starts with /api/.
    cors_origins_env = os.getenv('CORS_ORIGINS', 'http://localhost:5173')
    origins = [o.strip().strip("'").strip('"') for o in cors_origins_env.split(',')]

    CORS(
        app, 
        resources={r"/*": {"origins": origins}}, 
        supports_credentials=True,
        allow_headers="*"
    )
    
    jwt = JWTManager(app)
    mail.init_app(app)
    
    # Enable Redis as Socket.IO message queue for cross-process notification delivery.
    # The process-notifications worker runs as a separate daemon; without a message queue,
    # socketio.emit() from the worker never reaches connected browser clients.
    redis_mq = os.getenv('REDIS_URL')
    if redis_mq:
        try:
            socketio.init_app(app, cors_allowed_origins=origins, message_queue=redis_mq)
        except Exception as e:
            app.logger.warning(f"SocketIO message_queue init warning: {e}")
            socketio.init_app(app, cors_allowed_origins=origins)
    else:
        socketio.init_app(app, cors_allowed_origins=origins)
        
    init_db(app)
    Migrate(app, db)
    
    # Start background reminders scheduler and notification processor
    from app.utils.reminders_worker import start_reminders_scheduler, start_notification_processor
    start_reminders_scheduler(app)
    start_notification_processor(app)
    
    # ... (rest of your app setup) ...
    @jwt.expired_token_loader
    def expired_token_callback(jwt_header, jwt_payload): return jsonify({"message": "Token has expired", "error": "token_expired"}), 401
    @jwt.invalid_token_loader
    def invalid_token_callback(error): return jsonify({"message": "Signature verification failed", "error": "invalid_token"}), 401
    @jwt.unauthorized_loader
    def missing_token_callback(error): return jsonify({"message": "Request does not contain an access token", "error": "authorization_required"}), 401

    @jwt.token_in_blocklist_loader
    def check_if_token_revoked(jwt_header, jwt_payload):
        identity = jwt_payload.get('sub')
        role = jwt_payload.get('role')
        if not identity or not role:
            return True
            
        from app.models.super_admin_model import SuperAdmin
        from app.models.staff_model import Staff
        
        if role == 'superadmin':
            user = SuperAdmin.query.filter_by(email=identity).first()
            if not user or not getattr(user, 'is_active', True):
                return True
        elif role == 'staff':
            user = Staff.query.filter_by(email=identity).first()
            if not user or not getattr(user, 'is_active', True):
                return True
        elif role == 'parent':
            from app.models.student_model import Parent
            user = Parent.query.filter(db.func.lower(Parent.email) == db.func.lower(identity)).first()
            if not user or not getattr(user, 'is_active', True):
                return True
        else:
            return True
            
        return False

    @jwt.revoked_token_loader
    def revoked_token_callback(jwt_header, jwt_payload):
        return jsonify({"message": "Token has been revoked or is invalid for this role", "error": "token_revoked"}), 401

    from app.routes import register_blueprints
    register_blueprints(app)

    @app.route('/')
    def home():
        return {"message": "School Management API is running successfully!"}

    @app.route('/api/static/<path:filename>')
    def serve_static_fallback(filename):
        return send_from_directory(app.static_folder, filename)

    @app.before_request
    def sanitize_all_json_inputs():
        if request.is_json:
            try:
                from app.utils.sanitizer import sanitize_dict
                data = request.get_json(silent=True)
                if data is not None:
                    sanitized = sanitize_dict(data)
                    # Override Flask's internal cached JSON parsing representation
                    request._cached_json = (sanitized, sanitized)
            except Exception as e:
                app.logger.warning(f"Global JSON sanitization warning: {e}")

    return app