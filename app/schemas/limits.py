"""Shared input limits, so every schema rejects the same nonsense the same way.

Upper bounds are deliberately generous - they're there to stop typos and junk
(a price of 10^12, a 20k-char name), not to model real business rules.
"""
from typing import Annotated, Literal

from pydantic import Field

MAX_MONEY = 100_000_000

Money = Annotated[float, Field(ge=0, le=MAX_MONEY)]
PositiveMoney = Annotated[float, Field(gt=0, le=MAX_MONEY)]
Minutes = Annotated[int, Field(ge=1, le=24 * 60)]
Percent = Annotated[float, Field(ge=0, le=100)]
Multiplier = Annotated[float, Field(ge=0, le=100)]
Points = Annotated[float, Field(ge=0, le=1_000_000)]

Name = Annotated[str, Field(min_length=1, max_length=255)]
ShortText = Annotated[str, Field(max_length=255)]
LongText = Annotated[str, Field(max_length=5000)]
Phone = Annotated[str, Field(max_length=50)]
Currency = Annotated[str, Field(pattern=r"^[A-Za-z]{3}$")]
HexColor = Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$")]

PaymentMethod = Literal["cash", "card", "transfer", "other"]
PaymentStatus = Literal["completed", "pending", "failed"]
BusinessType = Literal["salon", "clinic", "medical", "dental", "hospital", "individual"]
LoyaltyProgramType = Literal["points", "cashback", "hybrid"]
PromotionType = Literal["double_points", "extra_cashback", "bonus_points", "discount"]
ClientLanguage = Literal["en", "ru", "cz"]
