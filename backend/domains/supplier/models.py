"""
domains/supplier/models.py  (담당: 팀원 B)

Supplier 도메인 SQLAlchemy ORM + Pydantic DTO.
컬럼명·타입·기본값은 schema.sql 영역 2~6과 1:1 일치(SSOT). ORM이 schema와
다르면 마이그레이션이 아니라 ORM을 고치는 게 정답(0-2절 규칙 5).

[이번 정합 수정 요지]
- 상태값 기본값을 접두어 표기로: status='supplier_pending'(구 'pending'),
  consent_status='consent_pending', training_records.status='not_started' 등.
- 타입을 schema와 일치: suppliers.status는 VARCHAR(30)(구 String(20)).
- 시각은 timezone-aware UTC: server_default=func.now() 사용(구 datetime.utcnow 제거).
- SupplierFactory 누락 컬럼 전부 보강: destination, applicable_regulations,
  hidden_regulations, operating_period_from/to, monthly_capacity,
  destination_detail, supply_ratio_percent, supply_quantity.
- CTI 4종 누락 컬럼 보강(manufacturing_process/capacity, recycled_materials 등).
- 부속 테이블 ORM: supplier_contacts, supplier_onboarding,
  supplier_audit_records.
"""
import uuid
from datetime import date, datetime
from typing import Optional

from geoalchemy2 import Geometry
from pydantic import BaseModel

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Integer, NUMERIC, String, Text, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.infrastructure.database import Base


# ============================================================
# 영역 2. 협력사 마스터
# ============================================================
class Supplier(Base):
    __tablename__ = "suppliers"

    supplier_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.tenant_id"))
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
    provider_type: Mapped[str] = mapped_column(String(30), nullable=False)
    smelter_type: Mapped[Optional[str]] = mapped_column(String(20))  # smelter 세부 구분(rmi/private)
    core_minerals: Mapped[Optional[dict]] = mapped_column(JSONB)  # 소재 구성: 핵심광물 함량(%) {"Li":..,"Co":..,"Ni":..}
    country: Mapped[Optional[str]] = mapped_column(String(2))  # 소재 국가(ISO 3166-1 alpha-2)
    address: Mapped[Optional[str]] = mapped_column(Text)  # 회사 주소(전체 문자열). 공장 주소와 별개 — 회사 소재지
    business_reg_doc_url: Mapped[Optional[str]] = mapped_column(String(500))  # 사업자등록증 업로드 URL
    environmental_report_url: Mapped[Optional[str]] = mapped_column(String(500))  # 환경성적서(회원가입 수집) 업로드 URL
    self_assessment_doc_url: Mapped[Optional[str]] = mapped_column(String(500))  # 실사 자가진단 보고서 업로드 URL
    parent_supplier_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("suppliers.supplier_id")
    )
    established_year: Mapped[Optional[int]] = mapped_column(Integer)
    employee_count: Mapped[Optional[int]] = mapped_column(Integer)
    completeness_score: Mapped[int] = mapped_column(Integer, default=0)

    # 상태값은 schema.sql 접두어 표기 그대로. 타입은 VARCHAR(30).
    status: Mapped[str] = mapped_column(String(30), default="supplier_pending")
    risk_level: Mapped[str] = mapped_column(String(20), default="low")
    feoc_status: Mapped[str] = mapped_column(String(20), default="unknown")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # CTI / 관계
    manufacturer_detail = relationship("SupplierManufacturerDetail", back_populates="supplier", uselist=False)
    miner_detail = relationship("SupplierMinerDetail", back_populates="supplier", uselist=False)
    factories = relationship("SupplierFactory", back_populates="supplier")
    contacts = relationship("SupplierContact", back_populates="supplier")
    onboarding = relationship("SupplierOnboarding", back_populates="supplier", uselist=False)
    risk_profile = relationship("SupplierRiskProfile", back_populates="supplier", uselist=False)
    parent_supplier = relationship("Supplier", remote_side=[supplier_id], back_populates="child_suppliers")
    child_suppliers = relationship("Supplier", back_populates="parent_supplier")


class SupplierFactory(Base):
    """공장 단위 원산지 추적의 불변 핵심 기준점. schema.sql 컬럼 전수 정합."""
    __tablename__ = "supplier_factories"

    factory_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("suppliers.supplier_id", ondelete="CASCADE"), nullable=False
    )
    factory_name: Mapped[Optional[str]] = mapped_column(String(255))
    factory_name_en: Mapped[Optional[str]] = mapped_column(String(255))
    address: Mapped[Optional[str]] = mapped_column(Text)
    country: Mapped[Optional[str]] = mapped_column(String(2))   # ISO 3166-1 alpha-2
    region: Mapped[Optional[str]] = mapped_column(String(100))
    location = mapped_column(Geometry(geometry_type="POINT", srid=4326), nullable=True)
    factory_role: Mapped[Optional[str]] = mapped_column(String(30))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    operating_period_from: Mapped[Optional[date]] = mapped_column(Date)
    operating_period_to: Mapped[Optional[date]] = mapped_column(Date)
    monthly_capacity: Mapped[Optional[str]] = mapped_column(String(100))
    destination: Mapped[Optional[str]] = mapped_column(String(10))   # EU / US / KR / BOTH
    destination_detail: Mapped[Optional[str]] = mapped_column(Text)
    applicable_regulations: Mapped[Optional[dict]] = mapped_column(JSONB)  # 공장별 차등 규제 배열
    hidden_regulations: Mapped[Optional[dict]] = mapped_column(JSONB)
    supply_ratio_percent: Mapped[Optional[float]] = mapped_column(NUMERIC(5, 2))
    supply_quantity: Mapped[Optional[str]] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    supplier = relationship("Supplier", back_populates="factories")


class SupplierContact(Base):
    __tablename__ = "supplier_contacts"

    contact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("suppliers.supplier_id", ondelete="CASCADE")
    )
    factory_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("supplier_factories.factory_id", ondelete="SET NULL")
    )
    name: Mapped[Optional[str]] = mapped_column(String(100))
    name_en: Mapped[Optional[str]] = mapped_column(String(100))
    role: Mapped[Optional[str]] = mapped_column(String(50))
    department: Mapped[Optional[str]] = mapped_column(String(100))
    email: Mapped[Optional[str]] = mapped_column(String(255))
    phone: Mapped[Optional[str]] = mapped_column(String(50))
    mobile: Mapped[Optional[str]] = mapped_column(String(50))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    language: Mapped[Optional[str]] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    supplier = relationship("Supplier", back_populates="contacts")


class SupplierOnboarding(Base):
    """동의 단계 + 2주 Onboarding SLA 독촉 추적."""
    __tablename__ = "supplier_onboarding"

    onboarding_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("suppliers.supplier_id", ondelete="CASCADE")
    )
    consent_status: Mapped[str] = mapped_column(String(20), default="consent_pending")
    consent_signed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    agreement_status: Mapped[str] = mapped_column(String(20), default="pending")
    agreement_signed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_invited_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_reminded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    sla_due_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reminder_count: Mapped[int] = mapped_column(Integer, default=0)

    supplier = relationship("Supplier", back_populates="onboarding")


# ============================================================
# 영역 3. Provider Type별 CTI 상세
# ============================================================
class SupplierManufacturerDetail(Base):
    __tablename__ = "supplier_manufacturer_details"
    detail_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("suppliers.supplier_id", ondelete="CASCADE"), nullable=False
    )
    manufacturing_process: Mapped[Optional[str]] = mapped_column(Text)
    energy_source: Mapped[Optional[str]] = mapped_column(String(100))
    capacity: Mapped[Optional[str]] = mapped_column(String(100))
    carbon_intensity: Mapped[Optional[float]] = mapped_column(NUMERIC(10, 4))  # kgCO2eq/kg (EU 배터리법 Art.7)
    supplier = relationship("Supplier", back_populates="manufacturer_detail")


class FactoryCarbonDeclaration(Base):
    """
    공장 단위 1차 탄소 선언(EU 배터리법 Art.7 PEF). schema.sql:555 전수 정합.
    배치 판정 시 supply_ratio 가중평균의 입력. supplier_factories에 종속.
    선언 누락 공장이 있으면 compliance에서 needs_human_review 처리.
    """
    __tablename__ = "factory_carbon_declarations"
    declaration_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    factory_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("supplier_factories.factory_id", ondelete="CASCADE"), nullable=False
    )
    carbon_intensity: Mapped[float] = mapped_column(NUMERIC(10, 4), nullable=False)  # kg CO2e/kWh (PEF)
    methodology: Mapped[Optional[str]] = mapped_column(String(50))
    declared_at: Mapped[date] = mapped_column(Date, nullable=False)
    valid_from: Mapped[Optional[date]] = mapped_column(Date)
    valid_to: Mapped[Optional[date]] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(30), default="supplier_declared")  # supplier_declared/third_party_verified/estimated
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SupplierMinerDetail(Base):
    __tablename__ = "supplier_miner_details"
    detail_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("suppliers.supplier_id", ondelete="CASCADE"), nullable=False
    )
    mine_name: Mapped[Optional[str]] = mapped_column(String(255))
    mining_method: Mapped[Optional[str]] = mapped_column(String(50))
    extraction_volume: Mapped[Optional[float]] = mapped_column(NUMERIC(15, 2))
    mine_coordinates = mapped_column(Geometry(geometry_type="POINT", srid=4326), nullable=True)
    active_period_from: Mapped[Optional[date]] = mapped_column(Date)
    active_period_to: Mapped[Optional[date]] = mapped_column(Date)
    supplier = relationship("Supplier", back_populates="miner_detail")


# ============================================================
# 영역 4. 리스크 프로필
# ============================================================
class SupplierRiskProfile(Base):
    __tablename__ = "supplier_risk_profiles"

    profile_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # schema.sql: UNIQUE(supplier_id) — supplier당 1개 row
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("suppliers.supplier_id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )
    # 가점식 0~100 (↑위험). 대역: 0~29 low / 30~49 medium / 50~69 high / 70~100 critical
    overall_risk_score: Mapped[int] = mapped_column(Integer, default=0)
    risk_level: Mapped[str] = mapped_column(String(20), default="low")
    # 협력사 실사 자가진단(self-assessed) 결과 — low/medium/high/critical/unknown. (시스템 risk_level과 별개)
    self_reported_risk_level: Mapped[str] = mapped_column(String(20), default="unknown")
    feoc_status: Mapped[str] = mapped_column(String(20), default="unknown")
    feoc_direct_ownership: Mapped[Optional[float]] = mapped_column(NUMERIC(5, 2))
    feoc_indirect_ownership: Mapped[Optional[float]] = mapped_column(NUMERIC(5, 2))
    feoc_last_assessed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    feoc_cert_expiry: Mapped[Optional[date]] = mapped_column(Date)
    is_high_risk_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    high_risk_reasons: Mapped[Optional[dict]] = mapped_column(JSONB)
    last_risk_review_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    supplier = relationship("Supplier", back_populates="risk_profile")


class SupplierAuditRecord(Base):
    """공급망 실사(Due Diligence) 수행 실적 (CSDDD 대응). v_action_items 'DD' 소스."""
    __tablename__ = "supplier_audit_records"

    audit_record_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("suppliers.supplier_id", ondelete="CASCADE")
    )
    audit_date: Mapped[date] = mapped_column(Date, nullable=False)
    audit_type: Mapped[Optional[str]] = mapped_column(String(30))   # on_site/remote/document_review/third_party
    auditor: Mapped[Optional[str]] = mapped_column(String(255))
    # audit_status = 실사 '진행 단계'(워크플로우), result = 실사 '결과'(판정). 별개 축.
    audit_status: Mapped[str] = mapped_column(String(20), default="requested")
    inspector_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.user_id"))
    audit_scope: Mapped[Optional[str]] = mapped_column(Text)
    result: Mapped[Optional[str]] = mapped_column(String(30))       # pass/conditional_pass/fail/pending
    findings: Mapped[Optional[dict]] = mapped_column(JSONB)
    corrective_actions: Mapped[Optional[dict]] = mapped_column(JSONB)
    next_audit_due: Mapped[Optional[date]] = mapped_column(Date)
    report_url: Mapped[Optional[str]] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ============================================================
# Pydantic 입출력 스키마(DTO)
# ============================================================
class SupplierCreateRequest(BaseModel):
    tenant_id: uuid.UUID
    company_name: str
    provider_type: str
    email: str
    # [G1] 협력사→협력사 초대 시 이동 주체(초대한 협력사). 원청 직접 등록이면 None.
    inviter_supplier_id: Optional[uuid.UUID] = None


class SupplierBrief(BaseModel):
    """목록·단건 응답용 직렬화 스키마(ORM relationship lazy load 직렬화 에러 방지)."""
    supplier_id: uuid.UUID
    company_name: str
    provider_type: str
    status: str
    risk_level: str

    model_config = {"from_attributes": True}


class RiskProfileResponse(BaseModel):
    supplier_id: uuid.UUID
    overall_risk_score: int
    risk_level: str
    self_reported_risk_level: Optional[str] = "unknown"  # 협력사 실사 자가진단 결과
    feoc_status: Optional[str] = "unknown"

    model_config = {"from_attributes": True}


class RiskScoreUpdateRequest(BaseModel):
    score: int


class SupplierDetailUpdateRequest(BaseModel):
    """협력사 '자료 제출' — 입력 양식 영속화. 보낸 필드만 갱신(exclude_unset).
    suppliers 컬럼 + 소재(core_minerals) + 규제(탄소→manufacturer_details,
    실사 자가진단→risk_profiles)를 cross-table 로 저장한다(service가 분배)."""
    # 기업 기본정보 (suppliers)
    company_name: Optional[str] = None
    company_name_en: Optional[str] = None
    company_name_ko: Optional[str] = None
    country: Optional[str] = None
    business_reg_no: Optional[str] = None
    duns_number: Optional[str] = None
    provider_type: Optional[str] = None
    smelter_type: Optional[str] = None
    # 소재 구성 (suppliers.core_minerals)
    core_minerals: Optional[dict] = None
    # 규제 — 탄소발자국 (supplier_manufacturer_details)
    carbon_intensity: Optional[float] = None
    energy_source: Optional[str] = None
    # 규제 — 실사 자가진단 (supplier_risk_profiles)
    self_reported_risk_level: Optional[str] = None
    # 필요문서 업로드 URL(확인용) — suppliers 컬럼. 파일명/URL을 저장해 '어떤 문서 올렸는지' 확인.
    business_reg_doc_url: Optional[str] = None       # 사업자등록증
    environmental_report_url: Optional[str] = None   # 환경성적서
    self_assessment_doc_url: Optional[str] = None     # 실사 자가진단 보고서


# ----- CTI 상세 응답 DTO (목요일: provider type별 상세 노출) -----
class ManufacturerDetailDTO(BaseModel):
    manufacturing_process: Optional[str] = None
    energy_source: Optional[str] = None
    capacity: Optional[str] = None
    carbon_intensity: Optional[float] = None
    model_config = {"from_attributes": True}


class MinerDetailDTO(BaseModel):
    mine_name: Optional[str] = None
    mining_method: Optional[str] = None
    extraction_volume: Optional[float] = None
    active_period_from: Optional[date] = None
    model_config = {"from_attributes": True}


class SupplierDetailResponse(BaseModel):
    """
    단건 상세 — 기본 필드 + provider type에 해당하는 CTI 1종만 채워 반환.
    provider_type에 맞지 않는 detail은 None(예: manufacturer면 miner_detail=None).
    """
    supplier_id: uuid.UUID
    company_name: str
    # 기업 기본정보 섹션용 — suppliers 테이블 컬럼(있으면 채움).
    company_name_en: Optional[str] = None
    company_name_ko: Optional[str] = None
    ceo_name: Optional[str] = None
    business_reg_no: Optional[str] = None
    duns_number: Optional[str] = None
    website: Optional[str] = None
    established_year: Optional[int] = None
    employee_count: Optional[int] = None
    completeness_score: Optional[int] = None
    provider_type: str
    smelter_type: Optional[str] = None  # provider_type='smelter'일 때 rmi/private
    core_minerals: Optional[dict] = None  # 소재 구성: 핵심광물 함량(%)
    country: Optional[str] = None  # 소재 국가(ISO 3166-1 alpha-2)
    business_reg_doc_url: Optional[str] = None  # 사업자등록증 업로드 URL(확인용)
    environmental_report_url: Optional[str] = None  # 환경성적서 업로드 URL(확인용)
    self_assessment_doc_url: Optional[str] = None  # 실사 자가진단 보고서 업로드 URL(확인용)
    status: str
    risk_level: str
    feoc_status: str
    manufacturer_detail: Optional[ManufacturerDetailDTO] = None
    miner_detail: Optional[MinerDetailDTO] = None
    model_config = {"from_attributes": True}


# ----- BE-3: 탭 모달 조회 DTO (기존 테이블 SELECT 전용) -----
# 사업장(공장/광산) 탭: supplier_factories. location(PostGIS POINT)은 lat/lng로 분해해 노출.
class FactoryDTO(BaseModel):
    factory_id: uuid.UUID
    factory_name: Optional[str] = None
    factory_name_en: Optional[str] = None
    address: Optional[str] = None
    country: Optional[str] = None
    region: Optional[str] = None
    factory_role: Optional[str] = None
    is_active: Optional[bool] = None
    operating_period_from: Optional[date] = None
    operating_period_to: Optional[date] = None
    monthly_capacity: Optional[str] = None
    destination: Optional[str] = None
    destination_detail: Optional[str] = None
    supply_ratio_percent: Optional[float] = None
    supply_quantity: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    model_config = {"from_attributes": True}


class SupplierFactoriesResponse(BaseModel):
    supplier_id: uuid.UUID
    factories: list[FactoryDTO] = []


# 담당자 연락처 탭 — supplier_contacts 다건.
class ContactDTO(BaseModel):
    contact_id: uuid.UUID
    factory_id: Optional[uuid.UUID] = None
    name: Optional[str] = None
    name_en: Optional[str] = None
    role: Optional[str] = None
    department: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    mobile: Optional[str] = None
    is_primary: bool = False
    language: Optional[str] = None
    model_config = {"from_attributes": True}


class SupplierContactsResponse(BaseModel):
    supplier_id: uuid.UUID
    contacts: list[ContactDTO] = []


# 입력 완성도 — data_completeness_status(entity_type='supplier').
class SupplierCompletenessResponse(BaseModel):
    supplier_id: uuid.UUID
    required_field_count: Optional[int] = None
    filled_field_count: Optional[int] = None
    completion_rate: Optional[float] = None
    missing_fields: list[str] = []
    last_updated_at: Optional[datetime] = None


# 환경성적서(탄소발자국, EU 배터리법 Art7) — 공장별 factory_carbon_declarations.
class CarbonDeclarationDTO(BaseModel):
    declaration_id: uuid.UUID
    factory_id: uuid.UUID
    factory_name: Optional[str] = None
    carbon_intensity: Optional[float] = None     # kg CO2e/kWh
    methodology: Optional[str] = None            # 예: PEF
    declared_at: Optional[date] = None
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None
    source: Optional[str] = None                 # supplier_declared / third_party_verified / estimated
    is_active: Optional[bool] = None
    model_config = {"from_attributes": True}


class SupplierCarbonDeclsResponse(BaseModel):
    supplier_id: uuid.UUID
    declarations: list[CarbonDeclarationDTO] = []


# 공급 품목 — supply_chain_map에서 이 협력사가 공급하는 부품.
class SuppliedItemDTO(BaseModel):
    part_id: uuid.UUID
    part_code: Optional[str] = None
    part_name: Optional[str] = None
    tier_level: Optional[int] = None
    material_type: Optional[str] = None
    # [공급원 변경 자진신고] declare_source_change 실호출용 BOM 컨텍스트.
    bom_version_id: Optional[uuid.UUID] = None
    bom_version_number: Optional[str] = None
    model_config = {"from_attributes": True}


class SupplierSuppliedItemsResponse(BaseModel):
    supplier_id: uuid.UUID
    items: list[SuppliedItemDTO] = []


# Reliability(신뢰도) 탭: 완성도 + 리스크 프로필 + 온보딩 SLA + 실사 요약.
class SupplierReliabilityResponse(BaseModel):
    supplier_id: uuid.UUID
    completeness_score: int = 0
    # 리스크 프로필 (없으면 None)
    overall_risk_score: Optional[int] = None
    risk_level: Optional[str] = None
    feoc_status: Optional[str] = None
    is_high_risk_flag: Optional[bool] = None
    last_risk_review_at: Optional[datetime] = None
    # 온보딩/SLA 성실도
    consent_status: Optional[str] = None
    agreement_status: Optional[str] = None
    sla_due_date: Optional[datetime] = None
    reminder_count: Optional[int] = None
    last_reminded_at: Optional[datetime] = None
    # 실사 요약
    total_audits: int = 0
    last_audit_date: Optional[date] = None
    last_audit_result: Optional[str] = None


# ============================================================
# 마스터폼(표준화된 입력양식) 계약 모델 — SSOT (담당: 팀원 B / KIRA W5 §4)
#
# 협력사가 보는 것은 '하나의 표준화된 입력양식'이다. 역할 분기 없이 모든
# 협력사가 동일 양식을 보고, 역할에 해당없는 섹션 칸은 None으로 비활성한다.
# POST /suppliers/{supplier_id}/master-form 로 한 번에 제출되면, service가
# 섹션별로 쪼개 각 도메인 repository의 write 함수를 호출하고 **단일 트랜잭션으로
# commit(atomic)** 한다. 한 섹션 실패 시 전체 롤백. 신규 테이블은 만들지 않는다.
#
# 이 모델이 SSOT다. 프론트·각 도메인 담당(C/D/E)이 이 계약을 두고 시작한다.
#
# 섹션 → 저장 테이블 매핑(§4):
#   0 회사·공장·PIC      → suppliers / supplier_factories / supplier_contacts          (B)
#   1 탄소발자국         → supplier_manufacturer_details / factory_carbon_declarations (B)
#
# 좌표는 ordering 혼선(PostGIS=lng/lat, Leaflet=lat/lng)을 구조적으로 차단하기
# 위해 latitude/longitude 명시 필드(GeoPoint)로 받는다. POINT 변환은
# write 함수가 담당한다(섹션 0 공장 location = B).
# ============================================================
class GeoPoint(BaseModel):
    """좌표 — 입력은 lat/lng 명시, POINT(srid=4326) 변환은 write 함수가 수행."""
    latitude: float
    longitude: float


# ----- 섹션 0: 회사·공장·PIC (suppliers / supplier_factories / supplier_contacts) -----
class MasterFormCompany(BaseModel):
    """섹션 0 회사 — suppliers."""
    company_name: str
    company_name_en: Optional[str] = None
    company_name_ko: Optional[str] = None
    short_name_en: Optional[str] = None
    short_name_ko: Optional[str] = None
    ceo_name: Optional[str] = None
    business_reg_no: Optional[str] = None
    corporate_reg_no: Optional[str] = None
    duns_number: Optional[str] = None
    tax_number: Optional[str] = None
    website: Optional[str] = None
    provider_type: str  # manufacturer/recycler/trader/miner/smelter (chk_provider_type)
    smelter_type: Optional[str] = None  # provider_type='smelter'일 때 rmi/private
    core_minerals: Optional[dict] = None  # 소재 구성: 핵심광물 함량(%) {"Li":..,"Co":..,"Ni":..}
    country: Optional[str] = None  # 소재 국가(ISO 3166-1 alpha-2)
    business_reg_doc_url: Optional[str] = None  # 사업자등록증 업로드 URL
    environmental_report_url: Optional[str] = None  # 환경성적서 업로드 URL
    self_assessment_doc_url: Optional[str] = None  # 실사 자가진단 보고서 업로드 URL
    established_year: Optional[int] = None
    employee_count: Optional[int] = None


class MasterFormFactory(BaseModel):
    """섹션 0 공장 — supplier_factories (다건). location은 B가 POINT 변환."""
    factory_name: Optional[str] = None
    factory_name_en: Optional[str] = None
    address: Optional[str] = None
    country: Optional[str] = None        # ISO 3166-1 alpha-2
    region: Optional[str] = None
    coordinates: Optional[GeoPoint] = None   # → location GEOMETRY(POINT,4326)
    factory_role: Optional[str] = None   # headquarters/production/outsourcing/processing/mining
    operating_period_from: Optional[date] = None
    operating_period_to: Optional[date] = None
    monthly_capacity: Optional[str] = None
    destination: Optional[str] = None    # EU/US/KR/BOTH
    destination_detail: Optional[str] = None
    applicable_regulations: Optional[list] = None
    supply_ratio_percent: Optional[float] = None
    supply_quantity: Optional[str] = None


class MasterFormContact(BaseModel):
    """섹션 0 PIC(담당자) — supplier_contacts (다건)."""
    name: Optional[str] = None
    name_en: Optional[str] = None
    role: Optional[str] = None
    department: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    mobile: Optional[str] = None
    is_primary: bool = False
    language: Optional[str] = None


# ----- 섹션 1: 탄소발자국 (supplier_manufacturer_details / factory_carbon_declarations) -----
class MasterFormFactoryCarbon(BaseModel):
    """섹션 1 공장 단위 1차 탄소 선언 — factory_carbon_declarations (다건)."""
    # factories 리스트 내 인덱스로 공장을 가리킨다(같은 트랜잭션에서 공장이 먼저 생성됨).
    factory_index: Optional[int] = None
    carbon_intensity: float                  # kg CO2e/kWh (PEF 기반)
    methodology: Optional[str] = None        # 예: 'PEF'
    declared_at: date
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None
    source: str = "supplier_declared"        # supplier_declared/third_party_verified/estimated


class MasterFormManufacturing(BaseModel):
    """섹션 1 탄소발자국 — supplier_manufacturer_details + factory_carbon_declarations."""
    manufacturing_process: Optional[str] = None
    energy_source: Optional[str] = None
    capacity: Optional[str] = None
    carbon_intensity: Optional[float] = None         # 공급사 단위 집약도(kgCO2eq/kg)
    factory_declarations: list[MasterFormFactoryCarbon] = []


# ----- 최상위 마스터폼 요청/응답 -----
class MasterFormRequest(BaseModel):
    """
    마스터폼 전체 요청 — POST /suppliers/{supplier_id}/master-form.

    섹션 0~1 전체. 역할에 해당없는 섹션은 None 허용(섹션 0 company만 필수).
    service가 섹션별로 쪼개 각 도메인 write 함수 호출 → 단일 트랜잭션 commit(atomic).
    """
    company: MasterFormCompany                                  # 섹션 0 (필수)
    factories: list[MasterFormFactory] = []                     # 섹션 0
    contacts: list[MasterFormContact] = []                      # 섹션 0
    manufacturing: Optional[MasterFormManufacturing] = None     # 섹션 1
    # 규제 — 실사 자가진단 결과(협력사 자가신고). low/medium/high (→ risk_profiles.self_reported_risk_level)
    self_reported_risk_level: Optional[str] = None


class MasterFormResponse(BaseModel):
    """마스터폼 제출 결과 — 저장된 섹션 키 목록을 함께 반환."""
    supplier_id: uuid.UUID
    status: str
    sections_saved: list[str] = []   # 예: ["company", "factories", "recycling"]


# ----- AP: 마스터폼 AI 자동 채움(prefill) 응답 -----
class MasterFormPrefillResponse(BaseModel):
    """
    GET /suppliers/{id}/master-form/prefill — 협력사 보완 문서 추출결과를 마스터폼
    섹션 구조로 모은 초안. 협력사는 prefill을 검토·정정 후 master-form으로 제출한다.
    low_confidence_fields는 신뢰도 임계치 미만이라 '확인 요청'이 필요한 항목 목록.
    """
    supplier_id: uuid.UUID
    document_count: int = 0          # 추출결과가 모인 문서 수(0이면 업로드 전)
    unconfirmed_documents: int = 0   # 협력사 미확인(confirm 전) 문서 수
    prefill: dict = {}               # {"company": {...}, "manufacturing": {...}, ...}
    low_confidence_fields: list[dict] = []