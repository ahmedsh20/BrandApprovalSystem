from flask import Flask, render_template, request, redirect, session, send_file, flash
from models import db, BrandSubmission, Admin, RegistrationRequest, AdminActivity
from werkzeug.security import generate_password_hash
from sqlalchemy import or_, case
from io import BytesIO
from openpyxl import Workbook
from zoneinfo import ZoneInfo
from datetime import timedelta
import os
import webbrowser
import threading
import atexit

LOCK_FILE = ".browser_opened"

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "development-secret-key")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///database.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

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

@app.route("/", methods=["GET", "POST"])
def home():

    if request.method == "POST":

        student_name = request.form["student_name"]
        bue_id = request.form["bue_id"]
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

        password = request.form["password"]

        confirm = request.form["confirm_password"]

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
            password_hash=""
        )

        request_account = RegistrationRequest(
            username=username,
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

    return render_template(
        "admin_login.html",
        error=None
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

        bue_id = request.form["bue_id"]

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

        return render_template(
            "status_results.html",
            submissions=submissions,
            admin=admin,
        )

    return render_template("check_status.html")

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

        activity.local_time = activity.local_time = activity.timestamp + timedelta(hours=3)
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