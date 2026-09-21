# Emergency tool: set a new password for an admin from the server console.
# Run it in a PythonAnywhere Bash console:   python reset_password.py
# Only someone with access to the server can use it.

from app import app
from models import db, Admin
import getpass

with app.app_context():

    username = input("Username to reset: ").strip()

    admin = Admin.query.filter_by(username=username).first()

    if not admin:
        print("No admin with that username.")
    else:
        password = getpass.getpass("New password: ")
        confirm = getpass.getpass("Repeat new password: ")

        if password != confirm or len(password) < 8:
            print("Passwords must match and be at least 8 characters. Nothing changed.")
        else:
            admin.set_password(password)
            db.session.commit()
            print("Password changed for", admin.username)