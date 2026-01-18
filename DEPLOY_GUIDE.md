# 🚀 Руководство по деплою

**Дата:** 2024-12-28  
**Статус:** Готово к деплою

---

## 📋 Быстрый старт

### 1. Backend (Railway)

#### Шаг 1: Подготовка репозитория
```bash
# Убедитесь, что все изменения закоммичены
git add .
git commit -m "Ready for deployment"
git push origin main
```

#### Шаг 2: Деплой на Railway
1. Откройте [Railway Dashboard](https://railway.app)
2. Создайте новый проект или откройте существующий
3. Нажмите "New" → "GitHub Repo"
4. Выберите ваш репозиторий
5. Railway автоматически определит Dockerfile и начнет деплой

#### Шаг 3: Настройка переменных окружения

В Railway Dashboard → Settings → Variables добавьте:

```env
# Обязательные
ENVIRONMENT=production
DATABASE_URL=${{Postgres.DATABASE_URL}}  # Автоматически при подключении PostgreSQL
JWT_SECRET=your-secret-key-minimum-32-characters-long

# CORS (замените на ваш домен Vercel)
CORS_ORIGINS=https://your-app.vercel.app

# Опциональные (если используются)
REDIS_URL=redis://localhost:6379/0
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=your-email@example.com
SMTP_PASSWORD=your-password
TWILIO_ACCOUNT_SID=your-twilio-sid
TWILIO_AUTH_TOKEN=your-twilio-token
```

#### Шаг 4: Подключение PostgreSQL
1. В Railway Dashboard → New → Database → PostgreSQL
2. Railway автоматически создаст `DATABASE_URL`
3. Примените миграции (см. ниже)

#### Шаг 5: Применение миграций

**Вариант 1: Через Railway CLI**
```bash
railway shell
alembic upgrade head
exit
```

**Вариант 2: Через Railway Dashboard**
1. Откройте проект → Deployments → последний deployment
2. Откройте вкладку "Shell"
3. Выполните: `alembic upgrade head`

**Вариант 3: Автоматически (через entrypoint.sh)**
Миграции применяются автоматически при каждом деплое через `entrypoint.sh`

#### Шаг 6: Проверка backend
1. Откройте URL вашего Railway проекта (например: `https://xtrix-production.up.railway.app`)
2. Должен вернуться JSON:
```json
{
  "name": "Xtrix API",
  "version": "1.0.0",
  "status": "running"
}
```
3. Проверьте health: `https://your-railway-url/metrics/health`
4. Проверьте docs: `https://your-railway-url/docs`

---

### 2. Frontend (Vercel)

#### Шаг 1: Подготовка
```bash
cd frontend
npm run build  # Проверка, что сборка проходит успешно
```

#### Шаг 2: Деплой на Vercel

**Вариант 1: Через Vercel Dashboard**
1. Откройте [Vercel Dashboard](https://vercel.com)
2. Нажмите "Add New" → "Project"
3. Импортируйте ваш GitHub репозиторий
4. Настройте проект:
   - **Root Directory:** `frontend`
   - **Framework Preset:** Vite
   - **Build Command:** `npm run build`
   - **Output Directory:** `dist`
   - **Install Command:** `npm install`

**Вариант 2: Через Vercel CLI**
```bash
cd frontend
npm i -g vercel
vercel login
vercel
```

#### Шаг 3: Настройка переменных окружения

В Vercel Dashboard → Settings → Environment Variables:

**Вариант 1: Использовать rewrite (рекомендуется)**
- **НЕ устанавливайте** `VITE_API_BASE_URL`
- Убедитесь, что `vercel.json` настроен правильно (см. ниже)

**Вариант 2: Прямой URL**
```env
VITE_API_BASE_URL=https://your-railway-url.up.railway.app
```

#### Шаг 4: Настройка vercel.json

Убедитесь, что `frontend/vercel.json` содержит правильный URL backend:

```json
{
  "rewrites": [
    {
      "source": "/api/:path*",
      "destination": "https://your-railway-url.up.railway.app/:path*"
    }
  ]
}
```

#### Шаг 5: Проверка frontend
1. Откройте URL вашего Vercel проекта
2. Откройте DevTools → Network
3. Попробуйте залогиниться
4. Проверьте, что запросы идут на правильный URL

---

## 🔧 Проверка после деплоя

### Backend проверки

1. **Health endpoint:**
   ```bash
   curl https://your-railway-url/metrics/health
   ```

2. **API документация:**
   ```
   https://your-railway-url/docs
   ```

3. **Проверка CORS:**
   - Откройте frontend в браузере
   - Откройте DevTools → Console
   - Не должно быть ошибок CORS

### Frontend проверки

1. **Откройте приложение:**
   ```
   https://your-app.vercel.app
   ```

2. **Проверьте Network:**
   - Запросы должны идти на `/api/*` (если используется rewrite)
   - Или на прямой Railway URL (если используется прямой URL)

3. **Проверьте функционал:**
   - ✅ Логин/регистрация
   - ✅ Dashboard загружается
   - ✅ Виджеты отображают данные
   - ✅ Таблицы работают (сортировка, пагинация)
   - ✅ Формы валидируются
   - ✅ Экспорт CSV работает

---

## ⚠️ Устранение проблем

### Backend не запускается

**Проблема:** 502 Bad Gateway или ошибки в логах

**Решение:**
1. Проверьте логи в Railway Dashboard → Deployments → Logs
2. Убедитесь, что все переменные окружения настроены
3. Проверьте, что `DATABASE_URL` правильный
4. Проверьте, что миграции применены: `alembic current`

### CORS ошибки

**Проблема:** `Access-Control-Allow-Origin header is missing`

**Решение:**
1. В Railway → Settings → Variables
2. Убедитесь, что `CORS_ORIGINS` содержит ваш домен Vercel:
   ```
   CORS_ORIGINS=https://your-app.vercel.app
   ```
3. Убедитесь, что `ENVIRONMENT=production`
4. Перезапустите deployment в Railway

### Frontend не может подключиться к backend

**Проблема:** Network errors или 404

**Решение:**
1. Проверьте `VITE_API_BASE_URL` в Vercel (или убедитесь, что он не установлен для rewrite)
2. Проверьте `vercel.json` - правильный ли URL backend
3. Проверьте, что backend доступен: откройте Railway URL в браузере

### Миграции не применены

**Проблема:** Новые таблицы/колонки не существуют

**Решение:**
1. В Railway Dashboard → Deployments → Shell
2. Выполните: `alembic upgrade head`
3. Проверьте: `alembic current`

### Таблицы не сортируются / пагинация не работает

**Проблема:** Функционал таблиц не работает на production

**Решение:**
1. Убедитесь, что сборка прошла успешно: `npm run build`
2. Проверьте, что все файлы задеплоены (особенно `utils/table.ts`, компоненты таблиц)
3. Очистите кеш браузера
4. Проверьте консоль браузера на ошибки

---

## 📝 Чеклист перед деплоем

### Backend
- [ ] Все миграции созданы и протестированы локально
- [ ] `Dockerfile` настроен правильно
- [ ] `entrypoint.sh` работает (применяет миграции)
- [ ] Все переменные окружения задокументированы
- [ ] CORS настроен для production домена

### Frontend
- [ ] `npm run build` проходит без ошибок
- [ ] Все новые компоненты и утилиты включены в сборку
- [ ] `vercel.json` настроен правильно
- [ ] Переменные окружения настроены (или не установлены для rewrite)
- [ ] Тестирование на локальной сборке (`npm run preview`)

### Общее
- [ ] Все изменения закоммичены и запушены
- [ ] README обновлен (если нужно)
- [ ] Документация актуальна

---

## 🔄 Обновление после изменений

### Backend
1. Сделайте изменения в коде
2. Создайте миграции (если нужно): `alembic revision --autogenerate -m "description"`
3. Закоммитьте и запушьте: `git push origin main`
4. Railway автоматически задеплоит изменения
5. Миграции применятся автоматически через `entrypoint.sh`

### Frontend
1. Сделайте изменения в коде
2. Проверьте локально: `npm run build`
3. Закоммитьте и запушьте: `git push origin main`
4. Vercel автоматически задеплоит изменения

---

## 📚 Дополнительные ресурсы

- [Railway Setup](./RAILWAY_SETUP.md) - детальная настройка Railway
- [Vercel Setup](./VERCEL_SETUP.md) - детальная настройка Vercel
- [Apply Migration Railway](./APPLY_MIGRATION_RAILWAY.md) - применение миграций

---

## ✅ Готово!

После выполнения всех шагов ваше приложение должно быть доступно:
- **Backend:** `https://your-railway-url.up.railway.app`
- **Frontend:** `https://your-app.vercel.app`

**Успешного деплоя! 🎉**
