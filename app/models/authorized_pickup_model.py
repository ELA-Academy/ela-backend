from app.models import db

class AuthorizedPickup(db.Model):
    __tablename__ = 'authorized_pickups'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    relationship = db.Column(db.String(50), nullable=False)
    contact_number = db.Column(db.String(20), nullable=False)
    lead_id = db.Column(db.Integer, db.ForeignKey('leads.id'), nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'relationship': self.relationship,
            'contact_number': self.contact_number
        }