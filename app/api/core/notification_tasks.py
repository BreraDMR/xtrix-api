from datetime import datetime, date, timedelta
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_, func
from app.db.session import SessionLocal
from app.api.core.logger import logger
from app.db.models.client import Client
from app.db.models.visit import Visit
from app.db.models.business import Business
from app.api.core.notify import (
    send_notifications,
    send_birthday_greeting_email,
    send_birthday_greeting_sms,
    send_birthday_greeting_whatsapp,
    send_visit_followup_email,
    send_visit_followup_sms,
    send_visit_followup_whatsapp,
)
from app.api.core.loyalty_logic import award_birthday_bonus
def send_automatic_reminders(channel: str = "email") -> int:
    db: Session = SessionLocal()
    try:
        now = datetime.utcnow()
        horizon = now + timedelta(hours=24)
        visits = db.query(Visit).filter(
            and_(
                Visit.date_start >= now,
                Visit.date_start <= horizon,
                Visit.status == "planned",
                Visit.deleted_at.is_(None),
                Visit.archived_at.is_(None),
            )
        ).all()
        if not visits:
            logger.info("No visits to remind")
            return 0
        client_ids = {v.client_id for v in visits}
        clients = db.query(Client).filter(Client.id.in_(client_ids)).all()
        contacts = {
            c.id: {"email": c.contact_email, "phone": c.contact_phone} for c in clients
        }
        sent = send_notifications(visits, channel=channel, contacts=contacts)
        logger.info(f"Sent {sent} reminders via {channel}")
        return sent
    except Exception as e:
        logger.error(f"Error sending automatic reminders: {e}", exc_info=True)
        raise
    finally:
        db.close()
def send_birthday_greetings(channel: str = "email") -> int:
    db: Session = SessionLocal()
    try:
        today = date.today()
        clients = db.query(Client).filter(
            and_(
                Client.birth_date.isnot(None),
                func.extract('month', Client.birth_date) == today.month,
                func.extract('day', Client.birth_date) == today.day,
                Client.deleted_at.is_(None),
            )
        ).all()
        if not clients:
            logger.info("No birthdays today")
            return 0
        sent_count = 0
        for client in clients:
            try:
                from app.db.models.client_business_relation import ClientBusinessRelation
                business_relations = db.query(ClientBusinessRelation).filter(
                    ClientBusinessRelation.client_id == client.id
                ).all()
                if not business_relations:
                    continue
                for relation in business_relations:
                    business = db.query(Business).filter(Business.id == relation.business_id).first()
                    business_name = business.name if business else None
                    if channel == "email" and client.contact_email:
                        ok = send_birthday_greeting_email(client.contact_email, client.full_name, business_name)
                        if ok:
                            sent_count += 1
                            logger.info(f"Sent birthday email to {client.full_name} ({client.contact_email})")
                    elif channel == "sms" and client.contact_phone:
                        ok = send_birthday_greeting_sms(client.contact_phone, client.full_name, business_name)
                        if ok:
                            sent_count += 1
                            logger.info(f"Sent birthday SMS to {client.full_name} ({client.contact_phone})")
                    elif channel == "whatsapp" and client.contact_phone:
                        ok = send_birthday_greeting_whatsapp(client.contact_phone, client.full_name, business_name)
                        if ok:
                            sent_count += 1
                            logger.info(f"Sent birthday WhatsApp to {client.full_name} ({client.contact_phone})")
                    try:
                        award_birthday_bonus(db, client.id, relation.business_id)
                    except Exception as e:
                        logger.warning(f"Failed to award birthday bonus for client {client.id}, business {relation.business_id}: {e}")
            except Exception as e:
                logger.error(f"Error sending birthday greeting to client {client.id}: {e}")
                continue
        logger.info(f"Sent {sent_count} birthday greetings via {channel}")
        return sent_count
    except Exception as e:
        logger.error(f"Error sending birthday greetings: {e}", exc_info=True)
        raise
    finally:
        db.close()
def send_visit_followups(channel: str = "email", hours_after: int = 24) -> int:
    db: Session = SessionLocal()
    try:
        now = datetime.utcnow()
        cutoff_time = now - timedelta(hours=hours_after)
        start_time = cutoff_time - timedelta(hours=1)
        visits = db.query(Visit).filter(
            and_(
                Visit.status == "completed",
                Visit.date_end.isnot(None),
                Visit.date_end >= start_time,
                Visit.date_end <= cutoff_time,
                Visit.deleted_at.is_(None),
                Visit.archived_at.is_(None),
            )
        ).all()
        if not visits:
            logger.info("No visits for follow-up")
            return 0
        sent_count = 0
        for visit in visits:
            try:
                client = db.query(Client).filter(Client.id == visit.client_id).first()
                if not client:
                    continue
                business_name = None
                if visit.business_id:
                    business = db.query(Business).filter(Business.id == visit.business_id).first()
                    business_name = business.name if business else None
                service_name = visit.service_name or (visit.service.name if visit.service else str(visit.service_id))
                if channel == "email" and client.contact_email:
                    ok = send_visit_followup_email(
                        client.contact_email,
                        client.full_name,
                        visit.date_end or visit.date_start,
                        service_name,
                        business_name
                    )
                    if ok:
                        sent_count += 1
                        logger.info(f"Sent follow-up email for visit {visit.id} to {client.full_name}")
                elif channel == "sms" and client.contact_phone:
                    ok = send_visit_followup_sms(
                        client.contact_phone,
                        client.full_name,
                        visit.date_end or visit.date_start,
                        service_name,
                        business_name
                    )
                    if ok:
                        sent_count += 1
                        logger.info(f"Sent follow-up SMS for visit {visit.id} to {client.full_name}")
                elif channel == "whatsapp" and client.contact_phone:
                    ok = send_visit_followup_whatsapp(
                        client.contact_phone,
                        client.full_name,
                        visit.date_end or visit.date_start,
                        service_name,
                        business_name
                    )
                    if ok:
                        sent_count += 1
                        logger.info(f"Sent follow-up WhatsApp for visit {visit.id} to {client.full_name}")
            except Exception as e:
                logger.error(f"Error sending follow-up for visit {visit.id}: {e}")
                continue
        logger.info(f"Sent {sent_count} follow-up messages via {channel}")
        return sent_count
    except Exception as e:
        logger.error(f"Error sending visit follow-ups: {e}", exc_info=True)
        raise
    finally:
        db.close()
def run_all_notification_tasks(channel: str = "email") -> dict:
    results = {
        "reminders": 0,
        "birthday_greetings": 0,
        "followups": 0,
    }
    try:
        results["reminders"] = send_automatic_reminders(channel=channel)
        results["birthday_greetings"] = send_birthday_greetings(channel=channel)
        results["followups"] = send_visit_followups(channel=channel)
        logger.info(f"Notification tasks completed: {results}")
        return results
    except Exception as e:
        logger.error(f"Error running notification tasks: {e}", exc_info=True)
        raise
