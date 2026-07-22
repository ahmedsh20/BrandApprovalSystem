from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

db = SQLAlchemy()

db = SQLAlchemy()

class AdminActivity(db.Model):

    id = db.Column(db.Integer, primary_key=True)

    admin_username = db.Column(db.String(100), nullable=False)

    action = db.Column(db.String(100), nullable=False)

    target = db.Column(db.String(255), nullable=False)

    timestamp = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

class Admin(db.Model):

    id = db.Column(db.Integer, primary_key=True)

    username = db.Column(
        db.String(100),
        unique=True,
        nullable=False
    )

    password_hash = db.Column(
        db.String(255),
        nullable=False
    )

    role = db.Column(
        db.String(20),
        default="Admin"
    )

    can_review = db.Column(
        db.Boolean,
        default=False
    )

    can_edit = db.Column(
    db.Boolean,
    default=False
    )

    active = db.Column(
        db.Boolean,
        default=True
    )

    def check_password(self, password):

        return check_password_hash(
            self.password_hash,
            password
        )    
class RegistrationRequest(db.Model):

    id = db.Column(db.Integer, primary_key=True)

    username = db.Column(db.String(50), unique=True, nullable=False)

    password_hash = db.Column(db.String(255), nullable=False)

class BrandSubmission(db.Model):

    id = db.Column(db.Integer, primary_key=True)

    student_name = db.Column(db.String(100), nullable=False)
    bue_id = db.Column(db.String(30), nullable=False)

    brand_name = db.Column(db.String(100), nullable=False)
    social_link = db.Column(db.String(300), nullable=False)

    category = db.Column(db.String(100), nullable=False)

    phone_number = db.Column(db.String(30), nullable=False)

    contact_name = db.Column(db.String(100), nullable=False)

    contact_position = db.Column(db.String(100), nullable=False)

    status = db.Column(db.String(20), default="Pending")
