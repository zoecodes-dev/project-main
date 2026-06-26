"""0003 — files 테이블 신설 (공통 파일 업로드 §0.8)

첨부가 있는 화면(자료 제출·실사 보고서·시정 보고서·온보딩 등)이 공통으로 쓰는
`POST/GET/DELETE /files` 의 저장소. 응답 계약(fileId/fileName/sizeBytes/contentType/url)을
그대로 담기 위해 submission_documents 재사용 대신 전용 테이블을 둔다.

- 실제 바이트는 S3(버킷은 data_gateway 와 동일)에 저장, 여기엔 메타 + s3_key 만 보관.
- tenant_id 로 테넌트 격리(§0.2). 업로더는 users FK.

[멱등성] CREATE ... IF NOT EXISTS — 재적용 안전.

Revision ID: 0003_files_table
Revises: 0002_products_tenant_id
"""
from alembic import op  # noqa: F401

revision = "0003_files_table"
down_revision = "0002_products_tenant_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS files (
            file_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            tenant_id    UUID REFERENCES tenants(tenant_id),
            file_name    VARCHAR(255) NOT NULL,
            content_type VARCHAR(100),
            size_bytes   BIGINT,
            s3_key       VARCHAR(500) NOT NULL,
            context      VARCHAR(100),
            uploaded_by  UUID REFERENCES users(user_id),
            created_at   TIMESTAMPTZ DEFAULT now()
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_files_tenant ON files(tenant_id);")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_files_tenant;")
    op.execute("DROP TABLE IF EXISTS files;")
