# 🚨 СРОЧНО: Исправление проблем с деплоем

## Проблемы:

1. **502 Bad Gateway** - Backend на Railway не отвечает
2. **CORS ошибка** - Frontend (Vercel) не может обращаться к Backend (Railway)

## 🔧 Немедленные действия:

### 1. Проверить и настроить Railway (Backend)

#### A. Проверить статус деплоя:
1. Откройте Railway Dashboard: https://railway.com
2. Перейдите в ваш проект → Deployments
3. Проверьте последний deployment:
   - Если "Building" - подождите завершения
   - Если "Failed" - смотрите логи
   - Если "Running" но 502 - проблема в конфигурации

#### B. Настроить переменные окружения в Railway:

**КРИТИЧЕСКИ ВАЖНО:** Добавьте эти переменные:

1. Перейдите в Settings → Variables
2. Добавьте/проверьте:

```
CORS_ORIGINS=https://xtrix.vercel.app
ENVIRONMENT=production
```

3. Убедитесь, что установлены:
   - `DATABASE_URL` (автоматически при подключении PostgreSQL)
   - `JWT_SECRET` (если не установлен - добавьте)

#### C. Применить миграции базы данных:

**ОБЯЗАТЕЛЬНО после деплоя:**

1. В Railway Dashboard → ваш проект → Deployments
2. Откройте последний deployment
3. Вкладка "Shell" или найдите кнопку "Open Shell"
4. Выполните:
   ```bash
   alembic upgrade head
   ```

Если Shell недоступен, используйте Railway CLI:
```bash
railway shell
alembic upgrade head
exit
```

#### D. Проверить логи:

1. Railway Dashboard → ваш проект → Deployments → последний deployment → Logs
2. Проверьте, есть ли ошибки при запуске
3. Типичные проблемы:
   - Отсутствует `DATABASE_URL` → подключите PostgreSQL
   - Отсутствует `JWT_SECRET` → добавьте переменную
   - Ошибки миграций → примените миграции вручную
   - Порт не слушается → проверьте, что порт 8000 экспонируется

### 2. Проверить и настроить Vercel (Frontend)

#### A. Проверить переменные окружения:

1. Откройте Vercel Dashboard: https://vercel.com
2. Ваш проект → Settings → Environment Variables
3. **Рекомендуется:** НЕ устанавливать `VITE_API_BASE_URL` (чтобы использовать `/api` через rewrite)
4. **Или** установите: `VITE_API_BASE_URL=https://xtrix-production.up.railway.app`

#### B. Проверить, что vercel.json правильный:

Убедитесь, что в `vercel.json` указан правильный Railway URL:
```json
{
  "rewrites": [
    {
      "source": "/api/(.*)",
      "destination": "https://xtrix-production.up.railway.app/$1"
    }
  ]
}
```

**Исправлено и запушено в git!** Если на Vercel старая версия - сделайте redeploy.

#### C. Передеплоить frontend:

1. Vercel Dashboard → ваш проект → Deployments
2. Нажмите на последний deployment → "Redeploy"
3. Или сделайте новый commit (если изменения уже запушены, Vercel передеплоит автоматически)

### 3. Проверить работу

#### Backend:
1. Откройте: `https://xtrix-production.up.railway.app/`
2. Должен вернуться JSON с информацией об API
3. Если 502 - backend не запущен, проверьте логи

#### Frontend:
1. Откройте: `https://xtrix.vercel.app`
2. Попробуйте залогиниться
3. Откройте DevTools → Console
4. Не должно быть CORS ошибок

## 📋 Чек-лист:

- [ ] Railway: Переменная `CORS_ORIGINS=https://xtrix.vercel.app` установлена
- [ ] Railway: Переменная `ENVIRONMENT=production` установлена
- [ ] Railway: Миграции применены (`alembic upgrade head`)
- [ ] Railway: Backend отвечает на `/` endpoint
- [ ] Vercel: `vercel.json` обновлен с правильным Railway URL
- [ ] Vercel: Frontend передеплоен
- [ ] Проверено: Нет CORS ошибок в браузере
- [ ] Проверено: Логин работает

## 🆘 Если проблемы остаются:

### Backend не запускается (502):
1. Проверьте логи в Railway
2. Убедитесь, что все переменные окружения установлены
3. Проверьте, что PostgreSQL подключен
4. Попробуйте перезапустить deployment

### CORS ошибки продолжаются:
1. Убедитесь, что `CORS_ORIGINS` содержит точный домен Vercel
2. Убедитесь, что `ENVIRONMENT=production`
3. Перезапустите backend deployment в Railway
4. Проверьте, что нет опечаток в домене

### Миграции не применяются:
1. Проверьте, что `DATABASE_URL` правильный
2. Попробуйте подключиться к базе через Railway CLI
3. Проверьте логи при выполнении миграций

## 📞 Дополнительная помощь:

См. подробные инструкции в:
- `RAILWAY_SETUP.md` - настройка Railway
- `VERCEL_SETUP.md` - настройка Vercel
- `DEPLOYMENT_STATUS.md` - общий статус деплоя


