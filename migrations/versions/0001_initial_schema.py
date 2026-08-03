"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-08-03

Написано явним DDL, а не autogenerate: перша міграція має бути читабельною
і містити ролі та розширення, які ORM не описує. Наступні міграції вже
роби через ``alembic revision --autogenerate``.
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")

    op.execute(
        """
        CREATE TABLE users (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            email         CITEXT NOT NULL UNIQUE,
            display_name  VARCHAR(200),
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            deleted_at    TIMESTAMPTZ
        );

        CREATE TABLE identities (
            id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            provider   VARCHAR(32)  NOT NULL,
            subject    VARCHAR(320) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_identity_provider_subject UNIQUE (provider, subject)
        );

        CREATE TABLE households (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name          VARCHAR(200) NOT NULL,
            base_currency CHAR(3) NOT NULL DEFAULT 'UAH',
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE memberships (
            household_id UUID NOT NULL REFERENCES households(id) ON DELETE CASCADE,
            user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role         VARCHAR(16) NOT NULL DEFAULT 'member',
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (household_id, user_id),
            CONSTRAINT ck_membership_role CHECK (role IN ('owner','member','viewer'))
        );
        CREATE INDEX ix_memberships_user ON memberships(user_id);

        CREATE TABLE accounts (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            household_id UUID NOT NULL REFERENCES households(id) ON DELETE CASCADE,
            bank         VARCHAR(16) NOT NULL,
            account_ref  VARCHAR(64) NOT NULL,
            name         VARCHAR(120) NOT NULL,
            currency     CHAR(3) NOT NULL DEFAULT 'UAH',
            archived     BOOLEAN NOT NULL DEFAULT false,
            CONSTRAINT ck_account_bank CHECK (bank IN ('mono','privat','manual')),
            CONSTRAINT uq_account_ref_per_household UNIQUE (household_id, account_ref)
        );

        CREATE TABLE categories (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            household_id UUID REFERENCES households(id) ON DELETE CASCADE,
            parent_id    UUID REFERENCES categories(id),
            slug         VARCHAR(64)  NOT NULL,
            name         VARCHAR(120) NOT NULL,
            kind         VARCHAR(16)  NOT NULL DEFAULT 'expense',
            icon         VARCHAR(32)
        );
        CREATE UNIQUE INDEX uq_category_global_slug
            ON categories(slug) WHERE household_id IS NULL;

        CREATE TABLE import_batches (
            id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            household_id   UUID NOT NULL REFERENCES households(id) ON DELETE CASCADE,
            account_id     UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
            uploaded_by    UUID REFERENCES users(id) ON DELETE SET NULL,
            source_type    VARCHAR(32) NOT NULL DEFAULT 'csv',
            object_key     TEXT,
            file_sha256    BYTEA,
            parser_version VARCHAR(32),
            encoding       VARCHAR(32),
            status         VARCHAR(16) NOT NULL DEFAULT 'pending',
            rows_total     INTEGER NOT NULL DEFAULT 0,
            rows_inserted  INTEGER NOT NULL DEFAULT 0,
            rows_duplicate INTEGER NOT NULL DEFAULT 0,
            error_detail   JSONB,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_import_batch_status CHECK (status IN ('pending','parsed','failed'))
        );

        CREATE TABLE category_rules (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            household_id UUID NOT NULL REFERENCES households(id) ON DELETE CASCADE,
            priority     INTEGER NOT NULL DEFAULT 100,
            match_mcc    SMALLINT,
            match_regex  TEXT,
            category_id  UUID NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
            created_by   UUID REFERENCES users(id) ON DELETE SET NULL,
            active       BOOLEAN NOT NULL DEFAULT true
        );

        CREATE TABLE transactions (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            household_id         UUID NOT NULL REFERENCES households(id) ON DELETE CASCADE,
            account_id           UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
            import_batch_id      UUID REFERENCES import_batches(id) ON DELETE SET NULL,
            booked_at            TIMESTAMPTZ NOT NULL,
            amount_minor         BIGINT NOT NULL,
            currency             CHAR(3) NOT NULL,
            amount_account_minor BIGINT NOT NULL,
            mcc                  SMALLINT,
            description_raw      TEXT NOT NULL,
            description_norm     TEXT NOT NULL,
            counterparty         TEXT,
            balance_after_minor  BIGINT,
            category_id          UUID REFERENCES categories(id),
            category_source      VARCHAR(8) NOT NULL DEFAULT 'none',
            is_internal_transfer BOOLEAN NOT NULL DEFAULT false,
            dedup_hash           BYTEA NOT NULL,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_tx_dedup UNIQUE (account_id, dedup_hash),
            CONSTRAINT ck_tx_category_source
                CHECK (category_source IN ('mcc','rule','manual','none'))
        );
        CREATE INDEX ix_tx_household_booked   ON transactions(household_id, booked_at DESC);
        CREATE INDEX ix_tx_household_category ON transactions(household_id, category_id);

        CREATE TABLE goals (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            household_id UUID NOT NULL REFERENCES households(id) ON DELETE CASCADE,
            name         VARCHAR(200) NOT NULL,
            target_minor BIGINT NOT NULL,
            currency     CHAR(3) NOT NULL DEFAULT 'UAH',
            target_date  DATE,
            saved_minor  BIGINT NOT NULL DEFAULT 0
        );

        CREATE TABLE audit_log (
            id            BIGSERIAL PRIMARY KEY,
            household_id  UUID,
            actor_user_id UUID,
            action        VARCHAR(64) NOT NULL,
            entity        VARCHAR(64),
            entity_id     UUID,
            ip            INET,
            user_agent    TEXT,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_audit_household_created ON audit_log(household_id, created_at DESC);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS audit_log, goals, transactions, category_rules,
            import_batches, categories, accounts, memberships, households,
            identities, users CASCADE;
        """
    )
