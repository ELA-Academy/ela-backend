from app.models import db
from datetime import datetime

class ActivityLog(db.Model):
    __tablename__ = 'activity_logs'

    id = db.Column(db.Integer, primary_key=True)
    
    # --- THIS IS THE FIX ---
    # The actor can be a Staff, SuperAdmin, or System.
    # We remove the strict foreign key to `staff.id`.
    actor_id = db.Column(db.Integer, nullable=True) 
    actor_type = db.Column(db.String(50), nullable=True) # e.g., 'Staff', 'SuperAdmin'
    # --- END OF FIX ---
    
    actor_name = db.Column(db.String(100)) # Store name for convenience
    action = db.Column(db.String(255), nullable=False)
    target_type = db.Column(db.String(50), nullable=True)
    target_id = db.Column(db.Integer, nullable=True)
    target_name = db.Column(db.String(150), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'actor_name': self.actor_name,
            'action': self.action,
            'target_type': self.target_type,
            'target_name': self.target_name,
            'created_at': self.created_at.isoformat() + 'Z',
        }

# --- UPDATED HELPER FUNCTION ---
def log_activity(actor, action, target=None):
    """
    Creates and saves an activity log entry.
    'actor' can be a Staff or SuperAdmin object, or None.
    'action' is a string describing the event.
    'target' is the database object the action was performed on (optional).
    """
    if not actor:
        actor_id = None
        actor_type = "System"
        actor_name = "System"
    else:
        actor_id = actor.id
        actor_type = actor.__class__.__name__ # This will be 'Staff' or 'SuperAdmin'
        actor_name = actor.name
    
    target_type = target.__class__.__name__ if target else None
    target_id = target.id if target else None
    
    target_name = None
    if target:
        if hasattr(target, 'name'):
            target_name = target.name
        elif hasattr(target, 'title'):
            target_name = target.title
        elif hasattr(target, 'students') and target.students: # For Leads
            target_name = f"{target.students[0].first_name} {target.students[0].last_name}"

    new_log = ActivityLog(
        actor_id=actor_id,
        actor_type=actor_type,
        actor_name=actor_name,
        action=action,
        target_type=target_type,
        target_id=target_id,
        target_name=target_name
    )
    db.session.add(new_log)
    # The calling function will need to commit the session