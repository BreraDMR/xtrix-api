"""
Скрипт для создания тестовых данных в базе данных.
Запустите: python scripts/create_test_data.py
"""
import sys
from pathlib import Path

# Добавляем корневую директорию в путь
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datetime import date, datetime
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.db.session import SessionLocal
from app.db.models.user import User
from app.db.models.client import Client
from app.api.crud import crud_client
from app.schemas.client import ClientCreate


def create_test_data():
    """Создает тестовые данные: пользователя и клиента."""
    db: Session = SessionLocal()
    
    try:
        print("Создание тестовых данных...")
        
        # 1. Создаем пользователя (специалиста)
        print("\n1. Создаю пользователя (специалиста)...")
        user_email = "ivan.petrov@example.com"
        user_phone = "+420123456789"
        user_password = "TestP@ss57!"  # Пароль без последовательных символов
        
        # Проверяем, не существует ли уже такой пользователь
        existing_user = db.query(User).filter(
            (User.email == user_email) | 
            (User.phone == user_phone)
        ).first()
        
        if existing_user:
            print(f"   [INFO] Пользователь уже существует: {existing_user.full_name} (ID: {existing_user.id})")
            print(f"   [INFO] Обновляю пароль...")
            # Обновляем пароль с правильным bcrypt хешем
            from app.api.core.security import get_password_hash
            existing_user.hashed_password = get_password_hash(user_password)
            db.commit()
            db.refresh(existing_user)
            print(f"   [OK] Пароль обновлен")
            user = existing_user
        else:
            # Создаем хеш пароля через bcrypt
            from app.api.core.security import get_password_hash
            # Используем простой пароль без последовательных символов
            # Пароль должен быть: Pass123!@ (но без последовательных символов)
            # Используем: TestP@ss57!
            simple_password = "TestP@ss57!"
            try:
                hashed = get_password_hash(simple_password)
            except Exception as e:
                print(f"   [ERROR] Не удалось создать хеш пароля: {e}")
                print("   Попробуйте запустить скрипт снова или создайте пользователя через API")
                raise
            
            # Создаем пользователя напрямую через SQLAlchemy
            user = User(
                full_name="Иван Петров",
                phone=user_phone,
                email=user_email,
                hashed_password=hashed,
                role="specialist"
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            print(f"   [OK] Пользователь создан: {user.full_name} (ID: {user.id})")
            print(f"      Email: {user.email}")
            print(f"      Телефон: {user.phone}")
            print(f"      Роль: {user.role}")
        
        # 2. Создаем клиента
        print("\n2. Создаю клиента...")
        client_data = ClientCreate(
            owner_id=user.id,
            full_name="Мария Смирнова",
            birth_date=date(1990, 5, 15),
            gender="female",
            contact_phone="+420987654321",
            contact_email="maria.smirnova@example.com",
            allergies="Пыльца, орехи",
            chronic_diseases="Астма",
            pregnancy=False,
            personal_notes="Постоянный клиент, предпочитает вечерние визиты",
            spending=1500.0,
            birthday_discount=True
        )
        
        # Проверяем, не существует ли уже такой клиент
        existing_client = db.query(Client).filter(
            Client.owner_id == user.id,
            Client.full_name == client_data.full_name
        ).first()
        
        if existing_client:
            print(f"   [WARNING] Клиент уже существует: {existing_client.full_name} (ID: {existing_client.id})")
            client = existing_client
        else:
            client = crud_client.create_client(db, client_data)
            print(f"   [OK] Клиент создан: {client.full_name} (ID: {client.id})")
            print(f"      Дата рождения: {client.birth_date}")
            print(f"      Телефон: {client.contact_phone}")
            print(f"      Email: {client.contact_email}")
            print(f"      Потрачено: {client.spending} Kc")
        
        print("\n[SUCCESS] Тестовые данные успешно созданы!")
        print("\nИнформация для входа:")
        print(f"   Email: {user.email}")
        print(f"   Пароль: TestP@ss57!")
        print(f"\nПроверьте данные на сайте:")
        print(f"   http://localhost:8000/docs - документация API")
        print(f"   http://localhost:8000/api/v1/users/ - список пользователей")
        print(f"   http://localhost:8000/api/v1/clients/ - список клиентов")
        
        db.commit()
        
    except Exception as e:
        db.rollback()
        print(f"\n[ERROR] Ошибка при создании данных: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()


if __name__ == "__main__":
    create_test_data()

