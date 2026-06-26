"""
domains/files/models.py  (담당: 팀원 B / 공통)

공통 파일 업로드(§0.8) ORM + 응답 스키마. 실제 바이트는 S3, 여기엔 메타 + s3_key.
응답 키는 snake_case로 내보내고 프론트 snakeToCamel 이 camelCase로 변환한다
(file_id→fileId, size_bytes→sizeBytes, content_type→contentType, download_url→downloadUrl).
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID

from backend.infrastructure.database import Base


class FileObject(Base):
    __tablename__ = "files"

    file_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        UUID(as_uuid=True), ForeignKey("tenants.tenant_id"), nullable=True
    )
    file_name = Column(String(255), nullable=False)
    content_type = Column(String(100), nullable=True)
    size_bytes = Column(BigInteger, nullable=True)
    s3_key = Column(String(500), nullable=False)
    context = Column(String(100), nullable=True)
    uploaded_by = Column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=True
    )
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
