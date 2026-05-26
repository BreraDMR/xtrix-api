from datetime import datetime
from typing import Optional, List
from sqlalchemy.orm import Session
from app.db.models.transaction import Transaction
from app.schemas.transaction import TransactionCreate, TransactionUpdate
def create_transaction(db: Session, data: TransactionCreate) -> Transaction:
    if not data.transaction_date:
        data.transaction_date = datetime.utcnow()
    transaction = Transaction(**data.dict())
    db.add(transaction)
    db.commit()
    db.refresh(transaction)
    from app.api.crud import crud_subscription
    sub = crud_subscription.get_subscription(db, data.subscription_id)
    if sub:
        sub.last_transaction_id = transaction.id
        db.commit()
    return transaction
def update_transaction(db: Session, transaction: Transaction, data: TransactionUpdate) -> Transaction:
    for k, v in data.dict(exclude_unset=True).items():
        setattr(transaction, k, v)
    transaction.updated_at = datetime.utcnow()
    db.add(transaction)
    db.commit()
    db.refresh(transaction)
    return transaction
def get_transaction(db: Session, transaction_id: int) -> Optional[Transaction]:
    return db.query(Transaction).filter(Transaction.id == transaction_id).first()
def get_transactions_by_subscription(db: Session, subscription_id: int, limit: int = 100) -> List[Transaction]:
    return (
        db.query(Transaction)
        .filter(Transaction.subscription_id == subscription_id)
        .order_by(Transaction.transaction_date.desc())
        .limit(limit)
        .all()
    )
def get_transaction_by_external(db: Session, provider: str, external_id: str) -> Optional[Transaction]:
    return (
        db.query(Transaction)
        .filter(
            Transaction.provider == provider,
            Transaction.external_transaction_id == external_id,
        )
        .first()
    )
