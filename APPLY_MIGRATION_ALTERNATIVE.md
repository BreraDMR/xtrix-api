# 🔧 Альтернативные способы применить миграцию на Railway

## ✅ Решение 1: Автоматические миграции (УЖЕ СДЕЛАНО)

Создан `entrypoint.sh` который автоматически запускает миграции при каждом старте контейнера.

**Что делать:**
- Просто подождите, пока Railway передеплоит с новым кодом
- Миграции применятся автоматически при следующем запуске

## 🔄 Решение 2: Railway CLI команда `shell`

Если Railway CLI установлен:

```bash
railway shell
```

Затем в открывшемся shell выполните:
```bash
alembic upgrade head
exit
```

**Если команда `railway shell` не работает:**
- Убедитесь, что вы в правильной директории проекта
- Попробуйте `railway link` сначала
- Проверьте, что вы залогинены: `railway whoami`

## 🔄 Решение 3: Через Railway Dashboard → Service → Connect

1. Откройте Railway Dashboard
2. Перейдите в ваш проект
3. Выберите **Service** (не Deployment)
4. Найдите кнопку **"Connect"** или **"Terminal"**
5. Выполните: `alembic upgrade head`

## 🔄 Решение 4: Использовать Railway CLI напрямую

```bash
railway run --service your-service-name alembic upgrade head
```

Или просто:
```bash
railway run alembic upgrade head
```

## ⚡ Быстрое решение: Просто перезапустить deployment

Если миграции настроены автоматически (как сейчас), можно просто:

1. Railway Dashboard → ваш проект
2. Deployments → последний deployment
3. Нажмите **"Redeploy"** или **"Restart"**

Миграции применятся автоматически при следующем запуске благодаря `entrypoint.sh`.

## 🔍 Проверка текущего статуса миграций

После применения миграции проверьте:

```bash
railway run alembic current
```

Должно показать: `0011_add_specialist_types (head)`

Или через Railway Dashboard → Service → Connect:
```bash
alembic current
```

## 📝 Что уже сделано:

✅ Создан `entrypoint.sh` - автоматически запускает миграции  
✅ Обновлен `Dockerfile` - использует entrypoint script  
✅ Изменения запушены в git  
✅ Railway передеплоит автоматически  

**Просто подождите завершения деплоя и миграции применятся автоматически!**

