"""data_codes
Revision ID: 0040_data_codes
Revises: 0039_visit_no_overlap
Create Date: 2026-10-18 03:00:00
"""
from alembic import op
import sqlalchemy as sa
# revision identifiers, used by Alembic.
revision = "0040_data_codes"
down_revision = "0039_visit_no_overlap"
branch_labels = None
depends_on = None

# Russian text that used to be written into rows. Now rows keep a code and the frontend translates it;
# the English name stays as a fallback for anything that doesn't know the code (Stripe, exports).
SYSTEM_CATEGORIES = [
    # (old name, code, english name)
    ("Аренда", "rent", "Rent"),
    ("Зарплаты", "salaries", "Salaries"),
    ("Закупка товаров", "stock_purchase", "Stock purchase"),
    ("Маркетинг", "marketing", "Marketing"),
    ("Коммунальные услуги", "utilities", "Utilities"),
    ("Сервисы / Подписки", "subscriptions", "Services / Subscriptions"),
    ("Прочее", "other", "Other"),
]

PLANS = [
    # (code, old name, old features, new name, new features)
    ("admin_up_to_3_employees", "Admin: до 3 сотрудников", "До 3 сотрудников, полный функционал",
     "Admin: up to 3 employees", "Up to 3 employees, all features"),
    ("admin_up_to_7_employees", "Admin: до 7 сотрудников", "До 7 сотрудников, полный функционал",
     "Admin: up to 7 employees", "Up to 7 employees, all features"),
    ("admin_up_to_15_employees", "Admin: до 15 сотрудников", "До 15 сотрудников, полный функционал",
     "Admin: up to 15 employees", "Up to 15 employees, all features"),
    ("admin_unlimited_monthly", "Admin: Безлимит (помесячно)",
     "Безлимит сотрудников, помесячная оплата, полный функционал",
     "Admin: unlimited (monthly)", "Unlimited employees, monthly billing, all features"),
    ("admin_unlimited_yearly", "Admin: Безлимит (годовая оплата)",
     "Безлимит сотрудников, годовая оплата с бонусными месяцами (104€/мес), полный функционал",
     "Admin: unlimited (yearly)", "Unlimited employees, yearly billing with bonus months (104€/month), all features"),
    ("solo_basic", "Solo Basic", "Расписание, онлайн-запись, клиенты",
     "Solo Basic", "Schedule, online booking, clients"),
    ("solo_pro", "Solo Pro", "Все функции Basic + напоминания, отчёты, маркетинг",
     "Solo Pro", "Everything in Basic + reminders, reports, marketing"),
]

SALARY_TEXT = "Выплата зарплаты сотруднику (ID: "
SALARY_RE = r"^Выплата зарплаты сотруднику \(ID: (\d+)\)"
VISIT_TEXT = "Автоматическое списание при визите #"


def upgrade():
    conn = op.get_bind()

    # products: the default unit was "шт"
    op.alter_column("products", "unit", server_default="pcs")
    conn.execute(sa.text("UPDATE products SET unit = 'pcs' WHERE unit = 'шт'"))

    # system expense categories get a code
    op.add_column("expense_categories", sa.Column("code", sa.String(), nullable=True))
    for old, code, name in SYSTEM_CATEGORIES:
        conn.execute(sa.text(
            "UPDATE expense_categories SET code = :code, name = :name WHERE is_system AND name = :old"
        ), {"code": code, "name": name, "old": old})
    op.create_index("uq_expense_categories_code", "expense_categories", ["code"], unique=True,
                    postgresql_where=sa.text("code IS NOT NULL"))

    # expenses made by a salary payment: say so in columns, keep only the payment's own notes as text
    op.add_column("expenses", sa.Column("source", sa.String(), nullable=True))
    op.add_column("expenses", sa.Column("employee_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_expenses_employee_id", "expenses", "employees", ["employee_id"], ["id"],
                          ondelete="SET NULL")
    op.create_index("ix_expenses_source", "expenses", ["source"])
    conn.execute(sa.text(f"""
        UPDATE expenses e SET
            source = 'salary_payment',
            employee_id = (SELECT id FROM employees
                           WHERE id = ((regexp_match(e.description, '{SALARY_RE}'))[1])::int),
            description = NULLIF(regexp_replace(e.description, '{SALARY_RE}( - )?', ''), '')
        WHERE e.description ~ '{SALARY_RE}'
    """))

    # product write-offs made by a visit
    op.add_column("product_usages", sa.Column("source", sa.String(), nullable=True))
    conn.execute(sa.text(
        "UPDATE product_usages SET source = 'visit', notes = NULL "
        r"WHERE notes ~ '^Автоматическое списание при визите #\d+$'"
    ))

    for code, _old_name, _old_features, name, features in PLANS:
        conn.execute(sa.text("UPDATE subscription_plans SET name = :name, features = :features WHERE code = :code"),
                     {"code": code, "name": name, "features": features})


def downgrade():
    conn = op.get_bind()
    for code, old_name, old_features, _name, _features in PLANS:
        conn.execute(sa.text("UPDATE subscription_plans SET name = :name, features = :features WHERE code = :code"),
                     {"code": code, "name": old_name, "features": old_features})

    conn.execute(sa.text(
        "UPDATE product_usages SET notes = :text || visit_id WHERE source = 'visit' AND visit_id IS NOT NULL"
    ), {"text": VISIT_TEXT})
    op.drop_column("product_usages", "source")

    conn.execute(sa.text("""
        UPDATE expenses SET description = :text || COALESCE(employee_id::text, '?') || ')'
            || COALESCE(' - ' || description, '')
        WHERE source = 'salary_payment'
    """), {"text": SALARY_TEXT})
    op.drop_index("ix_expenses_source", table_name="expenses")
    op.drop_constraint("fk_expenses_employee_id", "expenses", type_="foreignkey")
    op.drop_column("expenses", "employee_id")
    op.drop_column("expenses", "source")

    for old, code, _name in SYSTEM_CATEGORIES:
        conn.execute(sa.text("UPDATE expense_categories SET name = :old WHERE code = :code"),
                     {"old": old, "code": code})
    op.drop_index("uq_expense_categories_code", table_name="expense_categories")
    op.drop_column("expense_categories", "code")

    conn.execute(sa.text("UPDATE products SET unit = 'шт' WHERE unit = 'pcs'"))
    op.alter_column("products", "unit", server_default="шт")
