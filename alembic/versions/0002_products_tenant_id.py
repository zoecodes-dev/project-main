"""0002 — products.tenant_id 추가 (테넌트 격리 §0.2)

baseline에서 products/customers/bom 서브트리는 행 단위 테넌트 격리가 없었다
(tenant_id 보유 테이블은 tenants·users·suppliers·batches 뿐). 원청 멀티테넌트
격리(§0.2 — 목록/상세를 토큰 tenant_id로 필터)를 products까지 확장하기 위해
products에 tenant_id(FK→tenants)를 추가한다.

[백필 전략] 기존 행의 소유 테넌트 신호:
  ① 제조 협력사(manufacturer_id→suppliers.tenant_id)로 채움.
  ② 그래도 NULL이고 테넌트가 정확히 1개면(단일 원청 배포) 그 테넌트로 채움.
  ③ 남는 NULL은 그대로 둔다(suppliers/batches.tenant_id 도 nullable — 동일 정책).

[멱등성] ADD/CREATE 모두 IF [NOT] EXISTS — 재적용 안전(부팅 시 upgrade head 반복 대비).

Revision ID: 0002_products_tenant_id
Revises: 0001_baseline
"""
from alembic import op  # noqa: F401

revision = "0002_products_tenant_id"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) 컬럼 추가 (nullable — suppliers/batches.tenant_id 와 동일 정책)
    op.execute(
        "ALTER TABLE products "
        "ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(tenant_id);"
    )
    # 2) 백필 ① 제조 협력사의 테넌트로 채움
    op.execute(
        "UPDATE products p SET tenant_id = s.tenant_id "
        "FROM suppliers s "
        "WHERE p.manufacturer_id = s.supplier_id AND p.tenant_id IS NULL;"
    )
    # 3) 백필 ② 남은 NULL은 테넌트가 정확히 1개일 때만 그 테넌트로 (단일 원청 배포)
    op.execute(
        "UPDATE products SET tenant_id = (SELECT tenant_id FROM tenants LIMIT 1) "
        "WHERE tenant_id IS NULL AND (SELECT count(*) FROM tenants) = 1;"
    )
    # 4) 조회 인덱스 (batches(tenant_id, status) 패턴과 동일 목적)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_products_tenant ON products(tenant_id);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_products_tenant;")
    op.execute("ALTER TABLE products DROP COLUMN IF EXISTS tenant_id;")
