from flask import Flask, render_template, request, redirect, session, send_file, flash, jsonify, url_for
from models import db, BrandSubmission, Admin, RegistrationRequest, AdminActivity, Feedback
from werkzeug.security import generate_password_hash
from sqlalchemy import or_, case, func, inspect, text
from io import BytesIO
from openpyxl import Workbook
from zoneinfo import ZoneInfo
from datetime import timedelta, timezone
import os
import re
import hashlib
import smtplib
from email.message import EmailMessage
from itsdangerous import URLSafeTimedSerializer, BadSignature
import webbrowser
import threading
import atexit

LOCK_FILE = ".browser_opened"

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "development-secret-key")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///database.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

MAX_FEEDBACK_LENGTH = 2000

# Times are stored in UTC and shown in local (Cairo) time.
# If the time zone database is missing (e.g. Windows without the
# "tzdata" package) we fall back to a fixed UTC+3 offset.
try:
    LOCAL_TZ = ZoneInfo("Africa/Cairo")
except Exception:
    LOCAL_TZ = None

def to_local(dt):

    if dt is None:
        return None

    if LOCAL_TZ:
        return dt.replace(tzinfo=timezone.utc).astimezone(LOCAL_TZ)

    return dt + timedelta(hours=3)

@app.template_filter("localtime")
def localtime_filter(dt, fmt="%d/%m/%Y %I:%M %p"):

    local = to_local(dt)

    if local is None:
        return ""

    return local.strftime(fmt)

def ensure_schema():
    # Creates any missing tables (e.g. Feedback) and adds the
    # created_at column to an existing brand_submission table.
    # Safe to run every time; existing data is never touched.
    try:
        with app.app_context():

            db.create_all()

            # Columns added after the first version of the database.
            new_columns = {
                "brand_submission": [("created_at", "TIMESTAMP")],
                "admin": [("email", "VARCHAR(150)")],
                "registration_request": [("email", "VARCHAR(150)")]
            }

            inspector = inspect(db.engine)

            for table, columns in new_columns.items():

                existing = [
                    c["name"] for c in inspector.get_columns(table)
                ]

                for name, sql_type in columns:

                    if name not in existing:
                        with db.engine.begin() as connection:
                            connection.execute(text(
                                'ALTER TABLE "%s" ADD COLUMN %s %s'
                                % (table, name, sql_type)
                            ))
                            
    except Exception as error:
        print("Schema check failed:", error)

ensure_schema()

# ---------- password reset helpers ----------

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

RESET_TOKEN_MAX_AGE = 30 * 60   # reset links work for 30 minutes

def reset_serializer():
    return URLSafeTimedSerializer(app.secret_key, salt="admin-password-reset")

def password_fingerprint(admin):
    # Changes whenever the password changes, so a reset link can only be
    # used once and old logins stop working after a reset.
    return hashlib.sha256(admin.password_hash.encode()).hexdigest()[:20]

def make_reset_token(admin):
    return reset_serializer().dumps({
        "id": admin.id,
        "fp": password_fingerprint(admin)
    })

def admin_from_reset_token(token):

    try:
        data = reset_serializer().loads(token, max_age=RESET_TOKEN_MAX_AGE)
    except BadSignature:   # also covers expired links
        return None

    admin = db.session.get(Admin, data.get("id"))

    if not admin or not admin.active:
        return None

    if data.get("fp") != password_fingerprint(admin):
        return None

    return admin

def send_email(to_address, subject, body):

    username = os.environ.get("MAIL_USERNAME")
    password = os.environ.get("MAIL_PASSWORD")

    if not username or not password:
        print("Email not sent: MAIL_USERNAME / MAIL_PASSWORD are not set.")
        if app.debug:
            print("Would have sent to", to_address, ":\n" + body)
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = os.environ.get("MAIL_FROM", username)
    message["To"] = to_address
    message.set_content(body)

    try:
        with smtplib.SMTP(
            os.environ.get("MAIL_SERVER", "smtp.gmail.com"),
            int(os.environ.get("MAIL_PORT", "587")),
            timeout=15
        ) as smtp:
            smtp.starttls()
            smtp.login(username, password)
            smtp.send_message(message)
        return True

    except Exception as error:
        print("Could not send email:", error)
        return False

def current_admin():

    admin_id = session.get("admin_id")

    if not admin_id:
        return None

    admin = db.session.get(Admin, admin_id)

    if not admin:
        return None

    if not admin.active:

        session.clear()

        return None

    return admin

def require_master():

    admin = current_admin()

    if not admin:
        return None

    if admin.role != "Master":
        return None

    return admin

@app.before_request
def enforce_admin_session():

    # Runs on every request. If the logged-in admin was deactivated
    # or deleted, the session is closed immediately.
    if request.endpoint in (None, "static", "session_check"):
        return

    admin_id = session.get("admin_id")

    if not admin_id:
        return

    admin = db.session.get(Admin, admin_id)

    if not admin or not admin.active:

        reason = "removed" if not admin else "deactivated"

        session.clear()

        # A login attempt with a stale cookie should still go through.
        if request.endpoint == "admin" and request.method == "POST":
            return

        return redirect("/login?reason=" + reason)

    # A password change (e.g. a reset) ends every older login.
    fingerprint = password_fingerprint(admin)

    if session.get("pw_fp") is None:
        session["pw_fp"] = fingerprint

    elif session.get("pw_fp") != fingerprint:
        session.clear()
        return redirect("/login?reason=expired")

    # Keep the cached values in the session in sync with the database
    # so permission changes apply without waiting for a new login.
    fresh = {
        "admin_role": admin.role,
        "admin_username": admin.username,
        "can_review": admin.role == "Master" or bool(admin.can_review),
        "can_edit": admin.role == "Master" or bool(admin.can_edit)
    }

    for key, value in fresh.items():
        if session.get(key) != value:
            session[key] = value

@app.route("/admin/session-check")
def session_check():

    # Polled by open admin pages (see base.html) so a deactivated
    # admin is sent to the login page without having to click.
    admin_id = session.get("admin_id")

    if not admin_id:
        response = jsonify(ok=False, reason="expired")

    else:

        admin = db.session.get(Admin, admin_id)

        if not admin:
            session.clear()
            response = jsonify(ok=False, reason="removed")

        elif not admin.active:
            session.clear()
            response = jsonify(ok=False, reason="deactivated")

        else:
            response = jsonify(ok=True)

    response.headers["Cache-Control"] = "no-store"

    return response

@app.route("/", methods=["GET", "POST"])
def home():

    if request.method == "POST":

        student_name = request.form["student_name"]
        bue_id = request.form["bue_id"].strip()
        brand_name = request.form["brand_name"]
        social_link = request.form["social_link"]
        category = request.form["category"]
        phone_number = request.form["phone_number"]
        contact_name = request.form["contact_name"]
        contact_position = request.form["contact_position"]

        new_submission = BrandSubmission(
            student_name=student_name,
            bue_id=bue_id,
            brand_name=brand_name,
            social_link=social_link,
            category=category,
            phone_number=phone_number,
            contact_name=contact_name,
            contact_position=contact_position
        )

        db.session.add(new_submission)
        db.session.commit()

        print("Submission saved successfully!")
        return redirect("/success")
    
    return render_template("index.html")

@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        username = request.form["username"]

        email = request.form.get("email", "").strip()

        password = request.form["password"]

        confirm = request.form["confirm_password"]

        if not EMAIL_PATTERN.match(email) or len(email) > 150:

            return render_template(
                "register.html",
                error="Please enter a valid email address."
            )

        if password != confirm:

            return render_template(
                "register.html",
                error="Passwords do not match."
            )

        if Admin.query.filter_by(username=username).first():

            return render_template(
                "register.html",
                error="this username is already registered."
            )

        if RegistrationRequest.query.filter_by(username=username).first():

            return render_template(
                "register.html",
                error="Registration request already pending."
            )

        request_account = RegistrationRequest(
            username=username,
            email=email,
            password_hash=generate_password_hash(password)
        )
        db.session.add(request_account)

        db.session.commit()

        return redirect("/registration-pending")

    return render_template(
        "register.html",
        error=None
    )

@app.route("/registration-pending")
def registration_pending():

    return render_template("registration_pending.html")

@app.route("/login", methods=["GET", "POST"])
def admin():

    if request.method == "POST":

        username = request.form["username"]
        password = request.form["password"]

        admin = Admin.query.filter_by(username=username).first()

        if admin:

            if admin.check_password(password):
                if not admin.active:

                    return render_template(
                        "admin_login.html",
                        error="This account has been disabled by the Master administrator."
                    )
                session["admin_logged_in"] = True
                session["admin_id"] = admin.id
                session["admin_role"] = admin.role
                session["admin_username"] = admin.username
                session["can_review"] = (
                    admin.role == "Master" or admin.can_review
                )
                session["can_edit"] = (
                    admin.role == "Master" or admin.can_edit
                )

                return redirect("/admin/dashboard")


            return render_template(
                "admin_login.html",
                error="Invalid username or password."
            )


        pending = RegistrationRequest.query.filter_by(
        username=username
        ).first()

        if pending:

            from werkzeug.security import check_password_hash

            if check_password_hash(
                pending.password_hash,
                    password
            ):

                return render_template(
                    "admin_login.html",
                    pending=True
                )


        return render_template(
            "admin_login.html",
           error="Invalid username or password."
        )

    reasons = {
        "deactivated": "Your account has been deactivated by the Master administrator. You have been signed out.",
        "removed": "Your account no longer exists. You have been signed out.",
        "expired": "Your session has ended. Please log in again."
    }

    success = None

    if request.args.get("reset") == "1":
        success = "Your password has been changed. You can now log in."

    return render_template(
        "admin_login.html",
        error=reasons.get(request.args.get("reason")),
        success=success
    )

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():

    sent = False

    if request.method == "POST":

        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()

        admin = Admin.query.filter_by(username=username).first()

        # The link is only ever sent to the email saved on the account,
        # never to whatever address was typed into this form.
        if (
            admin
            and admin.active
            and admin.email
            and admin.email.strip().lower() == email
        ):

            base_url = os.environ.get("SITE_URL", "").rstrip("/")

            path = url_for("reset_password", token=make_reset_token(admin))

            link = (base_url + path) if base_url else request.host_url.rstrip("/") + path

            send_email(
                admin.email,
                "Reset your admin password",
                "Hello " + admin.username + ",\n\n"
                "Use the link below to choose a new password. "
                "It works once and expires in 30 minutes.\n\n"
                + link + "\n\n"
                "If you did not ask for this, ignore this email; "
                "your password will not change.\n"
            )

        # Same answer whether or not the details matched, so nobody can
        # use this page to find out which usernames or emails exist.
        sent = True

    return render_template("forgot_password.html", sent=sent)

@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):

    admin = admin_from_reset_token(token)

    if not admin:
        return render_template("reset_password.html", invalid=True)

    error = None

    if request.method == "POST":

        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        if password != confirm:
            error = "Passwords do not match."

        elif len(password) < 8:
            error = "Password must be at least 8 characters."

        else:

            admin.set_password(password)

            db.session.commit()

            return redirect("/login?reset=1")

    return render_template("reset_password.html", invalid=False, error=error)

@app.route("/admin/account", methods=["GET", "POST"])
def admin_account():

    admin = current_admin()

    if not admin:
        return redirect("/login")

    error = None
    success = None

    if request.method == "POST":

        email = request.form.get("email", "").strip()
        current_password = request.form.get("current_password", "")

        if not admin.check_password(current_password):
            error = "Current password is incorrect."

        elif not EMAIL_PATTERN.match(email) or len(email) > 150:
            error = "Please enter a valid email address."

        else:
            admin.email = email
            db.session.commit()
            success = "Your email has been saved."

    return render_template(
        "admin_account.html",
        admin=admin,
        error=error,
        success=success
    )

@app.route("/admin")
def admin_home():

    admin = current_admin()

    if not admin:
        return redirect("/login")

    return redirect("/admin/dashboard")

@app.route("/admin/dashboard")
def admin_dashboard():

    admin = current_admin()

    if not admin:

        return redirect("/login")
     
    search = request.args.get("search", "").strip()
    sort = request.args.get("sort", "newest")
    status_filter = request.args.get("status", "")
    print("Search =", search)

    query = BrandSubmission.query

    if search:

        query = query.filter(

            or_(

                BrandSubmission.id.cast(db.String).ilike(f"%{search}%"),

                BrandSubmission.student_name.ilike(f"%{search}%"),

                BrandSubmission.brand_name.ilike(f"%{search}%"),

                BrandSubmission.status.ilike(f"%{search}%")
            )

        )

    if sort == "newest":

        query = query.order_by(BrandSubmission.id.desc())

    elif sort == "pending":

        query = query.order_by(
            case(
                (BrandSubmission.status == "Pending", 0),
                (BrandSubmission.status == "Accepted", 1),
                (BrandSubmission.status == "Rejected", 2)
            )
        )

    elif sort == "alphabetical":

        query = query.order_by(BrandSubmission.student_name.asc())

    total_submissions = BrandSubmission.query.count()

    pending_submissions = BrandSubmission.query.filter_by(
        status="Pending"
    ).count()

    accepted_submissions = BrandSubmission.query.filter_by(
        status="Accepted"
    ).count()

    rejected_submissions = BrandSubmission.query.filter_by(
        status="Rejected"
    ).count()

    if status_filter:

        query = query.filter(
            BrandSubmission.status == status_filter
        )

    submissions = query.all()

    return render_template(
        "admin_dashboard.html",
        submissions=submissions,
        admin=admin,
        search=search,
        sort=sort,
        total_submissions=total_submissions,
        pending_submissions=pending_submissions,
        accepted_submissions=accepted_submissions,
        rejected_submissions=rejected_submissions,
        status_filter=status_filter
    )

@app.route("/admin/pending-registrations")
def pending_registrations():

    admin = require_master()

    if not admin:
        return redirect("/admin/dashboard")

    requests = RegistrationRequest.query.all()

    return render_template(
        "pending_registrations.html",
        requests=requests
    )

@app.route("/admin/approve-registration/<int:id>", methods=["POST"])
def approve_registration(id):
    print("APPROVE ROUTE REACHED")

    admin = require_master()

    if not admin:
        return redirect("/admin/dashboard")

    request_account = RegistrationRequest.query.get_or_404(id)

    new_admin = Admin(
        username=request_account.username,
        password_hash=request_account.password_hash,
        email=request_account.email,
        role="Admin",
        can_review=False,
        active=True
    )

    db.session.add(new_admin)

    db.session.delete(request_account)

    db.session.commit()

    return redirect("/admin/pending-registrations")

@app.route("/admin/reject-registration/<int:id>", methods=["POST"])
def reject_registration(id):
    print("REJECT ROUTE REACHED")
    admin = require_master()

    if not admin:
        return redirect("/admin/dashboard")

    request_account = RegistrationRequest.query.get_or_404(id)

    db.session.delete(request_account)

    db.session.commit()

    return redirect("/admin/pending-registrations")

@app.route("/admin/review/<int:id>")

def review_submission(id):

    admin = current_admin()

    if not admin:
        
        return redirect("/login")
    
    submission = BrandSubmission.query.get_or_404(id)

    return render_template(
        "review.html",
        submission=submission
    )

@app.route("/admin/accept/<int:id>", methods=["POST"])
def accept_submission(id):

    admin = current_admin()

    if not admin:
        return redirect("/login")
    if admin.role != "Master" and not admin.can_review:
        return redirect("/admin/dashboard")

    submission = BrandSubmission.query.get_or_404(id)

    submission.status = "Accepted"
    activity = AdminActivity(
        admin_username=admin.username,
        action="Accepted",
        target=submission.brand_name
    )

    db.session.add(activity)
    
    db.session.commit()

    return redirect("/admin/dashboard")

@app.route("/admin/reject/<int:id>", methods=["POST"])
def reject_submission(id):

    admin = current_admin()

    if not admin:
        return redirect("/login")
    if admin.role != "Master" and not admin.can_review:
        return redirect("/admin/dashboard")

    submission = BrandSubmission.query.get_or_404(id)

    submission.status = "Rejected"
    activity = AdminActivity(
        admin_username=admin.username,
        action="Rejected",
        target=submission.brand_name
    )

    db.session.add(activity)

    db.session.commit()

    return redirect("/admin/dashboard")

@app.route("/admin/edit/<int:id>", methods=["GET", "POST"])
def edit_submission(id):

    admin = current_admin()

    if not admin:
        return redirect("/login")
    if admin.role != "Master":
        if not admin.can_review or not admin.can_edit:
            return redirect("/admin/dashboard")

    submission = BrandSubmission.query.get_or_404(id)

    if request.method == "POST":

        submission.status = request.form["status"]
        activity = AdminActivity(
            admin_username=admin.username,
            action="Edited",
            target=submission.brand_name
        )

        db.session.add(activity)
        db.session.commit()

        return redirect("/admin/review/" + str(id))

    return render_template(
        "edit_submission.html",
        submission=submission
    )

@app.route("/admin/manage-admins")
def manage_admins():

    admin = require_master()

    if not admin:
        return redirect("/admin/dashboard")

    admins = Admin.query.order_by(Admin.username).all()

    return render_template(
        "manage_admins.html",
        admins=admins
    )

@app.route("/admin/manage-admin/<int:id>", methods=["GET", "POST"])
def manage_admin(id):

    master = require_master()

    if not master:
        return redirect("/admin/dashboard")

    admin = Admin.query.get_or_404(id)

    # Never allow editing the Master account
    if admin.role == "Master":
        return redirect("/admin/manage-admins")

    if request.method == "POST":

        admin.can_review = "can_review" in request.form
        admin.can_edit = "can_edit" in request.form
        admin.active = "active" in request.form

        db.session.commit()

        return redirect("/admin/manage-admins")

    return render_template(
        "manage_admin.html",
        admin=admin
    )

@app.route("/admin/enable-admin/<int:admin_id>", methods=["POST"])
def enable_admin(admin_id):

    admin = current_admin()

    if not admin:
        return redirect("/login")

    if admin.role != "Master":
        return redirect("/admin/manage-admins")

    admin_to_enable = Admin.query.get_or_404(admin_id)

    admin_to_enable.active = True

    activity = AdminActivity(
        admin_username=admin.username,
        action="Enabled",
        target=admin_to_enable.username
    )

    db.session.add(activity)
    db.session.commit()

    return redirect("/admin/manage-admins")

@app.route("/logout")
def logout():

    session.clear()

    return redirect("/")

@app.route("/success")
def success():

    return render_template("success.html")

@app.route("/check-status", methods=["GET", "POST"])
def check_status():

    if request.method == "POST":

        # The BUE ID the student typed is remembered for this browser
        # session so the requests and feedback pages know whose they are.
        session["student_bue_id"] = request.form["bue_id"].strip()

        return redirect("/my-requests")

    return render_template("check_status.html")

def student_submission(submission_id):

    # Returns the request only if it belongs to the student
    # currently "signed in" by BUE ID, otherwise None.
    bue_id = session.get("student_bue_id")

    if not bue_id:
        return None

    submission = db.session.get(BrandSubmission, submission_id)

    if not submission or submission.bue_id != bue_id:
        return None

    return submission

@app.route("/my-requests")
def my_requests():

    bue_id = session.get("student_bue_id")

    if not bue_id:
        return redirect("/check-status")

    submissions = (
        BrandSubmission.query
        .filter_by(bue_id=bue_id)
        .order_by(
            case(
                (BrandSubmission.status == "Accepted", 0),
                (BrandSubmission.status == "Pending", 1),
                (BrandSubmission.status == "Rejected", 2),
                else_=3
            ),
            BrandSubmission.id.desc()
        ).all()
    )

    feedback_counts = {}

    if submissions:
        feedback_counts = dict(
            db.session.query(Feedback.submission_id, func.count(Feedback.id))
            .filter(Feedback.submission_id.in_([s.id for s in submissions]))
            .group_by(Feedback.submission_id)
            .all()
        )

    return render_template(
        "status_results.html",
        submissions=submissions,
        feedback_counts=feedback_counts
    )

@app.route("/my-requests/<int:submission_id>/feedback", methods=["GET", "POST"])
def post_feedback(submission_id):

    submission = student_submission(submission_id)

    if not submission:
        return redirect("/check-status")

    if submission.status != "Accepted":
        return redirect("/my-requests")

    error = None
    message = ""

    if request.method == "POST":

        message = request.form.get("message", "").strip()

        if not message:
            error = "Please write your feedback before submitting."

        elif len(message) > MAX_FEEDBACK_LENGTH:
            error = "Feedback is too long (maximum %d characters)." % MAX_FEEDBACK_LENGTH

        else:

            db.session.add(Feedback(
                submission_id=submission.id,
                message=message
            ))

            db.session.commit()

            return redirect(url_for(
                "feedback_submitted",
                submission_id=submission.id
            ))

    return render_template(
        "feedback_form.html",
        submission=submission,
        error=error,
        message=message,
        max_length=MAX_FEEDBACK_LENGTH
    )

@app.route("/my-requests/<int:submission_id>/feedback/submitted")
def feedback_submitted(submission_id):

    submission = student_submission(submission_id)

    if not submission:
        return redirect("/check-status")

    return render_template(
        "feedback_submitted.html",
        submission=submission
    )

@app.route("/my-requests/<int:submission_id>/feedbacks")
def show_feedbacks(submission_id):

    submission = student_submission(submission_id)

    if not submission:
        return redirect("/check-status")

    return render_template(
        "feedback_list.html",
        submission=submission,
        feedbacks=submission.feedbacks
    )

@app.route("/my-requests/<int:submission_id>/feedbacks/<int:feedback_id>")
def read_feedback(submission_id, feedback_id):

    submission = student_submission(submission_id)

    if not submission:
        return redirect("/check-status")

    feedback = Feedback.query.filter_by(
        id=feedback_id,
        submission_id=submission.id
    ).first_or_404()

    return render_template(
        "feedback_detail.html",
        submission=submission,
        feedback=feedback
    )

@app.route("/admin/transfer/<int:id>", methods=["GET", "POST"])
def transfer_request(id):

    master = require_master()

    if not master:
        return redirect("/admin/dashboard")

    submission = BrandSubmission.query.get_or_404(id)

    # Everyone who has posted a request: one entry per BUE ID, using the
    # name from their newest request. Built on every page load, so a
    # student who posts for the first time shows up automatically.
    students = {}

    rows = (
        db.session.query(BrandSubmission.bue_id, BrandSubmission.student_name)
        .order_by(BrandSubmission.id.desc())
        .all()
    )

    for bue_id, name in rows:
        if bue_id not in students:
            students[bue_id] = name

    students.pop(submission.bue_id, None)   # not the current owner

    student_list = sorted(students.items(), key=lambda item: item[1].lower())

    error = None

    if request.method == "POST":

        choice = request.form.get("new_owner", "")

        if choice == "__other__":
            new_name = request.form.get("new_student_name", "").strip()
            new_bue_id = request.form.get("new_bue_id", "").strip()
        else:
            new_bue_id = choice
            new_name = students.get(choice, "")

        if not new_name or not new_bue_id:
            error = "Please choose a student, or enter their name and BUE ID."
        elif len(new_name) > 100 or len(new_bue_id) > 30:
            error = "The name or BUE ID is too long."

        elif new_bue_id == submission.bue_id:
            error = "This request already belongs to that BUE ID."

        else:

            old_name = submission.student_name
            old_bue_id = submission.bue_id

            submission.student_name = new_name
            submission.bue_id = new_bue_id

            db.session.add(AdminActivity(
                admin_username=master.username,
                action="Transferred",
                target=(
                    "%s: %s (%s) -> %s (%s)"
                    % (submission.brand_name, old_name, old_bue_id, new_name, new_bue_id)
                )[:255]
            ))

            db.session.commit()

            return redirect("/admin/review/" + str(id))

    return render_template(
        "transfer_request.html",
        submission=submission,
        students=student_list,
        error=error
    )

@app.route("/admin/export")
def export_submissions():

    admin = current_admin()

    if not admin:
        return redirect("/login")

    workbook = Workbook()

    sheet = workbook.active
    sheet.title = "Brand Submissions"

    sheet.append([
        "ID",
        "Student Name",
        "BUE ID",
        "Brand Name",
        "Social Link",
        "Category",
        "Phone Number",
        "Contact Name",
        "Contact Position",
        "Status"
    ])

    submissions = BrandSubmission.query.order_by(
        BrandSubmission.id.asc()
    ).all()

    for submission in submissions:

        sheet.append([
            submission.id,
            submission.student_name,
            submission.bue_id,
            submission.brand_name,
            submission.social_link,
            submission.category,
            submission.phone_number,
            submission.contact_name,
            submission.contact_position,
            submission.status
        ])

    output = BytesIO()

    workbook.save(output)

    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name="BrandSubmissions.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

@app.route("/admin/delete/<int:admin_id>", methods=["POST"])
def delete_admin(admin_id):

    admin = current_admin()

    if not admin:
        return redirect("/login")

    if admin.role != "Master":
        flash("Only the Master can delete accounts.", "danger")
        return redirect("/admin/manage-admins")

    admin_to_delete = Admin.query.get_or_404(admin_id)

    if admin_to_delete.id == admin.id:
        flash("You cannot delete your own account.", "danger")
        return redirect("/admin/manage-admins")

    activity = AdminActivity(
       admin_username=admin.username,
        action="Deleted",
        target=admin_to_delete.username
    )

    db.session.delete(admin_to_delete)
    db.session.commit()

    flash("Admin account deleted successfully.", "success")

    return redirect("/admin/manage-admins")

@app.route("/admin/clear-activity-log", methods=["POST"])
def clear_activity_log():

    admin = current_admin()

    if not admin:
        return redirect("/login")

    if admin.role != "Master":
        return redirect("/admin/activity-log")

    # Remove every activity log
    AdminActivity.query.delete()

    db.session.commit()

    return redirect("/admin/activity-log")

@app.route("/admin/delete-submission/<int:submission_id>", methods=["POST"])
def delete_submission(submission_id):

    admin = current_admin()

    if not admin:
        return redirect("/login")

    if admin.role != "Master":
        flash("Only the Master can delete requests.", "danger")
        return redirect("/admin/dashboard")

    submission = BrandSubmission.query.get_or_404(submission_id)

    activity = AdminActivity(
        admin_username=admin.username,
        action="Deleted Request",
        target=submission.brand_name
    )

    db.session.delete(submission)
    db.session.commit()

    return redirect("/admin/dashboard")

@app.route("/admin/activity-log")
def activity_log():

    admin = current_admin()

    if not admin:
        return redirect("/admin/dashboard")

    activities = AdminActivity.query.order_by(
        AdminActivity.timestamp.desc()
    ).all()
    for activity in activities:

        activity.local_time = to_local(activity.timestamp)
    return render_template(
        "activity_log.html",
        activities=activities
    )

def open_browser():
    if not os.path.exists(LOCK_FILE):
        webbrowser.open("http://127.0.0.1:5000")
        with open(LOCK_FILE, "w") as f:
            f.write("opened")

def remove_lock():
    if os.path.exists(LOCK_FILE):
        os.remove(LOCK_FILE)

atexit.register(remove_lock)

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        print("Database and tables are ready!")
        print(app.url_map)
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        threading.Timer(1, open_browser).start()
    app.run(debug=True)