"""0004 — due_diligence: supplier_audit_records 컬럼 추가

due_diligence 도메인(§5) 스펙 대응:
  - audit_name  : 실사명 (5.3 POST 요청 name 필드)
  - factory_id  : 대상 공장 FK (5.1/5.2 factoryId)
  - score       : 실사 점수 (5.4 PATCH 갱신 대상)
  - report_file_id : 보고서 파일 FK → files 테이블 (5.4 multipart 업로드 후 연결)
  - audit_date  : NOT NULL 제약 완화 + DEFAULT CURRENT_DATE 추가
    (5.3 POST 요청에 날짜 없음 → 자동 set)

[멱등성] IF [NOT] EXISTS / IF EXISTS 사용 — 재적용 안전.

Revision ID: 0007_due_diligence_columns
Revises: 0006_report_new_fields
"""
from alembic import op

revision = "0007_due_diligence_columns"
down_revision = "0006_report_new_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE supplier_audit_records
        ADD COLUMN IF NOT EXISTS audit_name       VARCHAR(255),
        ADD COLUMN IF NOT EXISTS factory_id       UUID REFERENCES supplier_factories(factory_id),
        ADD COLUMN IF NOT EXISTS score            NUMERIC(5,2),
        ADD COLUMN IF NOT EXISTS report_file_id   UUID REFERENCES files(file_id);
        """
    )
    op.execute(
        "ALTER TABLE supplier_audit_records "
        "ALTER COLUMN audit_date SET DEFAULT CURRENT_DATE;"
    )
    op.execute(
        "ALTER TABLE supplier_audit_records "
        "ALTER COLUMN audit_date DROP NOT NULL;"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_records_factory "
        "ON supplier_audit_records(factory_id) WHERE factory_id IS NOT NULL;"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_audit_records_factory;")
    op.execute(
        """
        ALTER TABLE supplier_audit_records
        DROP COLUMN IF EXISTS audit_name,
        DROP COLUMN IF EXISTS factory_id,
        DROP COLUMN IF EXISTS score,
        DROP COLUMN IF EXISTS report_file_id;
        """
    )
    op.execute(
        "ALTER TABLE supplier_audit_records "
        "ALTER COLUMN audit_date SET NOT NULL;"
    )
    op.execute(
        "ALTER TABLE supplier_audit_records "
        "ALTER COLUMN audit_date DROP DEFAULT;"
    )
