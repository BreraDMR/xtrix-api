# Настройка оплаты подписки через Stripe

Пошаговая инструкция по настройке оплаты подписок через Stripe.

## 1. Регистрация в Stripe

1. Зарегистрируйтесь на [Stripe](https://stripe.com/)
2. Войдите в [Stripe Dashboard](https://dashboard.stripe.com/)

## 2. Получение API ключей

1. В Stripe Dashboard перейдите: **Developers** → **API keys**
2. Убедитесь, что включен **Test mode** (переключатель вверху) для тестирования
3. Скопируйте **Secret key** (начинается с `sk_test_...` для тестового режима или `sk_live_...` для продакшена)
   - Это будет ваша переменная `STRIPE_API_KEY`

## 3. Настройка Webhook

Webhook нужен для получения уведомлений от Stripe о событиях оплаты.

### Для Production (Railway/Vercel):

#### Пошаговая инструкция:

1. **Войдите в Stripe Dashboard**: https://dashboard.stripe.com/
   - Убедитесь, что вы в нужном режиме (Test mode для тестирования или Live mode для продакшена)

2. **Перейдите в раздел Webhooks**:
   - В левом меню нажмите **"Developers"**
   - Затем выберите **"Webhooks"**

3. **Создайте новый endpoint**:
   - Нажмите кнопку **"Add endpoint"** (справа вверху)

4. **Укажите URL вашего API**:
   - В поле **"Endpoint URL"** введите:
     ```
     https://ваш-backend-домен.com/api/v1/subscriptions/webhooks/stripe
     ```
   - Например: `https://xtrix-production.up.railway.app/api/v1/subscriptions/webhooks/stripe`
   - ⚠️ **Важно**: Используйте HTTPS, не HTTP!

5. **Выберите события** (КРИТИЧЕСКИ ВАЖНО!):
   
   У вас есть два варианта:
   
   **Вариант А: Выбрать конкретные события (рекомендуется)**
   - Выберите **"Select events to listen to"**
   - В поиске начните вводить название события
   - Найдите и отметьте галочками следующие события:
     - ✅ `checkout.session.completed` - **ОБЯЗАТЕЛЬНО!** Создание подписки после успешной оплаты
     - ✅ `customer.subscription.created`
     - ✅ `customer.subscription.updated`
     - ✅ `customer.subscription.deleted`
     - ✅ `invoice.payment_succeeded`
     - ✅ `invoice.payment_failed`
   
   **Вариант Б: Выбрать все события (не рекомендуется)**
   - Можно выбрать **"Select all events"**, но это создаст много лишних запросов
   
   ⚠️ **КРИТИЧЕСКИ ВАЖНО**: Событие `checkout.session.completed` **ОБЯЗАТЕЛЬНО** должно быть в списке! Без него подписки не будут создаваться автоматически после оплаты.

6. **Сохраните endpoint**:
   - Нажмите кнопку **"Add endpoint"** внизу формы

7. **Скопируйте Signing secret**:
   - После создания endpoint, откроется страница с деталями webhook
   - Найдите раздел **"Signing secret"**
   - Нажмите кнопку **"Reveal"** или **"Click to reveal"** рядом с секретом
   - Скопируйте значение (начинается с `whsec_...`)
   - ⚠️ **Важно**: Сохраните этот секрет! Он понадобится для переменной `STRIPE_WEBHOOK_SECRET`

#### Если webhook endpoint уже существует:

Если вы уже создали webhook endpoint ранее и нужно добавить событие `checkout.session.completed`:

1. Перейдите в **Developers** → **Webhooks**
2. Найдите ваш существующий endpoint и кликните на него
3. Нажмите кнопку **"Add events"** или **"Edit events"**
4. В поиске найдите `checkout.session.completed` и отметьте галочкой
5. Нажмите **"Add events"** или **"Update"** для сохранения

### Для локальной разработки (Stripe CLI):

Для тестирования webhook локально используйте Stripe CLI:

1. Установите [Stripe CLI](https://stripe.com/docs/stripe-cli)
2. Авторизуйтесь:
   ```bash
   stripe login
   ```
3. Запустите форвардинг webhook событий:
   ```bash
   stripe listen --forward-to localhost:8000/api/v1/subscriptions/webhooks/stripe
   ```
4. Скопируйте **Signing secret** из вывода команды (начинается с `whsec_...`)

## 4. Настройка переменных окружения

Добавьте в файл `.env` (локально) или в Railway/Vercel (Settings → Variables):

```env
# Stripe API ключи
STRIPE_API_KEY=sk_test_... # Ваш Secret key из Stripe Dashboard
STRIPE_WEBHOOK_SECRET=whsec_... # Ваш Webhook signing secret

# URL фронтенда для редиректов после оплаты
FRONTEND_URL=https://your-frontend-domain.vercel.app
# Для локальной разработки:
# FRONTEND_URL=http://localhost:3000
```

## 5. Настройка FRONTEND_URL

Убедитесь, что `FRONTEND_URL` указывает на правильный адрес вашего фронтенда:

- **Production**: URL вашего фронтенда на Vercel или другом хостинге
- **Локальная разработка**: `http://localhost:3000` (или порт, на котором запущен фронтенд)

Этот URL используется для редиректов после успешной/отмененной оплаты.

## 6. Тестирование оплаты

### Тестовые карты Stripe:

Используйте тестовые карты от Stripe для проверки. **Полный список**: https://stripe.com/docs/testing#cards

**Основные тестовые карты:**

- **Успешная оплата**: `4242 4242 4242 4242`
- **Оплата требует аутентификацию (3D Secure)**: `4000 0025 0000 3155`
- **Отклоненная карта**: `4000 0000 0000 0002`
- **Недостаточно средств**: `4000 0000 0000 9995`
- **Неверный CVC**: `4000 0000 0000 0127`
- **Карта украдена**: `4000 0000 0000 9979`
- **Процессинговая ошибка**: `4000 0000 0000 0119`

**Для всех тестовых карт:**
- Дата истечения: любая будущая дата (например, `12/25`)
- CVC: любой 3 цифры (например, `123`)
- Почтовый индекс: любой (например, `12345`)

**Где найти:**
- Официальная документация: https://stripe.com/docs/testing
- В Stripe Dashboard: Developers → Testing → Test cards

### Проверка работы:

1. Убедитесь, что переменные окружения установлены
2. Перезапустите сервер (если изменяли .env)
3. В приложении перейдите в раздел подписок
4. Выберите план и нажмите "Подписаться"
5. Должен произойти редирект на страницу Stripe Checkout
6. Используйте тестовую карту для оплаты
7. После оплаты должен быть редирект обратно на ваш сайт

## 7. Переход на Production

Когда будете готовы к реальным платежам:

1. В Stripe Dashboard переключитесь на **Live mode**
2. Получите **Live API keys** (Secret key начинается с `sk_live_...`)
3. Создайте новый **Webhook endpoint** для production URL
4. Получите **Live webhook secret**
5. Обновите переменные окружения:
   ```env
   STRIPE_API_KEY=sk_live_...
   STRIPE_WEBHOOK_SECRET=whsec_... (новый для production)
   ```
6. Перезапустите сервер

## 8. Мониторинг платежей

- **Stripe Dashboard**: Все транзакции, подписки и события в реальном времени
- **Логи приложения**: Проверяйте логи для отслеживания webhook событий
- **База данных**: Все транзакции сохраняются в таблице `transactions`

## 9. Troubleshooting

### Ошибка "Stripe not configured"
- Убедитесь, что `STRIPE_API_KEY` установлен в `.env`
- Перезапустите сервер после изменения `.env`

### Webhook не работает
- Проверьте, что `STRIPE_WEBHOOK_SECRET` установлен
- Убедитесь, что webhook URL доступен из интернета (для production)
- Проверьте логи Stripe Dashboard → Webhooks для ошибок

### Редирект не работает после оплаты
- Проверьте, что `FRONTEND_URL` установлен правильно
- Убедитесь, что URL доступен и не имеет trailing slash

### Тестовая оплата не проходит
- Убедитесь, что используете тестовые карты Stripe
- Проверьте, что находитесь в Test mode в Stripe Dashboard
- Проверьте логи для деталей ошибок

## 10. Дополнительные ресурсы

- [Документация Stripe API](https://stripe.com/docs/api)
- [Stripe Checkout](https://stripe.com/docs/payments/checkout)
- [Stripe Webhooks](https://stripe.com/docs/webhooks)
- [Stripe Testing](https://stripe.com/docs/testing)
