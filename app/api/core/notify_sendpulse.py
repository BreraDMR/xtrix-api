import logging
from typing import Optional
from datetime import datetime
from app.api.core.sendpulse import get_sendpulse_client
from app.api.core.logger import logger, mask_email, mask_phone
def send_email_via_sendpulse(
    email: str,
    subject: str,
    html_content: Optional[str] = None,
    text_content: Optional[str] = None,
) -> bool:
    try:
        client = get_sendpulse_client()
        return client.send_email(
            email=email,
            subject=subject,
            html=html_content,
            text=text_content,
        )
    except Exception as e:
        logger.error(f"Failed to send email via SendPulse to {mask_email(email)}: {e}")
        return False
def send_sms_via_sendpulse(phone: str, message: str) -> bool:
    try:
        client = get_sendpulse_client()
        return client.send_sms(phone=phone, message=message)
    except Exception as e:
        logger.error(f"Failed to send SMS via SendPulse to {mask_phone(phone)}: {e}")
        return False
def send_visit_reminder_email_sendpulse(email: str, visit, business_name: Optional[str] = None) -> bool:
    service_name = getattr(visit, 'service_name', '') or getattr(visit, 'service_id', '')
    visit_date = visit.date_start.strftime("%d.%m.%Y %H:%M") if isinstance(visit.date_start, datetime) else str(visit.date_start)
    business_text = f" от {business_name}" if business_name else ""
    subject = "Напоминание о визите"
    html = f"""
    <html>
    <body>
        <h2>Напоминание о визите</h2>
        <p>Услуга: {service_name}</p>
        <p>Дата и время: {visit_date}</p>
        <p>С уважением,<br>команда{business_text}</p>
    </body>
    </html>
    """
    text = f"Напоминание о визите\n\nУслуга: {service_name}\nДата и время: {visit_date}\n\nС уважением,\nкоманда{business_text}"
    return send_email_via_sendpulse(email, subject, html, text)
def send_visit_reminder_sms_sendpulse(phone: str, visit, business_name: Optional[str] = None) -> bool:
    service_name = getattr(visit, 'service_name', '') or getattr(visit, 'service_id', '')
    visit_date = visit.date_start.strftime("%d.%m.%Y %H:%M") if isinstance(visit.date_start, datetime) else str(visit.date_start)
    business_text = f" от {business_name}" if business_name else ""
    message = f"Напоминание: визит {visit_date}. Услуга: {service_name}. Команда{business_text}"
    return send_sms_via_sendpulse(phone, message)
def send_birthday_greeting_email_sendpulse(email: str, client_name: str, business_name: Optional[str] = None) -> bool:
    business_text = f" от {business_name}" if business_name else ""
    subject = "🎉 С Днём Рождения!"
    html = f"""
    <html>
    <body>
        <h2>🎉 С Днём Рождения, {client_name}!</h2>
        <p>Поздравляем Вас с Днём Рождения! 🎂🎉🎁</p>
        <p>Желаем Вам крепкого здоровья, счастья, благополучия и всего самого наилучшего!</p>
        <p>С уважением,<br>команда{business_text}</p>
    </body>
    </html>
    """
    text = f"🎉 С Днём Рождения, {client_name}!\n\nПоздравляем Вас с Днём Рождения! 🎂🎉🎁\n\nЖелаем Вам крепкого здоровья, счастья, благополучия и всего самого наилучшего!\n\nС уважением,\nкоманда{business_text}"
    return send_email_via_sendpulse(email, subject, html, text)
def send_birthday_greeting_sms_sendpulse(phone: str, client_name: str, business_name: Optional[str] = None) -> bool:
    business_text = f" от {business_name}" if business_name else ""
    message = f"🎉 Дорогая(ой) {client_name}! Поздравляем Вас с Днём Рождения! Желаем здоровья, счастья и благополучия! С уважением, команда{business_text}"
    return send_sms_via_sendpulse(phone, message)
def send_followup_email_sendpulse(
    email: str,
    client_name: str,
    visit_date: datetime,
    service_name: str,
    business_name: Optional[str] = None,
) -> bool:
    business_text = f" от {business_name}" if business_name else ""
    visit_date_str = visit_date.strftime("%d.%m.%Y %H:%M") if isinstance(visit_date, datetime) else str(visit_date)
    subject = "Спасибо за визит!"
    html = f"""
    <html>
    <body>
        <h2>Дорогая(ой) {client_name}!</h2>
        <p>Спасибо, что посетили нас {visit_date_str}!</p>
        <p>Мы надеемся, что услуга "{service_name}" была для Вас полезной и приятной.</p>
        <p>Будем рады видеть Вас снова!</p>
        <p>С уважением,<br>команда{business_text}</p>
    </body>
    </html>
    """
    text = f"Дорогая(ой) {client_name}!\n\nСпасибо, что посетили нас {visit_date_str}!\n\nМы надеемся, что услуга '{service_name}' была для Вас полезной и приятной.\n\nБудем рады видеть Вас снова!\n\nС уважением,\nкоманда{business_text}"
    return send_email_via_sendpulse(email, subject, html, text)
def send_followup_sms_sendpulse(
    phone: str,
    client_name: str,
    visit_date: datetime,
    service_name: str,
    business_name: Optional[str] = None,
) -> bool:
    business_text = f" от {business_name}" if business_name else ""
    visit_date_str = visit_date.strftime("%d.%m.%Y %H:%M") if isinstance(visit_date, datetime) else str(visit_date)
    message = f"Дорогая(ой) {client_name}! Спасибо за визит {visit_date_str}. Надеемся, услуга '{service_name}' была полезна. Будем рады видеть Вас снова! Команда{business_text}"
    return send_sms_via_sendpulse(phone, message)
def send_verification_code_sendpulse(phone: str, code: str) -> bool:
    message = f"Ваш код верификации: {code}"
    return send_sms_via_sendpulse(phone, message)
def send_loyalty_notification_email_sendpulse(
    email: str,
    client_name: str,
    message: str,
    business_name: Optional[str] = None,
) -> bool:
    business_text = f" от {business_name}" if business_name else ""
    subject = "Программа лояльности"
    html = f"""
    <html>
    <body>
        <h2>Дорогая(ой) {client_name}!</h2>
        <p>{message}</p>
        <p>С уважением,<br>команда{business_text}</p>
    </body>
    </html>
    """
    text = f"Дорогая(ой) {client_name}!\n\n{message}\n\nС уважением,\nкоманда{business_text}"
    return send_email_via_sendpulse(email, subject, html, text)
def send_loyalty_notification_sms_sendpulse(
    phone: str,
    client_name: str,
    message: str,
    business_name: Optional[str] = None,
) -> bool:
    business_text = f" от {business_name}" if business_name else ""
    full_message = f"{client_name}, {message} Команда{business_text}"
    return send_sms_via_sendpulse(phone, full_message)
