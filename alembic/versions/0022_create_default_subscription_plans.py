"""create default subscription plans

Revision ID: 0022_default_plans
Revises: 0021_add_trial_fields
Create Date: 2025-01-05 14:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = "0022_default_plans"
down_revision = "0021_add_trial_fields"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    
    # Проверяем, существует ли таблица subscription_plans
    inspector = sa.inspect(conn)
    table_names = inspector.get_table_names()
    
    if 'subscription_plans' not in table_names:
        print("Warning: subscription_plans table does not exist. Skipping plan creation.")
        return
    
    # Проверяем, есть ли уже планы
    existing_count = conn.execute(text("SELECT COUNT(*) FROM subscription_plans")).scalar()
    if existing_count > 0:
        print(f"Found {existing_count} existing plans. Skipping default plan creation.")
        return
    
    # Создаем планы для Admin
    admin_plans = [
        {
            'code': 'admin_up_to_3_employees',
            'name': 'Admin: до 3 сотрудников',
            'period_days': 30,
            'price': 62.0,
            'currency': 'eur',
            'features': 'До 3 сотрудников, полный функционал',
            'is_active': True,
            'is_trial': False,
            'trial_days': None,
        },
        {
            'code': 'admin_up_to_7_employees',
            'name': 'Admin: до 7 сотрудников',
            'period_days': 30,
            'price': 96.0,
            'currency': 'eur',
            'features': 'До 7 сотрудников, полный функционал',
            'is_active': True,
            'is_trial': False,
            'trial_days': None,
        },
        {
            'code': 'admin_up_to_15_employees',
            'name': 'Admin: до 15 сотрудников',
            'period_days': 30,
            'price': 125.0,
            'currency': 'eur',
            'features': 'До 15 сотрудников, полный функционал',
            'is_active': True,
            'is_trial': False,
            'trial_days': None,
        },
        {
            'code': 'admin_unlimited_monthly',
            'name': 'Admin: Безлимит (помесячно)',
            'period_days': 30,
            'price': 146.0,
            'currency': 'eur',
            'features': 'Безлимит сотрудников, помесячная оплата, полный функционал',
            'is_active': True,
            'is_trial': False,
            'trial_days': None,
        },
        {
            'code': 'admin_unlimited_yearly',
            'name': 'Admin: Безлимит (годовая оплата)',
            'period_days': 365,
            'price': 1248.0,  # 104€ * 12 месяцев
            'currency': 'eur',
            'features': 'Безлимит сотрудников, годовая оплата с бонусными месяцами (104€/мес), полный функционал',
            'is_active': True,
            'is_trial': False,
            'trial_days': None,
        },
    ]
    
    # Создаем планы для Solo Specialist
    solo_plans = [
        {
            'code': 'solo_basic',
            'name': 'Solo Basic',
            'period_days': 30,
            'price': 14.0,  # Среднее между 12-16€
            'currency': 'eur',
            'features': 'Расписание, онлайн-запись, клиенты',
            'is_active': True,
            'is_trial': False,
            'trial_days': None,
        },
        {
            'code': 'solo_pro',
            'name': 'Solo Pro',
            'period_days': 30,
            'price': 22.5,  # Среднее между 20-25€
            'currency': 'eur',
            'features': 'Все функции Basic + напоминания, отчёты, маркетинг',
            'is_active': True,
            'is_trial': False,
            'trial_days': None,
        },
    ]
    
    all_plans = admin_plans + solo_plans
    
    for plan in all_plans:
        conn.execute(text("""
            INSERT INTO subscription_plans (code, name, period_days, price, currency, features, is_active, is_trial, trial_days)
            VALUES (:code, :name, :period_days, :price, :currency, :features, :is_active, :is_trial, :trial_days)
        """), plan)
    
    print(f"Created {len(all_plans)} default subscription plans.")


def downgrade():
    conn = op.get_bind()
    
    # Удаляем только дефолтные планы (по кодам)
    default_codes = [
        'admin_up_to_3_employees',
        'admin_up_to_7_employees',
        'admin_up_to_15_employees',
        'admin_unlimited_monthly',
        'admin_unlimited_yearly',
        'solo_basic',
        'solo_pro',
    ]
    
    for code in default_codes:
        conn.execute(text("DELETE FROM subscription_plans WHERE code = :code"), {'code': code})

