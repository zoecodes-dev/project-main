"""
domains/supplier/models.py (담당: 팀원 B)
PROJECT_CORE.md 및 schema.sql 100% 동기화 버전
"""
from datetime import datetime
from typing import Optional, Dict, Any
from geoalchemy2 import Geometry
from sqlalchemy import String, Integer, Boolean, DateTime, ForeignKey, Text, NUMERIC
from sqlalchemy.orm import Mapped, mapped_column, relationship

from infrastructure.database import Base
from infrastructure.trace import trace_node

class Supplier(Base):
    """영역 2. 협력사 마스터 테이블"""
    __tablename__ = "suppliers"

    supplier_id: Mapped[str] = mapped_column(String(36), primary_key=True) # UUID 대응
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
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
    supplier_type: Mapped[str] = mapped_column(String(30), nullable=False) # manufacturer, recycler, trader, miner
    tier: Mapped[Optional[int]] = mapped_column(Integer)
    parent_supplier_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("suppliers.supplier_id"))
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


class SupplierFactory(Base):
    """영역 2. 공장 마스터 (Geo Audit 대상)"""
    __tablename__ = "supplier_factories"

    factory_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    supplier_id: Mapped[str] = mapped_column(String(36), ForeignKey("suppliers.supplier_id"), nullable=False)
    factory_name: Mapped[Optional[str]] = mapped_column(String(255))
    factory_name_en: Mapped[Optional[str]] = mapped_column(String(255))
    address: Mapped[Optional[str]] = mapped_column(Text)
    country: Mapped[Optional[str]] = mapped_column(String(2)) # ISO 국가코드
    region: Mapped[Optional[str]] = mapped_column(String(100))
    
    # PostGIS 공간 좌표 (영수 에이전트 전용 위성 분석용)
    location = mapped_column(Geometry(geometry_type="POINT", srid=4326), nullable=True)
    
    factory_role: Mapped[Optional[str]] = mapped_column(String(30))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    supply_ratio_percent: Mapped[Optional[float]] = mapped_column(NUMERIC(5, 2))
    supply_quantity: Mapped[Optional[str]] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ============================================================
# 영역 3. Provider Type별 상세 (CTI 구조)
# ============================================================

class SupplierManufacturerDetail(Base):
    __tablename__ = "supplier_manufacturer_details"
    
    detail_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    supplier_id: Mapped[str] = mapped_column(String(36), ForeignKey("suppliers.supplier_id"), nullable=False)
    energy_source: Mapped[Optional[str]] = mapped_column(String(100))
    carbon_intensity: Mapped[Optional[float]] = mapped_column(NUMERIC(10, 4)) # Art.7 탄소발자국 핵심 필드
    supplier = relationship("Supplier", back_populates="manufacturer_detail")


class SupplierRecyclerDetail(Base):
    __tablename__ = "supplier_recycler_details"
    
    detail_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    supplier_id: Mapped[str] = mapped_column(String(36), ForeignKey("suppliers.supplier_id"), nullable=False)
    recycled_content_ratio: Mapped[Optional[float]] = mapped_column(NUMERIC(5, 2)) # DPP 재활용 함량 원본
    supplier = relationship("Supplier", back_populates="recycler_detail")


class SupplierTraderDetail(Base):
    __tablename__ = "supplier_trader_details"
    
    detail_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    supplier_id: Mapped[str] = mapped_column(String(36), ForeignKey("suppliers.supplier_id"), nullable=False)
    disclosure_completeness: Mapped[float] = mapped_column(NUMERIC(5, 2), default=0.0) # 75% 미만시 발행 차단
    supplier = relationship("Supplier", back_populates="trader_detail")


class SupplierMinerDetail(Base):
    __tablename__ = "supplier_miner_details"
    
    detail_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    supplier_id: Mapped[str] = mapped_column(String(36), ForeignKey("suppliers.supplier_id"), nullable=False)
    mine_name: Mapped[Optional[str]] = mapped_column(String(255))
    mine_coordinates = mapped_column(Geometry(geometry_type="POINT", srid=4326), nullable=True) # 우회 채굴 판정 좌표
    supplier = relationship("Supplier", back_populates="miner_detail")


# ============================================================
# 1주차 환경 검증용 깡통 함수
# ============================================================

@trace_node(node_name="create_supplier_onboarding", node_type="agent")
async def create_supplier_onboarding(state: Dict[str, Any], db: Any) -> Dict[str, Any]:
    print("[TRACE INFO] schema.sql 구조와 싱크가 완벽히 완료된 깡통 함수입니다.")
    return {
        **state,
        "supplier_status": "invited",
        "current_stage": "supplier_onboarding_initiated",
        "timestamp": datetime.utcnow().isoformat()
    }