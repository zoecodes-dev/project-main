import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Column, Integer, String, Text, Boolean, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB, TIMESTAMP

from backend.infrastructure.database import Base


# === ORM (DB 테이블 매핑) ===

class Tenant(Base):
    """schema.sql 영역 1의 tenants 테이블과 매핑됩니다."""
    __tablename__ = "tenants"
    __table_args__ = {'extend_existing': True}

    tenant_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_name = Column(String(255), nullable=False)


class User(Base):
    """schema.sql 영역 1의 users 테이블과 매핑됩니다."""
    __tablename__ = "users"
    __table_args__ = {'extend_existing': True}

    user_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=True)
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    name = Column(String(100), nullable=True)
    role = Column(String(50), nullable=True)
    is_active = Column(Boolean, default=True)


class AuditTrail(Base):
    __tablename__ = "audit_trail"

    audit_id       = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id       = Column(UUID(as_uuid=True), nullable=True)   # FK: batches.batch_id (도메인 간 직접 import 금지 — UUID만)
    step_number    = Column(Integer, nullable=True)
    timestamp      = Column(TIMESTAMP(timezone=True), nullable=True)
    node_type      = Column(String(20), nullable=True)            # agent / tool / human
    node_name      = Column(String(100), nullable=True)
    model_version  = Column(String(50), nullable=True)            # LLM 미사용 노드는 NULL
    prompt_version = Column(String(20), nullable=True)
    duration_ms    = Column(Integer, nullable=True)
    input_hash     = Column(String(64), nullable=True)
    output_hash    = Column(String(64), nullable=True)
    prev_hash      = Column(String(64), nullable=True)            # NULL = 첫 번째 step
    decision_text  = Column(Text, nullable=True)
    citations      = Column(JSONB, nullable=True)


# === API 응답 스키마 (Pydantic) ===

class AuditTrailRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    audit_id: uuid.UUID
    batch_id: uuid.UUID | None
    step_number: int | None
    timestamp: datetime | None
    node_type: str | None
    node_name: str | None
    model_version: str | None
    prompt_version: str | None
    duration_ms: int | None
    input_hash: str | None
    output_hash: str | None
    prev_hash: str | None
    decision_text: str | None
    citations: object | None


class ChainBreakOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    step_number: int | None
    expected_prev_hash: str | None
    actual_prev_hash: str | None
    reason: str


class ChainWarningOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    step_number: int | None
    reason: str


class ChainVerificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    batch_id: uuid.UUID
    total_steps: int
    chain_valid: bool
    breaks: list[ChainBreakOut]
    warnings: list[ChainWarningOut]