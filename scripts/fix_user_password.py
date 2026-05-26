import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from app.db.models.user import User
import bcrypt
def fix_password():
    db: Session = SessionLocal()
    try:
        email = "ivan.petrov@example.com"
        password = "TestP@ss57!"
        user = db.query(User).filter(User.email == email).first()
        if not user:
            print(f"[ERROR] Пользователь с email {email} не найден")
            print("Создайте пользователя через API: http://localhost:8000/docs -> POST /users/")
            return
        print(f"[INFO] Найден пользователь: {user.full_name} (ID: {user.id})")
        print(f"[INFO] Старый хеш: {user.hashed_password[:50]}...")
        print(f"[INFO] Создаю новый bcrypt хеш для пароля: {password}")
        password_bytes = password.encode('utf-8')
        if len(password_bytes) > 72:
            password_bytes = password_bytes[:72]
        salt = bcrypt.gensalt()
        hashed = bcrypt.hashpw(password_bytes, salt)
        user.hashed_password = hashed.decode('utf-8')
        db.commit()
        db.refresh(user)
        print(f"[OK] Пароль успешно обновлен!")
        print(f"[INFO] Новый хеш: {user.hashed_password[:50]}...")
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
    fix_password()
