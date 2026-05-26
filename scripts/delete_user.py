import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from app.db.models.user import User
def delete_user():
    db: Session = SessionLocal()
    try:
        email = "ivan.petrov@example.com"
        user = db.query(User).filter(User.email == email).first()
        if not user:
            print(f"[INFO] Пользователь с email {email} не найден")
            return
        print(f"[INFO] Найден пользователь: {user.full_name} (ID: {user.id})")
        print(f"[INFO] Удаляю пользователя...")
        db.delete(user)
        db.commit()
        print(f"[OK] Пользователь успешно удален!")
        print(f"\nТеперь вы можете создать нового пользователя через API:")
        print(f"   http://localhost:8000/docs -> POST /users/")
    except Exception as e:
        db.rollback()
        print(f"[ERROR] Ошибка: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()
if __name__ == "__main__":
    delete_user()
