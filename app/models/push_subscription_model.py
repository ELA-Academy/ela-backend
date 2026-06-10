from app.models import db

class PushSubscription(db.Model):
    __tablename__ = 'push_subscriptions'

    id = db.Column(db.Integer, primary_key=True)
    subscription_json = db.Column(db.Text, nullable=False)
    
    # Use unique=True to ensure one subscription per user
    staff_id = db.Column(db.Integer, db.ForeignKey('staff.id'), nullable=True, unique=True)
    super_admin_id = db.Column(db.Integer, db.ForeignKey('super_admins.id'), nullable=True, unique=True)
    
    staff = db.relationship('Staff')
    super_admin = db.relationship('SuperAdmin')