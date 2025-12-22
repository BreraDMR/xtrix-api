# 🗄️ КРИТИЧЕСКИ ВАЖНО: Применить миграцию на Railway

## ❌ Текущая проблема:

Ошибка: `column users.specialist_type does not exist`

**Причина:** Миграция `0011_add_specialist_types.py` не была применена на Railway.

## ✅ Решение: Применить миграцию

### Вариант 1: Через Railway Dashboard (РЕКОМЕНДУЕТСЯ)

1. **Откройте Railway Dashboard:**
   - Перейдите на https://railway.com
   - Войдите в ваш проект

2. **Найдите последний deployment:**
   - Перейдите в раздел "Deployments"
   - Найдите последний успешный deployment

3. **Откройте Shell:**
   - Нажмите на deployment
   - Найдите кнопку "Shell" или "Open Shell" / "Connect"
   - Или перейдите во вкладку "Shell"

4. **Примените миграцию:**
   ```bash
   alembic upgrade head
   ```

5. **Проверьте результат:**
   Должно появиться что-то вроде:
   ```
   INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
   INFO  [alembic.runtime.migration] Will assume transactional DDL.
   INFO  [alembic.runtime.migration] Running upgrade 0010_... -> 0011_add_specialist_types, Add specialist_type to users and create specialist_profiles table
   ```

### Вариант 2: Через Railway CLI

1. **Убедитесь, что Railway CLI установлен:**
   ```bash
   railway --version
   ```

2. **Подключитесь к проекту:**
   ```bash
   railway link
   ```
   (если еще не подключен)

3. **Откройте shell и примените миграцию:**
   ```bash
   railway shell
   alembic upgrade head
   exit
   ```

### Вариант 3: Через один запуск команды (Railway CLI)

```bash
railway run alembic upgrade head
```

**НО:** Этот вариант может не работать, если нужно подключение к базе данных, которое уже настроено в Railway.

## 🔍 Проверка после применения миграции

1. **Попробуйте залогиниться снова:**
   - Ошибка должна исчезнуть
   - Логин должен работать

2. **Проверьте через API:**
   Откройте в браузере:
   ```
   https://xtrix-production.up.railway.app/docs
   ```
   
   Попробуйте endpoint `POST /users/` и создайте пользователя с `specialist_type`

3. **Проверьте в базе данных (опционально):**
   Если есть доступ к базе данных через Railway:
   ```sql
   SELECT column_name 
   FROM information_schema.columns 
   WHERE table_name = 'users' AND column_name = 'specialist_type';
   ```
   
   Должна вернуться строка с `specialist_type`

## ⚠️ Важно

- **НЕ делайте** `alembic downgrade` - это удалит данные
- Миграция безопасна - она добавляет nullable колонку, существующие пользователи не пострадают
- После применения миграции перезапускать deployment НЕ нужно - изменения применяются сразу

## 📝 Что делает миграция:

1. Добавляет колонку `specialist_type` в таблицу `users` (nullable)
2. Создает таблицу `specialist_profiles` с расширенной информацией о специалистах
3. Создает индексы для быстрого поиска

## 🆘 Если миграция не применяется:

1. **Проверьте переменную DATABASE_URL:**
   - Railway Dashboard → Settings → Variables
   - Убедитесь, что `DATABASE_URL` установлена
   - Она должна автоматически создаваться при подключении PostgreSQL

2. **Проверьте логи:**
   - Railway Dashboard → Deployments → Logs
   - Ищите ошибки при выполнении `alembic upgrade head`

3. **Попробуйте проверить текущую версию миграции:**
   ```bash
   railway run alembic current
   ```
   
   Должно показать текущую версию, например: `0010_... (head)`
   
   После применения должно быть: `0011_add_specialist_types (head)`

4. **Если миграция уже применена, но ошибка остается:**
   - Перезапустите deployment в Railway
   - Проверьте, что код обновлен (последний коммит задеплоен)

