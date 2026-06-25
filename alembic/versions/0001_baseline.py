"""baseline — 01_schema.sql 시점의 스키마 앵커 (no-op)

빈 볼륨 최초 기동 시 docker/01_schema.sql이 baseline 스키마 전체를 만든다. 이 리비전은
'그 baseline 시점'을 가리키는 앵커일 뿐이라 upgrade/downgrade가 비어 있다.

★ 규칙: docker/01_schema.sql은 이 시점 이후 **동결(freeze)**. 모든 스키마 변경은
   0002+ 마이그레이션으로만 추가한다. (그래야 fresh DB = baseline + 마이그레이션,
   기존 DB = 마이그레이션만 적용 → 두 경로가 항상 같은 최종 스키마로 수렴한다.)

Revision ID: 0001_baseline
Revises:
"""
from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # baseline 스키마는 01_schema.sql이 이미 생성 — 앵커만 기록(no-op).
    pass


def downgrade() -> None:
    # baseline 되돌리기는 지원하지 않음(전체 스키마 삭제에 해당).
    pass
