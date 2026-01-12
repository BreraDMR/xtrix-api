from datetime import datetime, date
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Boolean, Text, Date
from sqlalchemy.orm import relationship

from app.db.base import Base


class LoyaltyProgram(Base):
    """
    Настройки программы лояльности для бизнеса.
    """
    __tablename__ = "loyalty_programs"

    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(Integer, ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    
    # Тип программы: 'points' (баллы) или 'cashback' (кэшбэк)
    program_type = Column(String, nullable=False, default='points', index=True)  # 'points', 'cashback', 'hybrid'
    
    # Настройки баллов
    points_per_currency = Column(Float, nullable=False, default=1.0)  # Баллов за единицу валюты
    points_name = Column(String, nullable=True, default='points')  # Название баллов (например, "баллы", "stars")
    currency_per_point = Column(Float, nullable=True)  # Стоимость балла в валюте (для списания)
    
    # Настройки кэшбэка
    cashback_percent = Column(Float, nullable=True, default=0.0)  # Процент кэшбэка
    
    # Уровни лояльности
    use_levels = Column(Boolean, default=False, nullable=False)
    
    # Срок действия бонусов (в днях, null = без срока)
    points_expiry_days = Column(Integer, nullable=True)
    
    # Бонусы ко дню рождения
    birthday_bonus_enabled = Column(Boolean, default=False, nullable=False)
    birthday_bonus_points = Column(Float, nullable=True)
    birthday_bonus_percent = Column(Float, nullable=True)  # Дополнительный процент кэшбэка
    
    # Активна ли программа
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    
    # Максимальный процент оплаты бонусами
    max_bonus_payment_percent = Column(Float, nullable=True, default=100.0)  # Максимум % суммы чека, которое можно оплатить бонусами
    
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)
    
    # Relationships
    business = relationship("Business", lazy="select")
    levels = relationship("LoyaltyLevel", back_populates="program", lazy="dynamic", cascade="all,delete", order_by="LoyaltyLevel.min_spending")
    promotions = relationship("LoyaltyPromotion", back_populates="program", lazy="dynamic", cascade="all,delete")


class LoyaltyLevel(Base):
    """
    Уровни лояльности в программе.
    """
    __tablename__ = "loyalty_levels"

    id = Column(Integer, primary_key=True, index=True)
    program_id = Column(Integer, ForeignKey("loyalty_programs.id", ondelete="CASCADE"), nullable=False, index=True)
    
    name = Column(String, nullable=False, index=True)  # Например: "Базовый", "VIP", "Gold"
    level_number = Column(Integer, nullable=False, index=True)  # Порядковый номер уровня (1, 2, 3...)
    min_spending = Column(Float, nullable=False, index=True)  # Минимальная сумма трат для уровня
    
    # Множители для уровня
    points_multiplier = Column(Float, nullable=True, default=1.0)  # Множитель для баллов
    cashback_multiplier = Column(Float, nullable=True, default=1.0)  # Множитель для кэшбэка
    
    # Дополнительные привилегии (JSON)
    benefits = Column(Text, nullable=True)  # JSON с описанием привилегий
    
    color = Column(String, nullable=True)  # Цвет уровня для UI
    
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    
    # Relationships
    program = relationship("LoyaltyProgram", back_populates="levels", lazy="select")


class LoyaltyPromotion(Base):
    """
    Персональные акции и промо-кампании.
    """
    __tablename__ = "loyalty_promotions"

    id = Column(Integer, primary_key=True, index=True)
    program_id = Column(Integer, ForeignKey("loyalty_programs.id", ondelete="CASCADE"), nullable=False, index=True)
    
    name = Column(String, nullable=False, index=True)
    description = Column(Text, nullable=True)
    
    # Тип акции: 'double_points', 'extra_cashback', 'bonus_points', 'discount'
    promotion_type = Column(String, nullable=False, index=True)
    
    # Параметры акции
    points_multiplier = Column(Float, nullable=True)  # Множитель баллов
    cashback_percent = Column(Float, nullable=True)  # Дополнительный кэшбэк
    bonus_points = Column(Float, nullable=True)  # Бонусные баллы
    discount_percent = Column(Float, nullable=True)  # Скидка
    
    # Условия применения
    service_ids = Column(Text, nullable=True)  # JSON список ID услуг (null = все услуги)
    min_amount = Column(Float, nullable=True)  # Минимальная сумма чека
    
    # Срок действия
    start_date = Column(Date, nullable=False, index=True)
    end_date = Column(Date, nullable=False, index=True)
    
    # Для кого акция (null = для всех, client_id = для конкретного клиента)
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="CASCADE"), nullable=True, index=True)
    
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    
    # Relationships
    program = relationship("LoyaltyProgram", back_populates="promotions", lazy="select")
    client = relationship("Client", lazy="select")


class LoyaltyTransaction(Base):
    """
    История транзакций по программе лояльности (начисление и списание баллов/кэшбэка).
    """
    __tablename__ = "loyalty_transactions"

    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True)
    business_id = Column(Integer, ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True)
    program_id = Column(Integer, ForeignKey("loyalty_programs.id", ondelete="SET NULL"), nullable=True, index=True)
    
    # Тип транзакции: 'earned' (начисление), 'spent' (списание), 'expired' (истекли), 'birthday_bonus', 'promotion'
    transaction_type = Column(String, nullable=False, index=True)
    
    # Сумма транзакции (положительная для начисления, отрицательная для списания)
    points_amount = Column(Float, nullable=False)
    cashback_amount = Column(Float, nullable=True, default=0.0)
    
    # Баланс после транзакции
    points_balance_after = Column(Float, nullable=False)
    cashback_balance_after = Column(Float, nullable=True, default=0.0)
    
    # Связь с визитом (если транзакция связана с визитом)
    visit_id = Column(Integer, ForeignKey("visits.id", ondelete="SET NULL"), nullable=True, index=True)
    
    # Связь с промо-акцией (если применялась)
    promotion_id = Column(Integer, ForeignKey("loyalty_promotions.id", ondelete="SET NULL"), nullable=True, index=True)
    
    # Связь с уровнем (если связано с изменением уровня)
    level_id = Column(Integer, ForeignKey("loyalty_levels.id", ondelete="SET NULL"), nullable=True, index=True)
    
    # Описание
    description = Column(Text, nullable=True)
    
    # Срок действия начисленных баллов (null = без срока)
    expiry_date = Column(Date, nullable=True, index=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    
    # Relationships
    client = relationship("Client", lazy="select")
    business = relationship("Business", lazy="select")
    program = relationship("LoyaltyProgram", lazy="select")
    visit = relationship("Visit", lazy="select")
    promotion = relationship("LoyaltyPromotion", lazy="select")
    level = relationship("LoyaltyLevel", lazy="select")
