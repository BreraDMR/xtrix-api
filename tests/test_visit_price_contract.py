"""Pure explicit-price contracts; unittest runs without database fixtures."""
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal, localcontext
import os
import subprocess
import sys
import unittest

EXPECTED = 'vprice1_' + 'a' * 64


class PriceContractTests(unittest.TestCase):
    def body(self, **values):
        from app.schemas.visit_price import VisitPriceChange
        return VisitPriceChange(currency='EUR', expected_quote=EXPECTED, **values)

    def state(self, **values):
        from app.api.core.visit_price_quote import StoredVisitPrice, StoredVisitPriceItem
        data = dict(visit_id=11, currency_snapshot='CZK', total=40.0, service_id=1, specialist_id=2,
                    items=(StoredVisitPriceItem(21, 0, 1, 2, Decimal('15.00')),
                           StoredVisitPriceItem(22, 1, None, 3, Decimal('25.00'))))
        data.update(values)
        return StoredVisitPrice(**data)

    def test_valid_explicit_body_uses_currency_validator(self):
        from app.schemas.visit_price import VisitPriceChange
        body = VisitPriceChange(currency='eur', expected_quote=EXPECTED, legacy_price='40.00')
        self.assertEqual(body.currency, 'EUR')
        self.assertEqual(body.total, Decimal('40.00'))

    def test_legacy_null_projection_is_safe(self):
        from app.api.core.visit_price_quote import StoredVisitPrice, project_price
        state = StoredVisitPrice(11, None, None, None, 2)
        quote = project_price(state)
        self.assertIsNone(quote.currency)
        self.assertIsNone(quote.total)
        self.assertEqual(quote.items, ())
        self.assertRegex(quote.expected_quote, r'^vprice1_[0-9a-f]{64}$')

    def test_invalid_amounts_are_not_rounded_or_coerced_from_bool(self):
        for value in [True, False, -1, '-0.01', '1.001', '1.000', 'NaN', 'Infinity', '-Infinity',
                      float('nan'), float('inf'), Decimal('sNaN'), '100000000', '99999999.999', {}, []]:
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                self.body(legacy_price=value)
        self.assertEqual(self.body(legacy_price='99999999.99').total, Decimal('99999999.99'))
        for value in [0, '0.00', Decimal('0.01'), 12.34]:
            self.assertEqual(self.body(legacy_price=value).total, Decimal(str(value)))

    def test_explicit_currency_quote_and_shape_are_required(self):
        from app.schemas.visit_price import VisitPriceChange
        invalid = [dict(legacy_price=1, expected_quote=EXPECTED), dict(currency='EUR', legacy_price=1),
                   dict(currency='EUR', expected_quote=EXPECTED),
                   dict(currency='ZZZ', legacy_price=1, expected_quote=EXPECTED),
                   dict(currency='EUR', legacy_price=1, expected_quote='not-a-quote'),
                   dict(currency='EUR', legacy_price=1, expected_quote=EXPECTED, total=1)]
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValueError):
                VisitPriceChange(**values)
        with self.assertRaises(ValueError):
            self.body(items=[dict(item_id=1, price=1)], legacy_price=1)
        with self.assertRaises(ValueError):
            self.body(items=[])

    def test_ids_unique_strict_positive_and_item_extra_forbidden(self):
        for items in [[dict(item_id=1, price=1), dict(item_id=1, price=2)],
                      [dict(item_id=True, price=1)], [dict(item_id='1', price=1)],
                      [dict(item_id=0, price=1)], [dict(item_id=1, price=1, service_id=2)]]:
            with self.subTest(items=items), self.assertRaises(ValueError):
                self.body(items=items)

    def test_item_total_exact_and_bounded_even_in_low_decimal_context(self):
        with localcontext() as context:
            context.prec = 3
            body = self.body(items=[dict(item_id=21, price='99999.99'), dict(item_id=22, price='0.02')])
            self.assertEqual(body.total, Decimal('100000.01'))
            with self.assertRaises(ValueError):
                self.body(items=[dict(item_id=21, price='99999999.99'), dict(item_id=22, price='0.01')])

    def test_item_membership_exact_once_and_legacy_mutual_exclusion(self):
        from app.api.core.visit_price_quote import validate_change_items
        state = self.state()
        change = self.body(items=[dict(item_id=22, price=25), dict(item_id=21, price=15)])
        self.assertEqual(validate_change_items(state, change), Decimal('40.00'))
        for change in [self.body(legacy_price=40), self.body(items=[dict(item_id=21, price=40)]),
                       self.body(items=[dict(item_id=21, price=15), dict(item_id=99, price=25)])]:
            with self.assertRaises(ValueError):
                validate_change_items(state, change)
        legacy = self.state(items=())
        self.assertEqual(validate_change_items(legacy, self.body(legacy_price=40)), Decimal('40.00'))
        with self.assertRaises(ValueError):
            validate_change_items(legacy, self.body(items=[dict(item_id=21, price=40)]))

    def test_fingerprint_changes_for_every_money_or_identity_component(self):
        from app.api.core.visit_price_quote import fingerprint
        state = self.state()
        original = fingerprint(state)
        changed = [replace(state, visit_id=12), replace(state, total=41), replace(state, currency_snapshot='EUR'),
                   replace(state, service_id=5), replace(state, specialist_id=5)]
        for field, value in [('id', 99), ('position', 3), ('price', Decimal('15.01')), ('service_id', None), ('specialist_id', 9)]:
            changed.append(replace(state, items=(replace(state.items[0], **{field: value}), state.items[1])))
        for candidate in changed:
            with self.subTest(state=candidate):
                self.assertNotEqual(fingerprint(candidate), original)

    def test_fingerprint_canonical_order_and_numeric_equality_are_context_independent(self):
        from app.api.core.visit_price_quote import fingerprint
        state = self.state()
        equivalent = replace(state, total=Decimal('40.000'), items=tuple(reversed(state.items)))
        with localcontext() as context:
            context.prec = 2
            self.assertEqual(fingerprint(state), fingerprint(equivalent))
        self.assertNotEqual(fingerprint(state), fingerprint(replace(state, items=tuple(
            replace(item, position=1 - item.position) for item in state.items))))

    def test_unknown_unit_never_becomes_current_business_currency(self):
        from app.api.core.visit_price_quote import fingerprint, project_price
        null = self.state(currency_snapshot=None)
        unknown = replace(null, currency_snapshot='ZZZ')
        self.assertIsNone(project_price(null).currency)
        self.assertIsNone(project_price(unknown).currency)
        self.assertNotEqual(fingerprint(null), fingerprint(unknown))
        self.assertEqual(project_price(self.state()).currency, 'CZK')
        self.assertFalse(hasattr(project_price(null), 'business_currency'))

    def test_snapshot_projection_immutable_scalar_allowlist_and_no_lazy_orm(self):
        from app.api.core.visit_price_quote import fingerprint, project_price
        state = self.state()
        quote = project_price(state)
        with self.assertRaises(FrozenInstanceError):
            quote.total = Decimal(1)
        with self.assertRaises(FrozenInstanceError):
            quote.items[0].price = Decimal(1)
        with self.assertRaises(FrozenInstanceError):
            state.currency_snapshot = 'EUR'
        for forbidden in ['client_id', 'account_id', 'note', 'medical', 'payment_attempts', '_sa_instance_state']:
            self.assertFalse(hasattr(quote, forbidden))
        class Lazy:
            def __getattr__(self, name):
                raise AssertionError('Lazy attribute read: ' + name)
        for function in [fingerprint, project_price]:
            with self.assertRaises(ValueError):
                function(Lazy())

    def test_nonfinite_or_null_stored_amounts_project_unknown_without_hash_failure(self):
        from app.api.core.visit_price_quote import fingerprint, project_price
        for value in [None, float('nan'), float('inf'), float('-inf'), Decimal('NaN')]:
            state = self.state(total=value, items=())
            with self.subTest(value=repr(value)):
                self.assertIsNone(project_price(state).total)
                self.assertEqual(fingerprint(state), fingerprint(state))
        self.assertNotEqual(fingerprint(self.state(total=None)), fingerprint(self.state(total=float('nan'))))

    def test_fingerprint_is_stable_across_python_hash_seeds(self):
        source = "from app.api.core.visit_price_quote import StoredVisitPrice,fingerprint; print(fingerprint(StoredVisitPrice(11,None,None,None,2)))"
        outputs = []
        for seed in ['1', '991']:
            env = {**os.environ, 'PYTHONHASHSEED': seed}
            outputs.append(subprocess.check_output([sys.executable, '-c', source], env=env, text=True).strip())
        self.assertEqual(outputs[0], outputs[1])

    def test_invalid_snapshot_inputs_cannot_smuggle_mutability_or_orm(self):
        from app.api.core.visit_price_quote import StoredVisitPriceItem
        for values in [dict(visit_id=True), dict(total=True), dict(items=[]), dict(currency_snapshot={}),
                       dict(items=(object(),)), dict(items=(self.state().items[0], self.state().items[0]))]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.state(**values)
        with self.assertRaises(ValueError):
            StoredVisitPriceItem(1, 0, None, True, Decimal(1))

    def test_historical_negative_and_unknown_raw_unit_are_not_repaired(self):
        from app.api.core.visit_price_quote import fingerprint, project_price
        state = self.state(total=Decimal('-7.123'), currency_snapshot='eur', items=())
        quote = project_price(state)
        self.assertEqual(quote.total, Decimal('-7.123'))
        self.assertIsNone(quote.currency)
        self.assertNotEqual(fingerprint(state), fingerprint(replace(state, currency_snapshot='EUR')))
        self.assertNotEqual(fingerprint(state), fingerprint(replace(state, total=None)))

    def test_actual_json_body_validates_prices_without_rounding(self):
        import json
        from app.schemas.visit_price import VisitPriceChange
        payload = dict(currency='EUR', expected_quote=EXPECTED, items=[dict(item_id=21, price=15), dict(item_id=22, price=25)])
        body = VisitPriceChange.model_validate_json(json.dumps(payload))
        self.assertEqual(body.total, Decimal('40.00'))
        for bad in [True, 0.001, 100000000]:
            payload['items'][0]['price'] = bad
            with self.subTest(value=bad), self.assertRaises(ValueError):
                VisitPriceChange.model_validate_json(json.dumps(payload))


if __name__ == '__main__':
    unittest.main()
