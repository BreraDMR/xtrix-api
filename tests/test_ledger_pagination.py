from datetime import datetime, timedelta

from app.api.crud import crud_expense, crud_product
from app.db.models.expense import Expense
from app.db.models.expense_category import ExpenseCategory
from app.db.models.product import Product
from tests.test_public_lists import _owner_with_business


def _pages(client, path, headers, params, expected_ids):
    received = []
    pages = (len(expected_ids) + 99) // 100
    for page in range(1, pages + 2):
        response = client.get(path, headers=headers, params={**params, 'per_page': 100, 'page': page})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data['total'] == len(expected_ids), (params, data['total'])
        assert data['pages'] == pages
        received.extend(item['id'] for item in data['items'])
    assert received == expected_ids
    assert len(received) == len(set(received))


def test_products_filter_count_and_tied_pages(client, make_user, headers_for, db):
    _, headers, business = _owner_with_business(client, make_user, headers_for, 'Alpha')
    _, _, other = _owner_with_business(client, make_user, headers_for, 'Beta')
    common = dict(business_id=business, name='Needle', category='supplies', quantity=1, min_quantity=5)
    # Reverse insertion order so equal names cannot accidentally stand in for an ID sort.
    matching = [Product(id=1000 + i, **common) for i in reversed(range(125))]
    noise = [
        Product(id=2000, **{**common, 'category': 'other'}),
        Product(id=2001, **{**common, 'is_active': False}),
        Product(id=2002, **{**common, 'quantity': 10}),
        Product(id=2003, **{**common, 'name': 'Other'}),
    ]
    db.add_all(matching + noise + [Product(**{**common, 'business_id': other})])
    db.commit()
    ids = list(range(1000, 1125))
    cases = [
        ({}, ids + [2000, 2002, 2003]),
        ({'category': 'supplies'}, ids + [2002, 2003]),
        ({'search': 'needle'}, ids + [2000, 2002]),
        ({'low_stock_only': True}, ids + [2000, 2003]),
        ({'is_active': False}, [2001]),
        ({'category': 'supplies', 'search': 'needle', 'low_stock_only': True}, ids),
        ({'search': 'missing'}, []),
    ]
    for params, expected in cases:
        _pages(client, '/products/', headers, params, expected)
    # Existing callers still get a list with the same offset/limit arguments.
    assert [row.id for row in crud_product.list_products(db, business, skip=100, limit=25)] == ids[100:]


def test_expenses_filter_count_and_tied_pages(client, make_user, headers_for, db):
    _, headers, business = _owner_with_business(client, make_user, headers_for, 'Alpha')
    _, _, other = _owner_with_business(client, make_user, headers_for, 'Beta')
    category = ExpenseCategory(name='Selected', business_id=business)
    other_category = ExpenseCategory(name='Other', business_id=business)
    db.add_all([category, other_category])
    db.flush()
    day = datetime(2026, 1, 15, 12)
    common = dict(business_id=business, expense_date=day, category_id=category.id, amount=50, payment_method='cash')
    matching = [Expense(id=1000 + i, **common) for i in range(125)]
    noise = [
        Expense(id=2000, **{**common, 'category_id': other_category.id}),
        Expense(id=2001, **{**common, 'expense_date': day - timedelta(days=1)}),
        Expense(id=2002, **{**common, 'expense_date': day + timedelta(days=1)}),
        Expense(id=2003, **{**common, 'payment_method': 'card'}),
        Expense(id=2004, **{**common, 'amount': 5}),
        Expense(id=2005, **{**common, 'amount': 500}),
    ]
    db.add_all(matching + noise + [Expense(**{**common, 'business_id': other, 'category_id': None})])
    db.commit()
    tied = [2005, 2004, 2003, 2000] + list(reversed(range(1000, 1125)))
    since = day.isoformat() + 'Z'
    cases = [
        ({}, [2002] + tied + [2001]),
        ({'category_id': category.id}, [2002] + [i for i in tied if i != 2000] + [2001]),
        ({'date_from': since}, [2002] + tied),
        ({'date_to': since}, tied + [2001]),
        ({'payment_method': 'cash'}, [2002] + [i for i in tied if i != 2003] + [2001]),
        ({'min_amount': 10}, [2002] + [i for i in tied if i != 2004] + [2001]),
        ({'max_amount': 100}, [2002] + [i for i in tied if i != 2005] + [2001]),
        ({'category_id': category.id, 'date_from': since, 'date_to': since,
          'payment_method': 'cash', 'min_amount': 10, 'max_amount': 100}, list(reversed(range(1000, 1125)))),
        ({'min_amount': 999}, []),
    ]
    for params, expected in cases:
        _pages(client, '/expenses/', headers, params, expected)
    assert [row.id for row in crud_expense.list_expenses(db, business, category_id=category.id,
            date_from=day, date_to=day, payment_method='cash', min_amount=10, max_amount=100,
            skip=100, limit=25)] == list(reversed(range(1000, 1025)))
