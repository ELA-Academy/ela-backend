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

from flask import Flask, jsonify, send_from_directory
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
    
    # For single-node hosting, we don't need a Redis message queue for Socket.IO event propagation
    # (Socket.IO broadcasts directly to all connected users on this node). We keep Redis active for fast preference caching.
    socketio.init_app(app, cors_allowed_origins=origins)
        
    init_db(app)
    Migrate(app, db)
    
    # Start background reminders scheduler
    from app.utils.reminders_worker import start_reminders_scheduler
    start_reminders_scheduler(app)
    
    # ... (rest of your app setup) ...
    @jwt.expired_token_loader
    def expired_token_callback(jwt_header, jwt_payload): return jsonify({"message": "Token has expired", "error": "token_expired"}), 401
    @jwt.invalid_token_loader
    def invalid_token_callback(error): return jsonify({"message": "Signature verification failed", "error": "invalid_token"}), 401
    @jwt.unauthorized_loader
    def missing_token_callback(error): return jsonify({"message": "Request does not contain an access token", "error": "authorization_required"}), 401

    from app.routes import register_blueprints
    register_blueprints(app)

    @app.route('/')
    def home():
        return {"message": "School Management API is running successfully!"}

    @app.route('/api/static/<path:filename>')
    def serve_static_fallback(filename):
        return send_from_directory(app.static_folder, filename)

    return app