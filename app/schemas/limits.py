"""Shared input limits, so every schema rejects the same nonsense the same way.

Upper bounds are deliberately generous - they're there to stop typos and junk
(a price of 10^12, a 20k-char name), not to model real business rules.
"""
from typing import Annotated, Literal

from pydantic import AfterValidator, Field

from app.schemas.errors import invalid

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
# active ISO 4217 codes (funds, metals and test codes left out)
ISO_4217 = frozenset("""
AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BRL BSD BTN BWP BYN BZD
CAD CDF CHF CLP CNY COP CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD
GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT
LAK LBP LKR LRD LSL LYD MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MYR MZN NAD NGN NIO NOK NPR
NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SOS SRD SSP
STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD TZS UAH UGX USD UYU UZS VES VND VUV WST XAF XCD XCG
XOF XPF YER ZAR ZMW ZWG
""".split())


def _iso_currency(value: str) -> str:
    code = value.upper()
    if code not in ISO_4217:
        raise invalid("invalid_currency", "Unknown currency, use an ISO 4217 code like EUR or CZK")
    return code


Currency = Annotated[str, Field(pattern=r"^[A-Za-z]{3}$"), AfterValidator(_iso_currency)]
HexColor = Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$")]

PaymentMethod = Literal["cash", "card", "transfer", "other"]
PaymentStatus = Literal["completed", "pending", "failed"]
BusinessType = Literal["salon", "clinic", "medical", "dental", "hospital", "individual"]
LoyaltyProgramType = Literal["points", "cashback", "hybrid"]
PromotionType = Literal["double_points", "extra_cashback", "bonus_points", "discount"]
ClientLanguage = Literal["en", "ru", "cz"]
