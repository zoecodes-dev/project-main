from datetime import datetime
import uuid
from typing import Optional, Dict, Any
from geoalchemy2 import Geometry
from pydantic import BaseModel, Field
from sqlalchemy import String, Integer, Boolean, DateTime, ForeignKey, Text, NUMERIC
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB

from backend.infrastructure.database import Base
from backend.infrastructure.trace import trace_node

# ============================================================
# [1] SQLAlchemy ORM 모델 영역
# ============================================================

# ============================================================
# 협력사 마스터 및 CTI 상세
# ============================================================

class Supplier(Base):
    __tablename__ = "suppliers"

    supplier_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    company_name_en: Mapped[Optional[str]] = mapped_column(String(255))
    company_name_ko: Mapped[Optional[str]] = mapped_column(String(255))
    short_name_en: Mapped[Optional[str]] = mapped_column(String(100))
    short_name_ko: Mapped[Optional[str]] = mapped_column(String(100))
    ceo_name: Mapped[Optional[str]] = mapped_column(String(100))
    business_reg_no: Mapped[Optional[str]] = mapped_column(String(50))
    corporate_reg_no: Mapped[Optional[str]] = mapped_column(String(50))
    duns_number: Mapped[Optional[str]] = mapped_column(String(20))
    tax_number: Mapped[Optional[str]] = mapped_column(String(50))
    website: Mapped[Optional[str]] = mapped_column(String(255))
    supplier_type: Mapped[str] = mapped_column(String(30), nullable=False) 
    tier: Mapped[Optional[int]] = mapped_column(Integer)
    parent_supplier_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.supplier_id"))
    established_year: Mapped[Optional[int]] = mapped_column(Integer)
    employee_count: Mapped[Optional[int]] = mapped_column(Integer)
    completeness_score: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    risk_level: Mapped[str] = mapped_column(String(20), default="low")
    feoc_status: Mapped[str] = mapped_column(String(20), default="unknown")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    # CTI Relationships
    manufacturer_detail = relationship("SupplierManufacturerDetail", back_populates="supplier", uselist=False)
    recycler_detail = relationship("SupplierRecyclerDetail", back_populates="supplier", uselist=False)
    trader_detail = relationship("SupplierTraderDetail", back_populates="supplier", uselist=False)
    miner_detail = relationship("SupplierMinerDetail", back_populates="supplier", uselist=False)
    factories = relationship("SupplierFactory", back_populates="supplier")
    parent_supplier = relationship("Supplier", remote_side=[supplier_id], back_populates="child_suppliers")
    child_suppliers = relationship("Supplier", back_populates="parent_supplier")

class SupplierFactory(Base):
    __tablename__ = "supplier_factories"

    factory_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    supplier_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.supplier_id"), nullable=False)
    factory_name: Mapped[Optional[str]] = mapped_column(String(255))
    factory_name_en: Mapped[Optional[str]] = mapped_column(String(255))
    address: Mapped[Optional[str]] = mapped_column(Text)
    country: Mapped[Optional[str]] = mapped_column(String(2))
    region: Mapped[Optional[str]] = mapped_column(String(100))
    location = mapped_column(Geometry(geometry_type="POINT", srid=4326), nullable=True)
    factory_role: Mapped[Optional[str]] = mapped_column(String(30))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    supply_ratio_percent: Mapped[Optional[float]] = mapped_column(NUMERIC(5, 2))
    supply_quantity: Mapped[Optional[str]] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    supplier = relationship("Supplier", back_populates="factories")


# ============================================================
# CTI 상세 구조 (Detail Tables)
# ============================================================

class SupplierManufacturerDetail(Base):
    __tablename__ = "supplier_manufacturer_details"
    detail_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    supplier_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.supplier_id"), nullable=False)
    energy_source: Mapped[Optional[str]] = mapped_column(String(100))
    carbon_intensity: Mapped[Optional[float]] = mapped_column(NUMERIC(10, 4))
    supplier = relationship("Supplier", back_populates="manufacturer_detail")

class SupplierRecyclerDetail(Base):
    __tablename__ = "supplier_recycler_details"
    detail_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    supplier_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.supplier_id"), nullable=False)
    recycled_content_ratio: Mapped[Optional[float]] = mapped_column(NUMERIC(5, 2))
    supplier = relationship("Supplier", back_populates="recycler_detail")

class SupplierTraderDetail(Base):
    __tablename__ = "supplier_trader_details"
    detail_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    supplier_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.supplier_id"), nullable=False)
    disclosure_completeness: Mapped[float] = mapped_column(NUMERIC(5, 2), default=0.0)
    supplier = relationship("Supplier", back_populates="trader_detail")

class SupplierMinerDetail(Base):
    __tablename__ = "supplier_miner_details"
    detail_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    supplier_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.supplier_id"), nullable=False)
    mine_name: Mapped[Optional[str]] = mapped_column(String(255))
    mine_coordinates = mapped_column(Geometry(geometry_type="POINT", srid=4326), nullable=True)
    supplier = relationship("Supplier", back_populates="miner_detail")

# ============================================================
# 협력사 리스크 프로필
# ============================================================

class SupplierRiskProfile(Base):
    __tablename__ = "supplier_risk_profiles"

    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # schema.sql: UNIQUE(supplier_id) — supplier당 1개 row
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("suppliers.supplier_id"),
        nullable=False, unique=True,
    )

    # 0~100, 높을수록 위험
    overall_risk_score: Mapped[int] = mapped_column(Integer, default=0)
    # 0~29 critical / 30~49 high / 50~69 medium / 70~100 low
    risk_level: Mapped[str] = mapped_column(String(20), default="low")

    feoc_status: Mapped[str] = mapped_column(String(20), default="unknown")
    feoc_direct_ownership: Mapped[Optional[float]] = mapped_column(NUMERIC(5, 2))
    feoc_indirect_ownership: Mapped[Optional[float]] = mapped_column(NUMERIC(5, 2))
    feoc_last_assessed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    feoc_cert_expiry: Mapped[Optional[datetime]] = mapped_column(DateTime)

    is_high_risk_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    high_risk_reasons: Mapped[Optional[dict]] = mapped_column(JSONB)
    last_risk_review_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

# ============================================================
# [2] Pydantic 입출력 스키마(DTO) 영역
# ============================================================

class SupplierCreateRequest(BaseModel):
    tenant_id: uuid.UUID
    company_name: str
    supplier_type: str
    email: str


class SupplierBrief(BaseModel):
    """
    목록·단건 응답용 직렬화 스키마.
    ORM 객체를 그대로 반환하면 CTI relationship lazy load에서 직렬화 에러가
    날 수 있으므로, 명시적 스키마로 변환해 반환한다(직렬화 안전).
    from_attributes=True 로 ORM 인스턴스에서 바로 만든다.
    """
    supplier_id: uuid.UUID
    company_name: str
    supplier_type: str
    tier: Optional[int] = None
    status: str
    risk_level: str

    model_config = {"from_attributes": True}


class RiskProfileResponse(BaseModel):
    supplier_id: uuid.UUID
    overall_risk_score: int
    risk_level: str
    feoc_status: Optional[str] = "unknown"  # Optional 안전 가드 추가

    model_config = {"from_attributes": True}


class RiskScoreUpdateRequest(BaseModel):
    score: int


# ============================================================
# [3] 검증용 파이프라인 깡통 함수
# ============================================================

@trace_node(node_name="create_supplier_onboarding", node_type="agent")
async def create_supplier_onboarding(state: Dict[str, Any], db: Any) -> Dict[str, Any]:
    return {
        **state,
        "supplier_status": "invited",
        "current_stage": "supplier_onboarding_initiated",
        "timestamp": datetime.utcnow().isoformat()
    }