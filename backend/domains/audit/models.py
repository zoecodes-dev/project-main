import uuid

from sqlalchemy import Column, Integer, String, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB, TIMESTAMP
from sqlalchemy.sql import func

from backend.infrastructure.database import Base


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
