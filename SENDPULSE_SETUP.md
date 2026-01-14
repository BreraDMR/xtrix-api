# Настройка интеграции с SendPulse

Интеграция с SendPulse позволяет использовать единую платформу для всех email и SMS рассылок, включая:
- Напоминания о визитах
- Поздравления с днём рождения
- Follow-up сообщения после визита
- Уведомления о программе лояльности
- 2FA коды (SMS)
- Другие уведомления

## 1. Получение API ключей

1. Зарегистрируйтесь на [SendPulse](https://sendpulse.com/)
2. Перейдите в раздел **Настройки** → **API**
3. Создайте новое приложение и получите:
   - **Client ID** (Client Identifier)
   - **Client Secret** (Client Secret)

## 2. Настройка переменных окружения

Добавьте в файл `.env`:

```env
# SendPulse API credentials
SENDPULSE_CLIENT_ID=your_client_id_here
SENDPULSE_CLIENT_SECRET=your_client_secret_here

# SendPulse Address Book ID (опционально, можно создать через API)
SENDPULSE_ADDRESS_BOOK_ID=0

# Настройки отправителя для email
SENDPULSE_FROM_EMAIL=noreply@yourdomain.com
SENDPULSE_FROM_NAME=Your Business Name

# Настройки отправителя для SMS
SENDPULSE_SMS_SENDER=YourBusiness
```

## 2.1. Настройка адреса отправителя в SendPulse (важно!)

После настройки переменных окружения, необходимо добавить адрес отправителя в SendPulse:

### Шаг 1: Добавление адреса отправителя
1. Войдите в аккаунт SendPulse: https://sendpulse.com/
2. Перейдите: **Настройки сервиса** → **Адреса отправителя** (или **Settings** → **Sender addresses**)
3. Нажмите **"Новый адрес отправителя"** / **"Add new sender address"**
4. Введите:
   - **Email**: ваш email адрес (например, `xtrix@post.cz`)
   - **Имя отправителя**: имя для отображения (например, `Xtrix`)
5. Нажмите **"Добавить"** / **"Add"**

### Шаг 2: Подтверждение адреса
- На указанный email адрес будет отправлено письмо с подтверждением
- Откройте письмо и следуйте инструкциям для активации адреса
- После подтверждения адрес будет доступен для отправки

### Шаг 3: Настройка аутентификации домена (рекомендуется)
Для лучшей доставляемости писем рекомендуется настроить SPF и DKIM:

1. В SendPulse: **Настройки сервиса** → **Аутентификация** (или **Settings** → **Domain authentication**)
2. Добавьте ваш домен (например, `post.cz`)
3. Получите DNS-записи (SPF, DKIM) от SendPulse
4. Добавьте эти записи в настройки DNS вашего домена

**Важно**: Если у вас нет доступа к DNS настройкам домена, можно пропустить этот шаг - SendPulse позволяет отправку и без верификации домена (с некоторыми ограничениями).

**Документация SendPulse:**
- [Как добавить адрес отправителя](https://sendpulse.com/ru/knowledge-base/email-service/sendpulse-basics/add-new-from-email-address)
- [Настройка аутентификации домена](https://sendpulse.com/ru/knowledge-base/email-service/sendpulse-basics/email-basics)

## 3. Создание адресной книги

Адресная книга используется для хранения контактов. Вы можете:

1. Создать адресную книгу в интерфейсе SendPulse
2. Получить ID адресной книги из URL или через API
3. Указать ID в переменной `SENDPULSE_ADDRESS_BOOK_ID`

Или использовать API для получения списка адресных книг:

```python
from app.api.core.sendpulse import get_sendpulse_client

client = get_sendpulse_client()
books = client.get_address_books()
print(books)
```

## 4. Синхронизация контактов

### Автоматическая синхронизация всех контактов

```bash
# Синхронизировать всех клиентов и пользователей
python scripts/sync_sendpulse.py --type all

# Только клиенты
python scripts/sync_sendpulse.py --type clients

# Только пользователи
python scripts/sync_sendpulse.py --type users

# С ограничением количества
python scripts/sync_sendpulse.py --type all --limit 100

# С указанием адресной книги
python scripts/sync_sendpulse.py --type all --address-book-id 12345
```

### Настройка автоматической синхронизации через cron

Добавьте в crontab для ежедневной синхронизации:

```bash
# Синхронизация каждый день в 2:00 ночи
0 2 * * * cd /path/to/project && python scripts/sync_sendpulse.py --type all
```

## 5. Использование SendPulse для уведомлений

После настройки SendPulse, вы можете использовать функции из `app/api/core/notify_sendpulse.py`:

```python
from app.api.core.notify_sendpulse import (
    send_email_via_sendpulse,
    send_sms_via_sendpulse,
    send_visit_reminder_email_sendpulse,
    send_birthday_greeting_email_sendpulse,
    send_verification_code_sendpulse,
    send_loyalty_notification_email_sendpulse,
)

# Отправка email
send_email_via_sendpulse(
    email="client@example.com",
    subject="Тема письма",
    html_content="<h1>Привет!</h1>",
    text_content="Привет!",
)

# Отправка SMS
send_sms_via_sendpulse(
    phone="+1234567890",
    message="Ваше сообщение",
)

# Отправка кода верификации
send_verification_code_sendpulse(
    phone="+1234567890",
    code="123456",
)
```

## 6. Интеграция в существующие модули

Для переключения на SendPulse в существующих модулях уведомлений, замените вызовы функций:

**Было (SMTP/Twilio):**
```python
from app.api.core.notify import send_email_notification, send_sms_notification
```

**Стало (SendPulse):**
```python
from app.api.core.notify_sendpulse import send_email_via_sendpulse, send_sms_via_sendpulse
```

## 7. Программная синхронизация контактов

Вы можете синхронизировать контакты программно:

```python
from app.db.session import SessionLocal
from app.api.core.sendpulse_sync import (
    sync_client_to_sendpulse,
    sync_user_to_sendpulse,
    sync_all_contacts_to_sendpulse,
)

db = SessionLocal()

# Синхронизировать одного клиента
sync_client_to_sendpulse(db, client_id=1)

# Синхронизировать одного пользователя
sync_user_to_sendpulse(db, user_id=1)

# Синхронизировать всех контактов
results = sync_all_contacts_to_sendpulse()
print(results)

db.close()
```

## 8. Мониторинг и логирование

Все операции логируются. Проверьте логи для отслеживания синхронизации и отправки:

```bash
tail -f logs/app.log | grep -i sendpulse
```

## 9. Автоматическая синхронизация при создании контакта

Для автоматической синхронизации при создании/обновлении клиента или пользователя, добавьте в соответствующие CRUD функции:

```python
from app.api.core.sendpulse_sync import sync_client_to_sendpulse

# После создания/обновления клиента
sync_client_to_sendpulse(db, client.id)
```

## 10. Troubleshooting

### Ошибка "SendPulse client_id and client_secret must be configured"
- Убедитесь, что переменные `SENDPULSE_CLIENT_ID` и `SENDPULSE_CLIENT_SECRET` установлены в `.env`

### Ошибка "Failed to get access token"
- Проверьте правильность Client ID и Client Secret
- Убедитесь, что API включено в настройках SendPulse

### Контакты не синхронизируются
- Проверьте, что адресная книга существует и ID указан правильно
- Проверьте логи для деталей ошибок

### Email/SMS не отправляются
- Убедитесь, что `SENDPULSE_FROM_EMAIL` и `SENDPULSE_SMS_SENDER` настроены
- Проверьте баланс на счету SendPulse
- Проверьте логи для деталей ошибок

## 11. Дополнительные ресурсы

- [Документация SendPulse API](https://sendpulse.com/api)
- [SendPulse Knowledge Base](https://sendpulse.com/ru/knowledge-base)
