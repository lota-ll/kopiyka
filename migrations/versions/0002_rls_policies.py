"""row level security policies

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-03

Це не «додаткова безпека», а другий незалежний рівень ізоляції тенантів.
Перший рівень — фільтр у коді. Він ламається одним забутим ``WHERE`` в
одному ендпоінті; RLS цього не пробачає.

Дві пастки, через які RLS «не працює» у більшості туторіалів:

1. **Власник таблиці обходить політики за замовчуванням.** Тому нижче
   є ``FORCE ROW LEVEL SECURITY``, і застосунок підключається окремою
   роллю ``kopiyka_app``, яка не володіє таблицями.
2. **``BYPASSRLS``** — роль застосунку не повинна мати цього атрибута
   і не повинна бути суперкористувачем.

Як і в 0001, кожна команда виконується окремим ``op.execute()`` — асинхронний
драйвер ``asyncpg`` не приймає кілька команд в одному prepared statement.
Виняток — ``DO $$ ... $$`` та тіло функції: dollar-quoted блок PostgreSQL
розглядає як єдиний строковий літерал, тому внутрішні ``;`` не рахуються
за межі команди, і весь блок лишається ОДНІЄЮ інструкцією.
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# Таблиці з прямим household_id, для яких достатньо симетричної політики
# "USING/WITH CHECK household_id = current_household()". memberships сюди
# НЕ входить: вона єдина потребує іншого правила читання (див. нижче).
TENANT_TABLES = (
    "accounts",
    "import_batches",
    "transactions",
    "category_rules",
    "goals",
)


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kopiyka_app') THEN
                CREATE ROLE kopiyka_app LOGIN PASSWORD 'devpass';
            END IF;
        END
        $$
        """
    )
    op.execute("GRANT USAGE ON SCHEMA public TO kopiyka_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO kopiyka_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO kopiyka_app")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO kopiyka_app"
    )

    # Функція читає tenant-контекст поточної транзакції.
    # STABLE, а не IMMUTABLE: значення змінюється між транзакціями.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION current_household() RETURNS UUID AS $$
            SELECT NULLIF(current_setting('app.household_id', true), '')::uuid;
        $$ LANGUAGE sql STABLE
        """
    )

    # Розв'язує "курку і яйце" автентифікації: щоб визначити, до яких
    # households належить користувач, треба прочитати memberships ще ДО
    # того, як household обрано (і, відповідно, до того, як
    # current_household() поверне щось не-NULL). current_app_user()
    # виставляється auth-шаром одразу після встановлення особи —
    # раніше, ніж household.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION current_app_user() RETURNS UUID AS $$
            SELECT NULLIF(current_setting('app.user_id', true), '')::uuid;
        $$ LANGUAGE sql STABLE
        """
    )

    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
                USING      (household_id = current_household())
                WITH CHECK (household_id = current_household())
            """
        )

    # memberships: читання дозволене або в межах активного household, або
    # для власних рядків користувача (self-read за user_id) — без цього
    # неможливо прочитати "до яких households я належу", не знаючи
    # заздалегідь, який household активний. WITH CHECK свідомо ВУЖЧИЙ за
    # USING: вставка нового membership завжди йде в межах активного
    # household, ніколи — довільно за самим лише user_id.
    op.execute("ALTER TABLE memberships ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE memberships FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON memberships
            USING      (household_id = current_household()
                        OR user_id = current_app_user())
            WITH CHECK (household_id = current_household())
        """
    )

    # households: ізоляція за власним id.
    op.execute("ALTER TABLE households ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE households FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON households
            USING      (id = current_household())
            WITH CHECK (id = current_household())
        """
    )

    # categories: видно глобальні (household_id IS NULL) + власні.
    op.execute("ALTER TABLE categories ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE categories FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON categories
            USING      (household_id IS NULL OR household_id = current_household())
            WITH CHECK (household_id = current_household())
        """
    )


def downgrade() -> None:
    for table in (*TENANT_TABLES, "memberships", "households", "categories"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP FUNCTION IF EXISTS current_app_user()")
    op.execute("DROP FUNCTION IF EXISTS current_household()")
