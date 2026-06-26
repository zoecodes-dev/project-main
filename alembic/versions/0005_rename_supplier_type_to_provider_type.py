"""0005 — suppliers.supplier_type → provider_type 리네임

업종 용어를 provider_type 으로 통일(프론트 providerType · 규제 provider_type_applicable 와 일관).
컬럼 + CHECK 제약 + 인덱스 이름까지 리네임한다. 의존 뷰(v_supply_chain_node_status)는
Postgres 가 컬럼 rename 시 카탈로그를 자동 갱신하므로 재생성 불필요.

[멱등성] 각 rename 을 존재 여부로 가드 → 재적용/부분적용 안전.

Revision ID: 0005_rename_provider_type
Revises: 0004_demo_accounts
"""
from alembic import op  # noqa: F401

revision = "0005_rename_provider_type"
down_revision = "0004_demo_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM information_schema.columns
                     WHERE table_name='suppliers' AND column_name='supplier_type') THEN
            ALTER TABLE suppliers RENAME COLUMN supplier_type TO provider_type;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname='chk_supplier_type') THEN
            ALTER TABLE suppliers RENAME CONSTRAINT chk_supplier_type TO chk_provider_type;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_class WHERE relname='idx_suppliers_type' AND relkind='i') THEN
            ALTER INDEX idx_suppliers_type RENAME TO idx_suppliers_provider_type;
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM information_schema.columns
                     WHERE table_name='suppliers' AND column_name='provider_type') THEN
            ALTER TABLE suppliers RENAME COLUMN provider_type TO supplier_type;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname='chk_provider_type') THEN
            ALTER TABLE suppliers RENAME CONSTRAINT chk_provider_type TO chk_supplier_type;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_class WHERE relname='idx_suppliers_provider_type' AND relkind='i') THEN
            ALTER INDEX idx_suppliers_provider_type RENAME TO idx_suppliers_type;
          END IF;
        END $$;
        """
    )
