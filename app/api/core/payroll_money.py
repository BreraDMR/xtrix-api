"""Exact money boundary for payroll; legacy floats never establish a currency."""
from decimal import Decimal, InvalidOperation
from app.schemas.limits import ISO_4217


class PayrollError(ValueError):
    def __init__(self, reason, **context):
        self.reason = reason
        self.context = context
        super().__init__(reason)


def amount(value, signed=False):
    try:
        if isinstance(value, bool) or value is None:
            raise ValueError
        result = Decimal(str(value))
        if not result.is_finite() or result.copy_abs() > Decimal('9999999999999999.99'):
            raise ValueError
        if not signed and result < 0:
            raise ValueError
        if result.is_zero():
            return Decimal('0.00')
        sign, digits, exponent = result.as_tuple()
        digits = list(digits)
        while digits[-1] == 0:
            digits.pop()
            exponent += 1
        if exponent < -2:
            raise ValueError
        digits.extend([0] * (exponent + 2))
        return Decimal((sign, tuple(digits), -2))
    except (ValueError, InvalidOperation, TypeError):
        raise PayrollError('payroll_invalid_amount') from None


def rate(value):
    try:
        result = Decimal(str(value))
        if isinstance(value, bool) or not result.is_finite() or not 0 <= result <= 100:
            raise ValueError
        return result
    except (ValueError, InvalidOperation, TypeError):
        raise PayrollError('payroll_invalid_rate') from None


def currency(value):
    if not isinstance(value, str) or value not in ISO_4217:
        raise PayrollError('payroll_currency_review')
    return value
