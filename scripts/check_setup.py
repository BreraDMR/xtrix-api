import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
def check_env_file():
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
    if os.path.exists(env_path):
        print("[OK] Файл .env найден")
        return True
    else:
        print("[ERROR] Файл .env НЕ найден!")
        print(f"  Создайте файл .env в корне проекта: {env_path}")
        return False
def check_database_connection():
    try:
        from app.api.core.config import settings
        print(f"[OK] Настройки загружены")
        print(f"  DATABASE_URL: {settings.database_url[:30]}...")
        from sqlalchemy import create_engine
        engine = create_engine(settings.database_url)
        with engine.connect() as conn:
            print("[OK] Подключение к базе данных успешно!")
            return True
    except Exception as e:
        print(f"[ERROR] Ошибка подключения к базе данных: {e}")
        print("\n  Возможные причины:")
        print("  1. База данных не создана")
        print("  2. Неправильный DATABASE_URL в .env")
        print("  3. PostgreSQL не запущен")
        return False
def check_required_settings():
    try:
        from app.api.core.config import settings
        required = {
            "DATABASE_URL": settings.database_url,
            "JWT_SECRET": settings.jwt_secret,
        }
        all_ok = True
        for key, value in required.items():
            if not value or value == "":
                print(f"[ERROR] {key} не установлен!")
                all_ok = False
            else:
                print(f"[OK] {key} установлен")
        return all_ok
    except Exception as e:
        print(f"[ERROR] Ошибка загрузки настроек: {e}")
        return False
if __name__ == "__main__":
    print("=" * 50)
    print("Проверка настройки проекта Xtrix")
    print("=" * 50)
    print()
    env_ok = check_env_file()
    print()
    if env_ok:
        settings_ok = check_required_settings()
        print()
        if settings_ok:
            db_ok = check_database_connection()
            print()
            if db_ok:
                print("=" * 50)
                print("[SUCCESS] Все проверки пройдены! Проект готов к работе.")
                print("=" * 50)
            else:
                print("=" * 50)
                print("[ERROR] Проблемы с базой данных. См. инструкции выше.")
                print("=" * 50)
        else:
            print("=" * 50)
            print("[ERROR] Не все настройки установлены. Проверьте .env файл.")
            print("=" * 50)
    else:
        print("=" * 50)
        print("[ERROR] Создайте файл .env с необходимыми настройками.")
        print("=" * 50)
        print("\nПример содержимого .env:")
        print("DATABASE_URL=postgresql://postgres:ваш_пароль@localhost:5432/xtrix")
        print("JWT_SECRET=ваш_секретный_ключ_минимум_32_символа_длинный")
        print("REDIS_URL=redis://localhost:6379/0")
        print("RATE_LIMIT_ENABLED=false")
