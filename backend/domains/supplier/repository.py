"""
domains/supplier/repository.py  (담당: 팀원 B)

Supplier 도메인 DB 접근 계층.
- 커밋하지 않는다(flush만). 커밋은 service가 일원화해서 책임진다.
- CTI 상세는 selectinload로 미리 로드(N+1·lazy load 방지).
"""
from typing import List, Optional
from uuid import UUID
 
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
 
from backend.domains.supplier.models import Supplier, SupplierRiskProfile

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