import json
from app.models import db
from datetime import datetime


class OverviewCard(db.Model):
    """Persistent overview card for a space dashboard — ClickUp-style."""
    __tablename__ = 'overview_cards'

    id = db.Column(db.Integer, primary_key=True)
    board_id = db.Column(db.Integer, db.ForeignKey('boards.id', ondelete='CASCADE'), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    card_type = db.Column(db.String(50), nullable=False)  # 'calculation', 'recent', 'docs', 'bookmarks', 'folders'
    position = db.Column(db.Integer, default=0)

    # For calculation cards
    data_source_board_id = db.Column(db.Integer, db.ForeignKey('boards.id', ondelete='SET NULL'), nullable=True)
    measure_field_id = db.Column(db.Integer, db.ForeignKey('board_custom_fields.id', ondelete='SET NULL'), nullable=True)
    calculation = db.Column(db.String(50), nullable=True)  # 'sum', 'count', 'average', 'min', 'max'
    units = db.Column(db.String(50), nullable=True)  # 'None', '$', '€', '#', '%'
    filters_json = db.Column(db.Text, nullable=True)  # JSON: {show_closed, show_archived}

    created_by_staff_id = db.Column(db.Integer, db.ForeignKey('staff.id', ondelete='SET NULL'), nullable=True)
    created_by_super_admin_id = db.Column(db.Integer, db.ForeignKey('super_admins.id', ondelete='SET NULL'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    board = db.relationship('Board', foreign_keys=[board_id])
    data_source_board = db.relationship('Board', foreign_keys=[data_source_board_id])

    def to_dict(self):
        filters_val = None
        if self.filters_json:
            try:
                filters_val = json.loads(self.filters_json)
            except Exception:
                filters_val = None

        return {
            'id': self.id,
            'board_id': self.board_id,
            'name': self.name,
            'card_type': self.card_type,
            'position': self.position,
            'data_source_board_id': self.data_source_board_id,
            'data_source_name': self.data_source_board.name if self.data_source_board else None,
            'measure_field_id': self.measure_field_id,
            'calculation': self.calculation,
            'units': self.units,
            'filters': filters_val,
            'created_at': self.created_at.isoformat() + 'Z'
        }


class SpaceBookmark(db.Model):
    """Per-user bookmark on a space overview."""
    __tablename__ = 'space_bookmarks'

    id = db.Column(db.Integer, primary_key=True)
    board_id = db.Column(db.Integer, db.ForeignKey('boards.id', ondelete='CASCADE'), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    url = db.Column(db.String(1024), nullable=True)
    bookmark_type = db.Column(db.String(50), default='url')  # 'url', 'task', 'doc', 'list'
    target_id = db.Column(db.Integer, nullable=True)  # Board/task/doc ID if internal

    # Per-user: only the user who created it sees it
    staff_id = db.Column(db.Integer, db.ForeignKey('staff.id', ondelete='CASCADE'), nullable=True)
    super_admin_id = db.Column(db.Integer, db.ForeignKey('super_admins.id', ondelete='CASCADE'), nullable=True)

    position = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'board_id': self.board_id,
            'title': self.title,
            'url': self.url,
            'bookmark_type': self.bookmark_type,
            'target_id': self.target_id,
            'position': self.position,
            'created_at': self.created_at.isoformat() + 'Z'
        }
