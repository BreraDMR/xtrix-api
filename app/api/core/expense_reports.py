"""Compare saved service quotes with explicit expenses, without FX or cash claims."""
from collections import defaultdict
from decimal import Decimal, Inexact, Rounded, localcontext
from math import isfinite

from sqlalchemy import Date, Integer, Numeric, String, and_, case, cast, func, literal, null, select, union_all

from app.db.models.expense import Expense
from app.db.models.expense_category import ExpenseCategory
from app.db.models.visit import Visit
from app.schemas.limits import ISO_4217


def _stored_money(value, unit):
    currency = case((unit.in_(sorted(ISO_4217)), unit), else_=None)
    # Direct float-to-Numeric rounds to 15 significant digits in PostgreSQL.
    # Its round-trip text retains sub-cent legacy tails for validation.
    number = case((and_(value.is_not(None), value >= 0, value < float('inf')),
                   cast(cast(value, String()), Numeric())), else_=None)
    cents = case((and_(currency.is_not(None), number == func.trunc(number, 2)), number * 100), else_=None)
    return currency, cents


def _money(cents):
    value = int(cents)
    sign = '-' if value < 0 else ''
    value = abs(value)
    return Decimal(f'{sign}{value // 100}.{value % 100:02d}')


def _float(value):
    if value is None:
        return None
    result = float(value)
    return result if isfinite(result) else None


def _summary(groups):
    """Combine only integer cents within the same proven unit."""
    units = defaultdict(dict)
    for row in groups:
        units[row['currency']][row['source']] = row
    slices = []
    for unit in sorted(units, key=lambda value: (value is None, value or '')):
        parts = units[unit]
        review = unit is None or any(row['valid_count'] != row['count'] for row in parts.values())
        cents = {}
        for source in ['quote', 'expense']:
            row = parts.get(source)
            cents[source] = (None if unit is None or (row and row['valid_count'] != row['count'])
                             else int(row['total_cents'] or 0) if row else 0)
        profit = (cents['quote'] - cents['expense'] if None not in cents.values() else None)
        margin = None
        if profit is not None:
            with localcontext() as context:
                context.prec = max(32, len(str(abs(profit))) + len(str(abs(cents['quote']))) + 16)
                context.traps[Inexact] = False
                context.traps[Rounded] = False
                margin = _float(Decimal(profit) / Decimal(cents['quote']) * 100) if cents['quote'] > 0 else 0.0
        slices.append({'currency': unit, 'revenue': _money(cents['quote']) if cents['quote'] is not None else None,
                       'expenses': _money(cents['expense']) if cents['expense'] is not None else None,
                       'profit': _money(profit) if profit is not None else None, 'profit_margin': margin,
                       'quote_count': int(parts.get('quote', {}).get('count', 0)),
                       'expense_count': int(parts.get('expense', {}).get('count', 0)), 'requires_review': review})
    single = slices[0] if len(slices) == 1 and not slices[0]['requires_review'] else None
    return {'currency': single['currency'] if single else None,
            **{key: _float(single[key]) if single else (0.0 if not slices else None)
               for key in ['revenue', 'expenses', 'profit', 'profit_margin']},
            'requires_review': bool(slices) and (single is None), 'by_currency': slices}


def get_profit_projection(db, business_id, date_from=None, date_to=None, include_quotes=True):
    """All dimensions share one statement snapshot, including under READ COMMITTED."""
    empty_id = cast(null(), Integer())
    empty_text = cast(null(), String())
    unit, cents = _stored_money(Expense.amount, Expense.currency)
    filters = [Expense.business_id == business_id]
    if date_from is not None:
        filters.append(Expense.expense_date >= date_from)
    if date_to is not None:
        filters.append(Expense.expense_date <= date_to)
    # A damaged foreign category link must not disclose another tenant's labels.
    visible_category = and_(ExpenseCategory.id == Expense.category_id,
        ((ExpenseCategory.business_id == business_id) |
         and_(ExpenseCategory.business_id.is_(None), ExpenseCategory.is_system.is_(True))))
    expenses = select(literal('expense').label('source'), unit.label('currency'),
        cast(Expense.expense_date, Date()).label('day'), ExpenseCategory.id.label('category_id'),
        ExpenseCategory.name.label('category_name'), ExpenseCategory.code.label('category_code'),
        ExpenseCategory.color.label('color'), cents.label('cents')).select_from(Expense).outerjoin(
            ExpenseCategory, visible_category).where(*filters)
    if include_quotes:
        unit, cents = _stored_money(Visit.price, Visit.currency_snapshot)
        filters = [Visit.business_id == business_id, Visit.status == 'completed', Visit.deleted_at.is_(None)]
        if date_from is not None:
            filters.append(Visit.date_start >= date_from)
        if date_to is not None:
            filters.append(Visit.date_start <= date_to)
        quotes = select(literal('quote'), unit, cast(Visit.date_start, Date()), empty_id,
                        empty_text, empty_text, empty_text, cents).where(*filters)
        base = union_all(expenses, quotes).cte('stored_profit_rows')
    else:
        base = expenses.cte('stored_profit_rows')

    def columns():
        return (func.count().label('count'), func.count(base.c.cents).label('valid_count'),
                func.sum(base.c.cents).label('total_cents'))

    def aggregate(kind, dimensions):
        day = base.c.day if 'day' in dimensions else cast(null(), Date())
        category = [base.c[name] if 'category' in dimensions else (empty_id if name == 'category_id' else empty_text)
                    for name in ['category_id', 'category_name', 'category_code', 'color']]
        query = select(literal(kind).label('kind'), base.c.source, base.c.currency,
                       day.label('day'), *[col.label(name) for col, name in zip(category,
                       ['category_id', 'category_name', 'category_code', 'color'])], *columns())
        grouping = [base.c.source, base.c.currency]
        if 'day' in dimensions:
            grouping.append(base.c.day)
        if 'category' in dimensions:
            grouping.extend(category)
            query = query.where(base.c.source == 'expense')
        return query.group_by(*grouping)

    statement = union_all(aggregate('total', []), aggregate('day', ['day']), aggregate('category', ['category']))
    with db.no_autoflush:
        rows = db.execute(statement).mappings().all()
    result = _summary([row for row in rows if row['kind'] == 'total'])
    days = defaultdict(list)
    categories = defaultdict(list)
    for row in rows:
        if row['kind'] == 'day':
            days[row['day']].append(row)
        elif row['kind'] == 'category':
            categories[(row['category_id'], row['category_name'], row['category_code'], row['color'])].append(row)
    result['daily'] = [{'date': day, **_summary(groups)} for day, groups in sorted(days.items())]
    result['expenses_by_category'] = []
    for (identifier, name, code, color), groups in sorted(categories.items(), key=lambda item: (item[0][0] is None, item[0][0] or 0)):
        summary = _summary(groups)
        result['expenses_by_category'].append({'category_id': identifier, 'category_name': name,
            'category_code': code, 'color': color, 'total': summary['expenses'], 'currency': summary['currency'],
            'requires_review': summary['requires_review'], 'by_currency': [
                {'currency': row['currency'], 'total': row['expenses'], 'count': row['expense_count'],
                 'requires_review': row['requires_review']} for row in summary['by_currency']]})
    result['value_basis'] = 'quoted_service_price'
    return result
