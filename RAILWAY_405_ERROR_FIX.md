# 🔧 Исправление ошибки 405 на Railway

## Проблема
Ошибка 405 (Method Not Allowed) при попытке логина/регистрации, хотя запрос идет на правильный URL.

## Причина
Railway использует динамический порт через переменную `$PORT`, но приложение может быть настроено на фиксированный порт 8000.

---

## ✅ Решение

### Шаг 1: Проверьте Start Command в Railway

1. В Railway перейдите в ваш сервис **Xtrix**
2. Откройте вкладку **Settings**
3. Найдите раздел **"Deploy"** или **"Build & Deploy"**
4. Проверьте поле **"Start Command"**

**Должно быть:**
```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

**Неправильно (не используйте):**
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000  ❌
```

### Шаг 2: Исправьте Start Command

Если в Start Command указан порт 8000:

1. Измените на: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
2. Сохраните изменения
3. Railway автоматически перезапустит сервис

### Шаг 3: Альтернатива - создайте файл railway.json или используйте Nixpacks

Railway автоматически определяет Python проекты. Если Start Command не помогает, проверьте:

1. **Settings** → **Build**
2. Убедитесь, что **"Build Command"** правильный:
   ```bash
   pip install -r requirements.txt
   ```

3. **Settings** → **Deploy**
4. Убедитесь, что **"Start Command"** правильный:
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port $PORT
   ```

---

## 🔍 Дополнительная диагностика

### Проверьте логи в Railway

1. Перейдите в ваш сервис Xtrix
2. Откройте вкладку **"Logs"**
3. Попробуйте войти снова
4. Посмотрите, есть ли ошибки в логах

**Что искать:**
- Ошибки о порте
- Ошибки о подключении к базе данных
- Ошибки валидации

### Проверьте, что бэкенд работает

1. Откройте в браузере: `https://xtrix-production.up.railway.app/docs`
2. Должна открыться Swagger документация
3. Если открывается - бэкенд работает
4. Попробуйте вызвать эндпоинт `/` (root):
   - `https://xtrix-production.up.railway.app/`
   - Должен вернуться JSON с информацией об API

---

## 📝 Правильная настройка для Railway

### Переменные окружения (уже настроены):
```
DATABASE_URL=${{Postgres.DATABASE_URL}}
JWT_SECRET=ваш-секретный-ключ
ENVIRONMENT=production
CORS_ORIGINS=https://ваш-фронтенд.vercel.app
```

### Start Command:
```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

### Build Command (если нужен):
```bash
pip install -r requirements.txt && alembic upgrade head
```

---

## 🆘 Если ошибка все еще есть

1. **Проверьте логи Railway** - там должна быть подробная информация
2. **Проверьте Swagger docs** - работает ли API вообще
3. **Проверьте Network в браузере** - какой именно HTTP метод используется (должен быть POST)
4. **Проверьте CORS** - возможно, проблема в preflight запросах (OPTIONS)

---

## 💡 Важно

Railway автоматически устанавливает переменную `$PORT`, и ваше приложение **должно** использовать её, а не фиксированный порт 8000.

