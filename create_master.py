from app import app
from models import db, Admin
import getpass

with app.app_context():

    username = input("Master username: ")
    password = getpass.getpass("Master password: ")

    existing = Admin.query.filter_by(role="Master").first()

    if existing:
        print("A Master account already exists.")
    else:
        master = Admin(
            username=username,
            role="Master"
        )

        master.set_password(password)

        db.session.add(master)
        db.session.commit()

        print("Master account created successfully!")