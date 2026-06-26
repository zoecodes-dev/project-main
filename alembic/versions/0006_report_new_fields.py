"""0006 — reports 신규 컬럼 추가 (type, submitted_at, severity, deadline, key_points)

P3 §3.2·3.3 audit/report 도메인 확장용.
- type: 보고서 종류 (compliance / sustainability / due_diligence 등)
- submitted_at: draft→approval_pending 전이 시점 기록
- severity: 결재함 표시용 심각도
- deadline: 결재 기한
- key_points: 결재함 표시용 핵심 포인트 JSONB 배열

IF [NOT] EXISTS 로 멱등성 보장.

Revision ID: 0006_report_new_fields
Revises: 0005_rename_supplier_type_to_provider_type
"""
from alembic import op

revision = "0006_report_new_fields"
down_revision = "0005_rename_supplier_type_to_provider_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE reports "
        "ADD COLUMN IF NOT EXISTS type VARCHAR(50) DEFAULT 'compliance';"
    )
    op.execute(
        "ALTER TABLE reports "
        "ADD COLUMN IF NOT EXISTS submitted_at TIMESTAMPTZ;"
    )
    op.execute(
        "ALTER TABLE reports "
        "ADD COLUMN IF NOT EXISTS severity VARCHAR(20) DEFAULT 'medium';"
    )
    op.execute(
        "ALTER TABLE reports "
        "ADD COLUMN IF NOT EXISTS deadline TIMESTAMPTZ;"
    )
    op.execute(
        "ALTER TABLE reports "
        "ADD COLUMN IF NOT EXISTS key_points JSONB DEFAULT '[]';"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE reports DROP COLUMN IF EXISTS key_points;")
    op.execute("ALTER TABLE reports DROP COLUMN IF EXISTS deadline;")
    op.execute("ALTER TABLE reports DROP COLUMN IF EXISTS severity;")
    op.execute("ALTER TABLE reports DROP COLUMN IF EXISTS submitted_at;")
    op.execute("ALTER TABLE reports DROP COLUMN IF EXISTS type;")
