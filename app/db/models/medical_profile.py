from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, Text
from sqlalchemy.orm import relationship

from app.db.base import Base


class MedicalProfile(Base):
    """
    Модель медицинского профиля клиента.
    Содержит медицинские данные клиента, доступные только клиникам и медицинским учреждениям.
    Один профиль на одного клиента (unique constraint).
    """
    __tablename__ = "medical_profiles"

    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    
    # Медицинские данные
    allergies = Column(Text, nullable=True)  # Аллергии
    chronic_diseases = Column(Text, nullable=True)  # Хронические заболевания
    pregnancy = Column(Boolean, default=False, index=True)  # Беременность
    viral_diseases = Column(Text, nullable=True)  # Вирусные заболевания
    ontology = Column(JSON, nullable=True)  # Онтология/структурированные медицинские данные
    
    # Дополнительные медицинские поля
    blood_type = Column(Text, nullable=True)  # Группа крови (A+, B-, AB+, O-, etc.)
    medications = Column(JSON, nullable=True)  # Текущие лекарства [{"name": "...", "dose": "...", "frequency": "..."}]
    medical_history = Column(Text, nullable=True)  # История болезней и операций
    emergency_contact = Column(JSON, nullable=True)  # Контакт экстренной связи {"name": "...", "phone": "...", "relation": "..."}
    insurance_info = Column(JSON, nullable=True)  # Информация о страховке
    doctor_notes = Column(Text, nullable=True)  # Заметки врача
    
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)
    deleted_at = Column(DateTime, nullable=True, index=True)  # Soft delete

    # Relationships
    client = relationship("Client", back_populates="medical_profile", lazy="select")

