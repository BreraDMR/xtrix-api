# 🚀 Деплой - Быстрый старт

**Все изменения закоммичены и запушены! ✅**

---

## 📋 Что было добавлено в этом коммите

### Frontend улучшения:
- ✅ Пустые состояния для Dashboard виджетов
- ✅ Улучшенные индикаторы загрузки
- ✅ Компоненты таблиц (сортировка, пагинация, экспорт CSV)
- ✅ Улучшенная валидация форм
- ✅ Обработка ошибок с понятными сообщениями
- ✅ Прокрутка меню для мобильных устройств

### Backend улучшения:
- ✅ Статистика платежей по методам оплаты
- ✅ Подсчет новых клиентов
- ✅ Улучшенная аналитика

---

## 🚀 Шаги деплоя

### 1. Backend (Railway)

#### Если проект уже подключен к Railway:
1. Деплой должен начаться автоматически после push в `main`
2. Проверьте статус в Railway Dashboard → Deployments
3. Дождитесь завершения деплоя

#### Если проект еще не подключен:
1. Откройте https://railway.app
2. Нажмите "New Project"
3. Выберите "Deploy from GitHub repo"
4. Выберите ваш репозиторий `BooNaty/Xtrix`
5. Railway автоматически определит Dockerfile и начнет деплой

#### Настройка переменных окружения:
После первого деплоя настройте переменные в Railway → Settings → Variables:

```env
ENVIRONMENT=production
CORS_ORIGINS=https://your-app.vercel.app  # Замените на ваш Vercel домен после деплоя frontend
JWT_SECRET=your-secret-key-minimum-32-characters-long
DATABASE_URL=${{Postgres.DATABASE_URL}}  # Автоматически при подключении PostgreSQL
```

#### Подключение PostgreSQL:
1. Railway Dashboard → New → Database → PostgreSQL
2. Railway автоматически создаст `DATABASE_URL`
3. Миграции применятся автоматически через `entrypoint.sh`

#### Проверка backend:
- URL будет в формате: `https://your-project.up.railway.app`
- Проверьте: `https://your-project.up.railway.app/metrics/health`
- Проверьте docs: `https://your-project.up.railway.app/docs`

---

### 2. Frontend (Vercel)

#### Если проект уже подключен к Vercel:
1. Деплой должен начаться автоматически после push в `main`
2. Проверьте статус в Vercel Dashboard → Deployments
3. Дождитесь завершения деплоя

#### Если проект еще не подключен:
1. Откройте https://vercel.com
2. Нажмите "Add New" → "Project"
3. Импортируйте GitHub репозиторий `BooNaty/Xtrix`
4. Настройте проект:
   - **Root Directory:** `frontend`
   - **Framework Preset:** Vite
   - **Build Command:** `npm run build`
   - **Output Directory:** `dist`
   - **Install Command:** `npm install`
5. Нажмите "Deploy"

#### Настройка переменных окружения:
**НЕ устанавливайте** `VITE_API_BASE_URL` - используется rewrite через `vercel.json`

#### Обновление vercel.json:
После получения URL Railway backend, обновите `frontend/vercel.json`:

```json
{
  "rewrites": [
    {
      "source": "/api/(.*)",
      "destination": "https://your-railway-url.up.railway.app/$1"
    }
  ]
}
```

Затем сделайте commit и push:
```bash
git add frontend/vercel.json
git commit -m "Update Railway URL in vercel.json"
git push origin main
```

#### Проверка frontend:
- URL будет в формате: `https://your-project.vercel.app`
- Откройте в браузере и проверьте работу

---

## ⚙️ Важные шаги после деплоя

### 1. Обновите CORS в Railway

После деплоя frontend на Vercel:
1. Скопируйте URL вашего Vercel проекта (например: `https://xtrix.vercel.app`)
2. В Railway Dashboard → Settings → Variables
3. Обновите `CORS_ORIGINS`:
   ```
   CORS_ORIGINS=https://your-vercel-url.vercel.app
   ```
4. Перезапустите deployment в Railway

### 2. Обновите vercel.json

После получения URL Railway backend:
1. Обновите `frontend/vercel.json` с правильным Railway URL
2. Сделайте commit и push
3. Vercel автоматически пересоберет и задеплоит

### 3. Проверьте работу

1. **Backend:**
   - ✅ Health endpoint: `https://your-railway-url/metrics/health`
   - ✅ API docs: `https://your-railway-url/docs`

2. **Frontend:**
   - ✅ Откройте в браузере
   - ✅ Попробуйте залогиниться
   - ✅ Проверьте Dashboard
   - ✅ Проверьте таблицы (сортировка, пагинация)
   - ✅ Проверьте экспорт CSV

3. **Интеграция:**
   - ✅ Проверьте Network в DevTools - запросы должны идти на `/api/*`
   - ✅ Не должно быть ошибок CORS

---

## 🔍 Мониторинг деплоя

### Railway:
- Dashboard: https://railway.app/dashboard
- Логи: Railway Dashboard → Deployments → последний deployment → Logs
- Shell: Railway Dashboard → Deployments → Shell

### Vercel:
- Dashboard: https://vercel.com/dashboard
- Логи: Vercel Dashboard → Deployments → последний deployment → Logs
- Build Logs: Vercel Dashboard → Deployments → Build Logs

---

## ⚠️ Если что-то пошло не так

### Backend не запускается:
1. Проверьте логи в Railway Dashboard
2. Убедитесь, что все переменные окружения настроены
3. Проверьте, что PostgreSQL подключен

### Frontend не собирается:
1. Проверьте логи в Vercel Dashboard
2. Убедитесь, что `npm run build` проходит локально
3. Проверьте, что все зависимости установлены

### CORS ошибки:
1. Убедитесь, что `CORS_ORIGINS` содержит ваш Vercel домен
2. Перезапустите deployment в Railway

### Миграции не применены:
1. В Railway Dashboard → Deployments → Shell
2. Выполните: `alembic upgrade head`
3. Проверьте: `alembic current`

---

## ✅ Готово!

После выполнения всех шагов ваше приложение будет доступно:
- **Backend:** `https://your-project.up.railway.app`
- **Frontend:** `https://your-project.vercel.app`

**Успешного деплоя! 🎉**
