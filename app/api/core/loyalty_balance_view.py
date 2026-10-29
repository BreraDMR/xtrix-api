"""Expose exact unit wallets and a labelled compatibility scalar, without FX."""
from dataclasses import asdict

from app.api.core import cashback_wallets


def balance_payload(db, client_id, business_id):
    wallets = cashback_wallets.balances(db, client_id, business_id)
    if not wallets:
        amount, currency = 0.0, None
    elif len(wallets) == 1 and wallets[0].currency is not None:
        amount, currency = float(wallets[0].amount), wallets[0].currency
    else:
        amount, currency = None, None
    return {'cashback': amount, 'cashback_currency': currency,
            'cashback_balances': [asdict(wallet) for wallet in wallets]}
