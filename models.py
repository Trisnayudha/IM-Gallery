# models.py

from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class Photo(db.Model):
    __tablename__ = "photo"

    id = db.Column(db.Integer, primary_key=True)
    uuid = db.Column(db.String(100), unique=True, nullable=False)
    folder_id = db.Column(db.Integer, db.ForeignKey('folder.id'), nullable=False)
    folder = db.relationship('Folder', backref='photos')
    original_key = db.Column(db.String(255), nullable=True)
    medium_key = db.Column(db.String(255), nullable=True)
    low_key = db.Column(db.String(255), nullable=True)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Photo {self.id} | {self.uuid}>"

class Folder(db.Model):
    __tablename__ = "folder"

    id = db.Column(db.Integer, primary_key=True)
    path = db.Column(db.String(1024), nullable=False, unique=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Folder {self.path}>"
