from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.api.core.clock import utcnow
class Business(Base):
    __tablename__ = "businesses"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, index=True)
    business_type = Column(
        String, 
        nullable=False, 
        index=True,
        comment="Business type: salon, clinic, medical, dental, hospital, individual"
    )
    address = Column(Text, nullable=True)
    phone = Column(String, nullable=True, index=True)
    email = Column(String, nullable=True, index=True)
    description = Column(Text, nullable=True)
    currency = Column(String, nullable=False, default="EUR", index=True)  
    # what the business mainly does (SpecialistType value); empty = not picked yet
    specialist_type = Column(String(64), nullable=True)
    # the assistant's name in this business; empty = the default for the interface language
    assistant_name = Column(String(30), nullable=True)
    assistant_gender = Column(String(10), nullable=False, default="female", server_default="female")
    # scheduling policy; visits stay in UTC and are shown in this zone
    timezone = Column(String(64), nullable=False, default="UTC", server_default="UTC")
    booking_horizon_days = Column(Integer, nullable=False, default=30, server_default="30")
    booking_lead_time_minutes = Column(Integer, nullable=False, default=0, server_default="0")
    # a cancel closer than this to the start counts as a late cancel; 0 = don't track them
    late_cancel_window_hours = Column(Integer, nullable=False, default=24, server_default="24")
    # "N no-shows within M months" -> tag the client and maybe stop their online booking. Off by default
    noshow_rule_enabled = Column(Boolean, nullable=False, default=False, server_default="false")
    noshow_rule_threshold = Column(Integer, nullable=False, default=3, server_default="3")
    noshow_rule_period_months = Column(Integer, nullable=False, default=6, server_default="6")
    noshow_rule_tag = Column(String(40), nullable=False, default="no-show", server_default="no-show")
    noshow_rule_block_online = Column(Boolean, nullable=False, default=False, server_default="false")
    created_at = Column(DateTime, default=utcnow, index=True)
    deleted_at = Column(DateTime, nullable=True, index=True)  
    users = relationship("User", back_populates="business", lazy="dynamic")
    specialist_profiles = relationship("SpecialistProfile", back_populates="business", lazy="dynamic")
    client_relations = relationship("ClientBusinessRelation", back_populates="business", lazy="dynamic", cascade="all,delete")
    visits = relationship("Visit", back_populates="business", lazy="dynamic")
    services = relationship("Service", back_populates="business", lazy="dynamic")
    public_booking_settings = relationship(
        "PublicBookingSettings",
        back_populates="business",
        uselist=False,
        cascade="all, delete-orphan",
    )
