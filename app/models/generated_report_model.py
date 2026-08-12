from app.models import db
from datetime import datetime

class GeneratedReport(db.Model):
    __tablename__ = 'generated_reports'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    category = db.Column(db.String(100), nullable=False)
    format = db.Column(db.String(50), nullable=False)  # 'XLSX' or 'PDF'
    file_path = db.Column(db.String(255), nullable=False)
    date_range = db.Column(db.String(100), nullable=False)  # e.g., "06/01/2026 - 06/30/2026"
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by_id = db.Column(db.Integer, nullable=True)
    created_by_role = db.Column(db.String(50), nullable=True)

    def to_dict(self):
        creator_name = "System"
        try:
            if self.created_by_role == 'staff':
                from app.models.staff_model import Staff
                staff = Staff.query.get(self.created_by_id)
                if staff:
                    creator_name = staff.name
            elif self.created_by_role == 'superadmin':
                from app.models.super_admin_model import SuperAdmin
                admin = SuperAdmin.query.get(self.created_by_id)
                if admin:
                    creator_name = admin.name
        except Exception:
            pass

        return {
            'id': self.id,
            'name': self.name,
            'category': self.category,
            'format': self.format,
            'file_path': self.file_path,
            'date_range': self.date_range,
            'created_at': self.created_at.isoformat() + 'Z',
            'created_by_id': self.created_by_id,
            'created_by_role': self.created_by_role,
            'creator_name': creator_name
        }
