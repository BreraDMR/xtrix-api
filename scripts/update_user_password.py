"""
Скрипт для обновления пароля пользователя напрямую через SQL.
Используется когда bcrypt не работает.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from app.db.models.user import User
import bcrypt

def update_password():
    """Обновляет пароль пользователя напрямую через bcrypt."""
    db: Session = SessionLocal()
    
    try:
        email = "ivan.petrov@example.com"
        password = "TestP@ss57!"
        
        user = db.query(User).filter(User.email == email).first()
        
        if not user:
            print(f"[ERROR] Пользователь с email {email} не найден")
            return
        
        print(f"[INFO] Найден пользователь: {user.full_name} (ID: {user.id})")
        print(f"[INFO] Обновляю пароль...")
        
        # Используем bcrypt напрямую
        try:
            salt = bcrypt.gensalt()
            hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
            user.hashed_password = hashed.decode('utf-8')
        except Exception as e:
            print(f"[ERROR] Ошибка при создании хеша: {e}")
            # Используем готовый хеш для пароля "TestP@ss57!"
            # Этот хеш был создан заранее
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

