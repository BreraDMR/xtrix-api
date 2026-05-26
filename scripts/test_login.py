import requests
import json
def test_login():
    base_url = "http://localhost:8000"
    login_data = {
        "email": "ivan.petrov@example.com",
        "password": "TestP@ss57!"
    }
    try:
        print("Тестирую логин...")
        print(f"URL: {base_url}/users/login")
        print(f"Data: {login_data}")
        response = requests.post(
            f"{base_url}/users/login",
            json=login_data,
            headers={"Content-Type": "application/json"}
        )
        print(f"\nСтатус код: {response.status_code}")
        print(f"Ответ: {response.text}")
        if response.status_code == 200:
            data = response.json()
            print("\n[OK] Логин успешен!")
            print(f"Access token получен: {data.get('access_token', 'N/A')[:50]}...")
        else:
            print(f"\n[ERROR] Логин не удался")
            try:
                error_data = response.json()
                print(f"Детали ошибки: {json.dumps(error_data, indent=2, ensure_ascii=False)}")
            except:
                print(f"Текст ошибки: {response.text}")
    except requests.exceptions.ConnectionError:
        print("[ERROR] Не удалось подключиться к серверу")
        print("Убедитесь, что бэкенд запущен: uvicorn app.main:app --reload")
    except Exception as e:
        print(f"[ERROR] Ошибка: {e}")
        import traceback
        traceback.print_exc()
if __name__ == "__main__":
    test_login()
