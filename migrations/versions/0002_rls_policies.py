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
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# Таблиці з прямим household_id.
TENANT_TABLES = (
    "memberships",
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
        $$;

        GRANT USAGE ON SCHEMA public TO kopiyka_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO kopiyka_app;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO kopiyka_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO kopiyka_app;

        -- Функція читає tenant-контекст поточної транзакції.
        -- STABLE, а не IMMUTABLE: значення змінюється між транзакціями.
        CREATE OR REPLACE FUNCTION current_household() RETURNS UUID AS $$
            SELECT NULLIF(current_setting('app.household_id', true), '')::uuid;
        $$ LANGUAGE sql STABLE;
        """
    )

    for table in TENANT_TABLES:
        op.execute(
            f"""
            ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE {table} FORCE  ROW LEVEL SECURITY;

            CREATE POLICY tenant_isolation ON {table}
                USING      (household_id = current_household())
                WITH CHECK (household_id = current_household());
            """
        )

    # households: ізоляція за власним id.
    op.execute(
        """
        ALTER TABLE households ENABLE ROW LEVEL SECURITY;
        ALTER TABLE households FORCE  ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON households
            USING (id = current_household());

        -- categories: видно глобальні (household_id IS NULL) + власні.
        ALTER TABLE categories ENABLE ROW LEVEL SECURITY;
        ALTER TABLE categories FORCE  ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON categories
            USING (household_id IS NULL OR household_id = current_household())
            WITH CHECK (household_id = current_household());
        """
    )


def downgrade() -> None:
    for table in (*TENANT_TABLES, "households", "categories"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table};")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")
    op.execute("DROP FUNCTION IF EXISTS current_household();")
