# Настройка SMTP для отправки email (Восстановление пароля)

## Вариант 1: Gmail (Рекомендуется - бесплатно)

### Шаги настройки:

1. **Создайте App Password в Gmail:**
   - Перейдите в [Google Account Settings](https://myaccount.google.com/)
   - Безопасность → Двухэтапная аутентификация (включите, если не включена)
   - Безопасность → Пароли приложений
   - Создайте новый пароль приложения для "Почта" → "Другое"
   - Скопируйте 16-значный пароль

2. **Добавьте переменные в Railway:**
   - Перейдите в Railway Dashboard → Your Project → Variables
   - Добавьте следующие переменные:

   ```
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USER=ваш_email@gmail.com
   SMTP_PASSWORD=ваш_16_значный_пароль_приложения
   SMTP_FROM=ваш_email@gmail.com
   ENVIRONMENT=production
   ```

### Пример:
```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=nataliekonopikhina@gmail.com
SMTP_PASSWORD=abcd efgh ijkl mnop
SMTP_FROM=nataliekonopikhina@gmail.com
```

---

## Вариант 2: SendGrid (Бесплатно до 100 email/день)

1. **Зарегистрируйтесь на [SendGrid](https://sendgrid.com/)**
2. **Создайте API Key:**
   - Settings → API Keys → Create API Key
   - Скопируйте ключ

3. **Добавьте в Railway:**
   ```
   SMTP_HOST=smtp.sendgrid.net
   SMTP_PORT=587
   SMTP_USER=apikey
   SMTP_PASSWORD=ваш_sendgrid_api_key
   SMTP_FROM=noreply@yourdomain.com
   ENVIRONMENT=production
   ```

---

## Вариант 3: Mailgun (Бесплатно до 5000 email/месяц)

1. **Зарегистрируйтесь на [Mailgun](https://www.mailgun.com/)**
2. **Получите SMTP credentials:**
   - Sending → Domain Settings → SMTP credentials

3. **Добавьте в Railway:**
   ```
   SMTP_HOST=smtp.mailgun.org
   SMTP_PORT=587
   SMTP_USER=ваш_mailgun_username
   SMTP_PASSWORD=ваш_mailgun_password
   SMTP_FROM=noreply@yourdomain.com
   ENVIRONMENT=production
   ```

---

## Проверка настройки

После добавления переменных в Railway:
1. Railway автоматически перезапустит сервис
2. Попробуйте восстановить пароль через форму "Forgot password?"
3. Проверьте логи в Railway, чтобы убедиться, что email отправлен

---

## Важно

- **ENVIRONMENT=production** - обязательно установите, чтобы код не показывался на экране
- Gmail может потребовать разрешения для "ненадежных приложений" - используйте App Password
- Пароль приложения Gmail состоит из 16 символов с пробелами (можно вводить с пробелами или без)

