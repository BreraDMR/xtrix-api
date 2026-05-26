import requests
import json
def create_user():
    base_url = "http://localhost:8000"
    user_data = {
        "full_name": "Иван Петров",
        "email": "ivan.petrov@example.com",
        "phone": "+420123456789",
        "password": "TestP@ss57!",
        "role": "specialist"
    }
    try:
        print("Создаю пользователя через API...")
        response = requests.get(f"{base_url}/users/")
        if response.status_code == 200:
            users = response.json()
            existing = [u for u in users if u.get('email') == user_data['email']]
            if existing:
                print(f"[INFO] Пользователь уже существует: {existing[0]['full_name']}")
                print("[INFO] Удалите пользователя через API или базу данных, затем создайте заново")
                return
        response = requests.post(
            f"{base_url}/users/",
            json=user_data,
            headers={"Content-Type": "application/json"}
        )
        if response.status_code == 200:
            print("[OK] Пользователь успешно создан!")
            print(f"\nУчетные данные для входа:")
            print(f"   Email: {user_data['email']}")
            print(f"   Пароль: {user_data['password']}")
        else:
            print(f"[ERROR] Ошибка при создании: {response.status_code}")
            print(f"Ответ: {response.text}")
    except requests.exceptions.ConnectionError:
        print("[ERROR] Не удалось подключиться к серверу")
        print("Убедитесь, что бэкенд запущен: uvicorn app.main:app --reload")
    except Exception as e:
        print(f"[ERROR] Ошибка: {e}")
if __name__ == "__main__":
    create_user()
