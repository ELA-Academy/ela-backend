import os
from app import create_app, socketio
from app.scheduled_tasks import register_commands

app = create_app()
register_commands(app)

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port)