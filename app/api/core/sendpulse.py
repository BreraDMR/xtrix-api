"""
Интеграция с SendPulse API для email и SMS рассылок.
Документация API: https://sendpulse.com/api
"""
import logging
import time
from typing import Optional, Dict, List, Any
from datetime import datetime

import requests

from app.api.core.config import settings
from app.api.core.logger import logger

# SendPulse API endpoints
SENDPULSE_API_URL = "https://api.sendpulse.com"
SENDPULSE_OAUTH_URL = f"{SENDPULSE_API_URL}/oauth/access_token"


class SendPulseClient:
    """Клиент для работы с SendPulse API."""
    
    def __init__(self):
        self.client_id = settings.sendpulse_client_id
        self.client_secret = settings.sendpulse_client_secret
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0
    
    def _get_access_token(self) -> str:
        """
        Получить access token для API.
        Токен кэшируется до истечения срока действия.
        """
        # Проверяем, действителен ли текущий токен
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token
        
        if not self.client_id or not self.client_secret:
            raise ValueError("SendPulse client_id and client_secret must be configured")
        
        # Получаем новый токен
        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        
        try:
            response = requests.post(SENDPULSE_OAUTH_URL, json=data, timeout=10)
            response.raise_for_status()
            result = response.json()
            
            self._access_token = result.get("access_token")
            expires_in = result.get("expires_in", 3600)  # По умолчанию 1 час
            self._token_expires_at = time.time() + expires_in - 60  # Вычитаем 60 сек для безопасности
            
            if not self._access_token:
                raise ValueError("Failed to get access token from SendPulse")
            
            logger.info("SendPulse access token obtained successfully")
            return self._access_token
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to get SendPulse access token: {e}")
            raise
    
    def _make_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        Выполнить запрос к SendPulse API.
        
        Args:
            method: HTTP метод (GET, POST, PUT, DELETE)
            endpoint: API endpoint (без базового URL)
            data: Данные для отправки (JSON)
            params: Query параметры
        
        Returns:
            Ответ API как словарь
        """
        token = self._get_access_token()
        url = f"{SENDPULSE_API_URL}/{endpoint.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        
        try:
            response = requests.request(
                method,
                url,
                json=data,
                params=params,
                headers=headers,
                timeout=30,
            )
            response.raise_for_status()
            return response.json() if response.content else {}
            
        except requests.exceptions.RequestException as e:
            logger.error(f"SendPulse API request failed: {method} {endpoint}, error: {e}")
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_data = e.response.json()
                    logger.error(f"SendPulse API error response: {error_data}")
                except:
                    logger.error(f"SendPulse API error response: {e.response.text}")
            raise
    
    def create_or_update_contact(
        self,
        email: Optional[str] = None,
        phone: Optional[str] = None,
        name: Optional[str] = None,
        address_book_id: Optional[int] = None,
        variables: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Создать или обновить контакт в адресной книге SendPulse.
        
        Args:
            email: Email адрес
            phone: Номер телефона
            name: Имя контакта
            address_book_id: ID адресной книги (если не указан, используется адресная книга по умолчанию)
            variables: Дополнительные переменные контакта
        
        Returns:
            True если успешно, False в противном случае
        """
        if not email and not phone:
            logger.warning("Cannot create contact without email or phone")
            return False
        
        address_book_id = address_book_id or settings.sendpulse_address_book_id
        
        if not address_book_id:
            logger.warning("SendPulse address_book_id not configured, using default")
            # Пытаемся получить список адресных книг и использовать первую
            try:
                books = self.get_address_books()
                if books:
                    address_book_id = books[0].get("id")
                else:
                    logger.error("No address books found in SendPulse")
                    return False
            except Exception as e:
                logger.error(f"Failed to get address books: {e}")
                return False
        
        contact_data = {
            "email": email,
            "phone": phone,
            "variables": variables or {},
        }
        
        if name:
            contact_data["variables"]["name"] = name
            if not contact_data["variables"].get("full_name"):
                contact_data["variables"]["full_name"] = name
        
        try:
            # Добавляем контакт в адресную книгу
            result = self._make_request(
                "POST",
                f"/addressbooks/{address_book_id}/emails",
                data=contact_data,
            )
            
            logger.info(f"Contact created/updated in SendPulse: email={email}, phone={phone}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to create/update contact in SendPulse: {e}")
            return False
    
    def get_address_books(self) -> List[Dict[str, Any]]:
        """
        Получить список адресных книг.
        
        Returns:
            Список адресных книг
        """
        try:
            result = self._make_request("GET", "/addressbooks")
            return result.get("data", [])
        except Exception as e:
            logger.error(f"Failed to get address books: {e}")
            return []
    
    def send_email(
        self,
        email: str,
        subject: str,
        html: Optional[str] = None,
        text: Optional[str] = None,
        from_email: Optional[str] = None,
        from_name: Optional[str] = None,
        attachments: Optional[List[Dict]] = None,
    ) -> bool:
        """
        Отправить email через SendPulse.
        
        Args:
            email: Email получателя
            subject: Тема письма
            html: HTML содержимое
            text: Текстовое содержимое
            from_email: Email отправителя (если не указан, используется из настроек)
            from_name: Имя отправителя
            attachments: Вложения
        
        Returns:
            True если успешно отправлено, False в противном случае
        """
        from_email = from_email or settings.sendpulse_from_email
        from_name = from_name or settings.sendpulse_from_name
        
        if not from_email:
            logger.warning("SendPulse from_email not configured")
            return False
        
        email_data = {
            "email": {
                "html": html or text,
                "text": text or (html.replace("<br>", "\n").replace("<br/>", "\n") if html else ""),
                "subject": subject,
                "from": {
                    "name": from_name,
                    "email": from_email,
                },
                "to": [
                    {
                        "name": "",
                        "email": email,
                    }
                ],
            }
        }
        
        if attachments:
            email_data["email"]["attachments"] = attachments
        
        try:
            result = self._make_request("POST", "/smtp/emails", data=email_data)
            logger.info(f"Email sent via SendPulse to {email}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send email via SendPulse: {e}")
            return False
    
    def send_sms(
        self,
        phone: str,
        message: str,
        sender: Optional[str] = None,
    ) -> bool:
        """
        Отправить SMS через SendPulse.
        
        Args:
            phone: Номер телефона получателя (формат: +1234567890)
            message: Текст сообщения
            sender: Имя отправителя (если не указано, используется из настроек)
        
        Returns:
            True если успешно отправлено, False в противном случае
        """
        sender = sender or settings.sendpulse_sms_sender
        
        if not sender:
            logger.warning("SendPulse SMS sender not configured")
            return False
        
        # Форматируем номер телефона
        if not phone.startswith("+"):
            phone = f"+{phone}"
        
        sms_data = {
            "sender": sender,
            "phones": [phone],
            "body": message,
        }
        
        try:
            result = self._make_request("POST", "/sms/send", data=sms_data)
            logger.info(f"SMS sent via SendPulse to {phone}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send SMS via SendPulse: {e}")
            return False


# Глобальный экземпляр клиента
_sendpulse_client: Optional[SendPulseClient] = None


def get_sendpulse_client() -> SendPulseClient:
    """Получить глобальный экземпляр SendPulse клиента."""
    global _sendpulse_client
    if _sendpulse_client is None:
        _sendpulse_client = SendPulseClient()
    return _sendpulse_client
