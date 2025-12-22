# 🌐 Настройка Vercel для деплоя Frontend

## 🔧 Переменные окружения в Vercel

Для правильной работы frontend на Vercel нужно настроить переменные окружения:

### VITE_API_BASE_URL

**Рекомендуется:** Использовать относительный путь `/api` (по умолчанию), чтобы использовать rewrite в `vercel.json`.

**Или** установить прямой URL backend:

```
VITE_API_BASE_URL=https://xtrix-production.up.railway.app
```

**Как добавить в Vercel:**
1. Откройте проект в Vercel Dashboard
2. Перейдите в Settings → Environment Variables
3. Добавьте переменную:
   - **Key:** `VITE_API_BASE_URL`
   - **Value:** `https://xtrix-production.up.railway.app` (или оставьте пустым для использования `/api` через rewrite)
   - **Environment:** Production, Preview, Development

## 📝 Варианты конфигурации

### Вариант 1: Использовать rewrite (рекомендуется)

**Не устанавливайте** `VITE_API_BASE_URL`, тогда frontend будет использовать `/api`, который будет проксироваться через `vercel.json` на Railway backend.

**Преимущества:**
- Единая точка входа через Vercel
- Легче менять backend URL без пересборки
- Возможность использовать кеширование на edge

### Вариант 2: Прямой URL (текущая ошибка)

Если установить `VITE_API_BASE_URL=https://xtrix-production.up.railway.app`, запросы будут идти напрямую на Railway.

**В этом случае нужно:**
1. Настроить CORS на Railway (см. `RAILWAY_SETUP.md`)
2. Убедиться, что backend доступен

## ⚠️ Текущая проблема

Судя по ошибке, frontend делает запросы напрямую на Railway URL (`https://xtrix-production.up.railway.app/users/login`), что означает:

1. **Либо** установлена переменная `VITE_API_BASE_URL=https://xtrix-production.up.railway.app`
2. **Либо** есть проблема с rewrite в `vercel.json`

### Решение проблемы CORS:

**Если используете прямой URL (Вариант 2):**
1. В Railway Dashboard → Settings → Variables
2. Добавьте: `CORS_ORIGINS=https://xtrix.vercel.app`
3. Перезапустите deployment

**Если используете rewrite (Вариант 1):**
1. Убедитесь, что `VITE_API_BASE_URL` не установлен
2. Проверьте, что `vercel.json` правильно настроен (исправлен на Railway URL)

## 🔍 Проверка после деплоя

1. Откройте frontend в браузере
2. Откройте DevTools → Network
3. Попробуйте залогиниться
4. Проверьте, какой URL используется для запросов:
   - Если `/api/users/login` → rewrite работает ✅
   - Если `https://xtrix-production.up.railway.app/users/login` → используется прямой URL

## 📦 Пересборка после изменения переменных

После изменения переменных окружения в Vercel:
1. Перейдите в Deployments
2. Нажмите на последний deployment → "Redeploy"
3. Или сделайте новый commit и push (автоматический redeploy)

