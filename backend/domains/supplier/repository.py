"""
domains/supplier/repository.py  (담당: 팀원 B)

Supplier 도메인 DB 접근 계층.
- 커밋하지 않는다(flush만). 커밋은 service가 일원화해서 책임진다.
- CTI 상세는 selectinload로 미리 로드(N+1·lazy load 방지).
"""
from typing import List, Optional
from uuid import UUID
 
from sqlalchemy import select, update, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
 
from backend.domains.supplier.models import (
    Supplier,
    SupplierRiskProfile,
    SupplierCertification,
    SupplierHumanRightsIssue,
    SupplierIndustrialAccident,
    SupplierAuditRecord,
    SupplierOnboarding,
    SupplierFactory,
    TrainingRecord,
)

async def create_supplier(db: AsyncSession, supplier_data: dict) -> Supplier:
    """협력사 INSERT. flush까지만(커밋은 service)."""
    supplier = Supplier(**supplier_data)
    db.add(supplier)
    await db.flush()
    return supplier

async def get_supplier_by_id(
    db: AsyncSession,
    supplier_id: UUID,
) -> Optional[Supplier]:
    """단건 조회. CTI 상세 + 공장을 미리 로드."""
    stmt = (
        select(Supplier)
        .where(Supplier.supplier_id == supplier_id)
        .options(
            selectinload(Supplier.manufacturer_detail),
            selectinload(Supplier.recycler_detail),
            selectinload(Supplier.trader_detail),
            selectinload(Supplier.miner_detail),
            selectinload(Supplier.factories),
        )
    )
    result = await db.execute(stmt)
    return result.scalars().first()

async def get_suppliers(
    db: AsyncSession,
    status: Optional[str] = None,
    risk_level: Optional[str] = None,
    feoc_status: Optional[str] = None,
    page: int = 1,
    size: int = 20,
) -> List[Supplier]:
    """목록 조회(필터 + 페이지네이션). 기본값 None 인자는 Optional로 명시."""
    stmt = select(Supplier)
    if status:
        stmt = stmt.where(Supplier.status == status)
    if risk_level:
        stmt = stmt.where(Supplier.risk_level == risk_level)
    if feoc_status:
        stmt = stmt.where(Supplier.feoc_status == feoc_status)
 
    # 서버 강제 페이지네이션 (N+1 방지 + 응답 크기 제한)
    page = max(page, 1)
    size = min(max(size, 1), 100)
    stmt = (
        stmt.order_by(Supplier.created_at.desc())
        .limit(size)
        .offset((page - 1) * size)
    )
 
    result = await db.execute(stmt)
    return result.scalars().all()
 
async def get_risk_profile_by_supplier(
    db: AsyncSession,
    supplier_id: UUID,
) -> Optional[SupplierRiskProfile]:
    """supplier_risk_profiles 단건 조회 (supplier당 1개)."""
    stmt = select(SupplierRiskProfile).where(
        SupplierRiskProfile.supplier_id == supplier_id
    )
    result = await db.execute(stmt)
    return result.scalars().first()
 
 
async def upsert_risk_profile(
    db: AsyncSession,
    supplier_id: UUID,
    overall_risk_score: int,
    risk_level: str,
    last_risk_review_at=None,
) -> SupplierRiskProfile:
    """
    supplier_risk_profiles upsert.
    UNIQUE(supplier_id) 충돌 시 점수·레벨만 갱신(ON CONFLICT DO UPDATE).
    flush까지만 — 커밋은 service(risk_service.upsert_risk_score)가 책임진다.
    """
    values = {
        "supplier_id": supplier_id,
        "overall_risk_score": overall_risk_score,
        "risk_level": risk_level,
        "last_risk_review_at": last_risk_review_at,
    }
    stmt = (
        pg_insert(SupplierRiskProfile)
        .values(**values)
        .on_conflict_do_update(
            index_elements=[SupplierRiskProfile.supplier_id],
            set_={
                "overall_risk_score": overall_risk_score,
                "risk_level": risk_level,
                "last_risk_review_at": last_risk_review_at,
            },
        )
        .returning(SupplierRiskProfile)
    )
    result = await db.execute(stmt)
    await db.flush()
    return result.scalars().first()
 
 
async def update_supplier_risk_level(
    db: AsyncSession,
    supplier_id: UUID,
    risk_level: str,
) -> None:
    """
    suppliers.risk_level 비정규화 캐시 동기화.
    목록 필터·공급망 맵 노드 컬러가 이 컬럼을 읽으므로 프로필 갱신과 함께 맞춘다.
    flush까지만 — 커밋은 service.
    """
    stmt = (
        update(Supplier)
        .where(Supplier.supplier_id == supplier_id)
        .values(risk_level=risk_level)
    )
    await db.execute(stmt)
    await db.flush()


# ============================================================
# BE-3: 7탭 모달 조회 (기존 테이블 SELECT 전용 · 커밋/변경 없음)
# ============================================================
async def get_certifications(
    db: AsyncSession, supplier_id: UUID
) -> List[SupplierCertification]:
    """ESG 탭 — 일반 인증서(ISO 14001 등) 목록. 발급일 최신순."""
    stmt = (
        select(SupplierCertification)
        .where(SupplierCertification.supplier_id == supplier_id)
        .order_by(SupplierCertification.issued_at.desc().nullslast())
    )
    result = await db.execute(stmt)
    return result.scalars().all()


async def get_human_rights_issues(
    db: AsyncSession, supplier_id: UUID
) -> List[SupplierHumanRightsIssue]:
    """ESG 탭 — 인권 이슈 목록. 탐지 시각 최신순."""
    stmt = (
        select(SupplierHumanRightsIssue)
        .where(SupplierHumanRightsIssue.supplier_id == supplier_id)
        .order_by(SupplierHumanRightsIssue.detected_at.desc().nullslast())
    )
    result = await db.execute(stmt)
    return result.scalars().all()


async def get_industrial_accidents(
    db: AsyncSession, supplier_id: UUID
) -> List[SupplierIndustrialAccident]:
    """ESG 탭 — 산업재해 목록. 발생일 최신순."""
    stmt = (
        select(SupplierIndustrialAccident)
        .where(SupplierIndustrialAccident.supplier_id == supplier_id)
        .order_by(SupplierIndustrialAccident.accident_date.desc())
    )
    result = await db.execute(stmt)
    return result.scalars().all()


async def get_audit_records(
    db: AsyncSession, supplier_id: UUID
) -> List[SupplierAuditRecord]:
    """ESG·Reliability 탭 — 실사(Due Diligence) 기록. 실사일 최신순."""
    stmt = (
        select(SupplierAuditRecord)
        .where(SupplierAuditRecord.supplier_id == supplier_id)
        .order_by(SupplierAuditRecord.audit_date.desc())
    )
    result = await db.execute(stmt)
    return result.scalars().all()


async def get_training_records(
    db: AsyncSession, supplier_id: UUID
) -> List[TrainingRecord]:
    """Training 탭 — 교육 이수 기록 + 교육 자료 메타(selectinload). 마감일 최신순."""
    stmt = (
        select(TrainingRecord)
        .where(TrainingRecord.supplier_id == supplier_id)
        .options(selectinload(TrainingRecord.material))
        .order_by(TrainingRecord.due_date.desc())
    )
    result = await db.execute(stmt)
    return result.scalars().all()


async def get_onboarding_by_supplier(
    db: AsyncSession, supplier_id: UUID
) -> Optional[SupplierOnboarding]:
    """Reliability 탭 — 온보딩/SLA 성실도 단건(supplier당 1개)."""
    stmt = select(SupplierOnboarding).where(
        SupplierOnboarding.supplier_id == supplier_id
    )
    result = await db.execute(stmt)
    return result.scalars().first()


async def get_factories(db: AsyncSession, supplier_id: UUID) -> List[dict]:
    """
    사업장 탭 — supplier_factories 목록.
    location(PostGIS POINT)은 직렬화 불가 → ST_Y/ST_X 로 latitude/longitude 분해해 반환.
    등록순(created_at) 정렬.
    """
    stmt = (
        select(
            SupplierFactory.factory_id,
            SupplierFactory.factory_name,
            SupplierFactory.factory_name_en,
            SupplierFactory.address,
            SupplierFactory.country,
            SupplierFactory.region,
            SupplierFactory.factory_role,
            SupplierFactory.is_active,
            SupplierFactory.operating_period_from,
            SupplierFactory.operating_period_to,
            SupplierFactory.monthly_capacity,
            SupplierFactory.destination,
            SupplierFactory.destination_detail,
            SupplierFactory.supply_ratio_percent,
            SupplierFactory.supply_quantity,
            func.ST_Y(SupplierFactory.location).label("latitude"),
            func.ST_X(SupplierFactory.location).label("longitude"),
        )
        .where(SupplierFactory.supplier_id == supplier_id)
        .order_by(SupplierFactory.created_at.asc())
    )
    result = await db.execute(stmt)
    return [dict(row._mapping) for row in result]