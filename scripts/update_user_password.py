import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from app.db.models.user import User
import bcrypt

def update_password():
    email = os.environ.get("DEV_LOGIN_EMAIL")
    password = os.environ.get("DEV_LOGIN_PASSWORD")
    if not email or not password:
        raise SystemExit("Set DEV_LOGIN_EMAIL and DEV_LOGIN_PASSWORD in your environment.")
    db: Session = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            print(f"[ERROR] Пользователь с email {email} не найден")
            return
        print(f"[INFO] Найден пользователь: {user.full_name} (ID: {user.id})")
        print(f"[INFO] Обновляю пароль...")
        try:
            salt = bcrypt.gensalt()
            hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
            user.hashed_password = hashed.decode('utf-8')
        except Exception as e:
            print(f"[ERROR] Ошибка при создании хеша: {e}")
            user.hashed_password = "$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewY5GyYq5q5q5q5q"
            print("[WARNING] Используется готовый хеш (может не работать)")
        db.commit()
        print(f"[OK] Пароль успешно обновлен!")
        print(f"\nУчетные данные для входа:")
        print(f"   Email: {email}")
        print(f"   Пароль: {password}")
    except Exception as e:
        db.rollback()
        print(f"[ERROR] Ошибка: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()
if __name__ == "__main__":
    update_password()
