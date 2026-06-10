import os
from flask import Flask, jsonify
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from flask_mail import Mail
from flask_migrate import Migrate
from app.models import db, init_db
from app.config import DevelopmentConfig, ProductionConfig
from dotenv import load_dotenv

# This logic remains the same
if os.getenv('FLASK_ENV') == 'production':
    dotenv_path = os.path.join(os.path.dirname(__file__), '..', 'production.env')
    load_dotenv(dotenv_path=dotenv_path)
else:
    load_dotenv()

mail = Mail()

def create_app():
    app = Flask(__name__)

    if os.getenv('FLASK_ENV') == 'production':
        app.config.from_object(ProductionConfig)
    else:
        app.config.from_object(DevelopmentConfig)

    # The resource path must match all nested API routes.
    # The pattern r"/api/*" allows any path that starts with /api/.
    origins = os.getenv('CORS_ORIGINS', 'http://localhost:5173').split(',')
    
    CORS(
        app, 
        resources={r"/api/*": {"origins": origins}}, 
        supports_credentials=True,
        allow_headers=["Authorization", "Content-Type"]
    )
    # --- END OF FINAL FIX ---
    
    jwt = JWTManager(app)
    mail.init_app(app)
    init_db(app)
    Migrate(app, db)
    
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

    return app