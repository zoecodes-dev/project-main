import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from backend.infrastructure.database import Base


class Report(Base):
    __tablename__ = "reports"

    report_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id = Column(
        UUID(as_uuid=True),
        ForeignKey("batches.batch_id", ondelete="CASCADE"),
        nullable=True,
    )
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)  # 프론트에서 summary 로 노출
    requester_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id"),
        nullable=False,
    )
    type = Column(String(50), default="compliance")
    # schema.sql chk_report_status: draft | approval_pending | fully_approved | returned
    status = Column(String(30), default="draft")
    current_step = Column(Integer, default=1)
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    severity = Column(String(20), default="medium")
    deadline = Column(DateTime(timezone=True), nullable=True)
    key_points = Column(JSONB, default=list)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ReportApprovalStep(Base):
    __tablename__ = "report_approval_steps"

    step_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_id = Column(
        UUID(as_uuid=True),
        ForeignKey("reports.report_id", ondelete="CASCADE"),
        nullable=False,
    )
    step_number = Column(Integer, nullable=False)
    approver_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id"),
        nullable=False,
    )
    # schema.sql chk_step_status: pending | approved | rejected
    status = Column(String(30), default="pending")
    decision_text = Column(Text, nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
