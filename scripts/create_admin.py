"""Create a platform admin. Public signup can't do that on purpose.

Usage: python scripts/create_admin.py admin@example.com "Full Name"
The password is asked interactively so it never lands in shell history.
"""
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.crud import crud_user  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.schemas.user import UserCreate  # noqa: E402


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    email, full_name = sys.argv[1], sys.argv[2]
    password = getpass.getpass("Password: ")
    db = SessionLocal()
    try:
        if crud_user.get_by_phone_or_email(db, phone=None, email=email):
            sys.exit(f"User {email} already exists")
        user = crud_user.create_user(
            db, UserCreate(email=email, full_name=full_name, password=password, role="admin")
        )
        print(f"Admin created: id={user.id} {user.email}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
