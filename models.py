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

    # Used only for password reset. Accounts created before this
    # column existed have no email until the admin adds one.
    email = db.Column(db.String(150))
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):

        return check_password_hash(
            self.password_hash,
            password
        )    
    
class RegistrationRequest(db.Model):

    id = db.Column(db.Integer, primary_key=True)

    username = db.Column(db.String(50), unique=True, nullable=False)

    password_hash = db.Column(db.String(255), nullable=False)

    email = db.Column(db.String(150))

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

        # When the request was submitted (stored in UTC).
    # Requests created before this column existed have no value.
    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    # Feedback belongs to the request, so it moves with the request
    # when the Master transfers it, and is removed if it is deleted.
    feedbacks = db.relationship(
        "Feedback",
        backref="submission",
        cascade="all, delete-orphan",
        order_by="Feedback.created_at.desc()"
    )

    class Feedback(db.Model):

        id = db.Column(db.Integer, primary_key=True)

        submission_id = db.Column(
            db.Integer,
            db.ForeignKey("brand_submission.id"),
            nullable=False
        )

        message = db.Column(db.Text, nullable=False)

        created_at = db.Column(
            db.DateTime,
            default=datetime.utcnow
        )
