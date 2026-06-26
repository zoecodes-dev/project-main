"""
domains/supplier/repository.py  (담당: 팀원 B)

Supplier 도메인 DB 접근 계층.
- 커밋하지 않는다(flush만). 커밋은 service가 일원화해서 책임진다.
- CTI 상세는 selectinload로 미리 로드(N+1·lazy load 방지).
"""
from typing import List, Optional
from uuid import UUID
 
from sqlalchemy import select, update, delete, text, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.domains.supplier.models import (
    Supplier,
    SupplierMinerDetail,
    SupplierRiskProfile,
    SupplierCertification,
    SupplierHumanRightsIssue,
    SupplierIndustrialAccident,
    SupplierAuditRecord,
    SupplierOnboarding,
    SupplierFactory,
    SupplierContact,
    SupplierManufacturerDetail,
    SupplierRecyclerDetail,
    FactoryCarbonDeclaration,
    TrainingRecord,
    MasterFormCompany,
    MasterFormFactory,
    MasterFormContact,
    MasterFormManufacturing,
    MasterFormRecycling,
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
    tenant_id: Optional[UUID] = None,
) -> Optional[Supplier]:
    """
    단건 조회. CTI 상세 + 공장을 미리 로드.
    tenant_id 지정 시 해당 테넌트 소유만(§0.2) — 남의 테넌트 것은 None(→호출부 404).
    내부 플로우(마스터폼 등)는 tenant_id 생략해 무필터로 쓴다.
    """
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
    if tenant_id is not None:
        stmt = stmt.where(Supplier.tenant_id == tenant_id)
    result = await db.execute(stmt)
    return result.scalars().first()

async def supplier_in_tenant(
    db: AsyncSession,
    supplier_id: UUID,
    tenant_id: Optional[UUID],
) -> bool:
    """
    소유권 경량 확인(§0.2). 하위 리소스(esg/training/factories…) 조회 전 게이트용.
    tenant_id=None 이면 무스코프(True 반환 — 내부/관리 토큰). PK·tenant만 조회해 가볍다.
    """
    stmt = select(Supplier.supplier_id).where(Supplier.supplier_id == supplier_id)
    if tenant_id is not None:
        stmt = stmt.where(Supplier.tenant_id == tenant_id)
    result = await db.execute(stmt)
    return result.scalars().first() is not None

async def get_suppliers(
    db: AsyncSession,
    status: Optional[str] = None,
    risk_level: Optional[str] = None,
    feoc_status: Optional[str] = None,
    page: int = 1,
    size: int = 20,
    tenant_id: Optional[UUID] = None,
) -> List[Supplier]:
    """목록 조회(필터 + 페이지네이션). 기본값 None 인자는 Optional로 명시."""
    stmt = select(Supplier)
    if tenant_id is not None:
        stmt = stmt.where(Supplier.tenant_id == tenant_id)
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

async def count_suppliers(
    db: AsyncSession,
    status: Optional[str] = None,
    risk_level: Optional[str] = None,
    feoc_status: Optional[str] = None,
    tenant_id: Optional[UUID] = None,
) -> int:
    """목록 전체 건수(필터 적용, 페이지 무관). X-Total-Count 헤더용(§0.6)."""
    stmt = select(func.count()).select_from(Supplier)
    if tenant_id is not None:
        stmt = stmt.where(Supplier.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(Supplier.status == status)
    if risk_level:
        stmt = stmt.where(Supplier.risk_level == risk_level)
    if feoc_status:
        stmt = stmt.where(Supplier.feoc_status == feoc_status)
    result = await db.execute(stmt)
    return int(result.scalar_one())

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


# ============================================================
# 마스터폼 섹션 0~2 write (담당: 팀원 B / KIRA W5 §4)
#
# B(service.py)의 submit_master_form이 단일 트랜잭션 내에서 호출한다.
# ★ 여기서 commit/rollback 하지 않는다(flush까지만). 커밋은 service가 단 한 번.
# 1-per-supplier 상세 테이블(manufacturer/recycler)과 다건 테이블(factories/
# contacts/carbon)은 모두 'replace-all'(기존 삭제 후 재입력) — E의 섹션 4~6과 동일 패턴.
# 마스터폼은 협력사가 보는 단일 양식의 '현재 상태'를 통째로 반영하기 때문이다.
# ============================================================
def _factory_id_at(idx: Optional[int], factory_ids: List[UUID]) -> Optional[UUID]:
    """factory_index(폼 factories 리스트 내 순서) → 실제 factory_id. 범위 밖이면 None."""
    if idx is None or not factory_ids:
        return None
    return factory_ids[idx] if 0 <= idx < len(factory_ids) else None


# ── 섹션 0: 회사 (suppliers UPDATE) ───────────────────────────────────────
async def write_master_form_company(
    db: AsyncSession, supplier_id: UUID, data: MasterFormCompany
) -> None:
    """
    섹션 0 회사 — suppliers 갱신.
    협력사 row는 초대 시점(create_supplier_and_invite)에 이미 존재하므로 INSERT가
    아니라 UPDATE다. 폼에 담긴 회사 식별·기본정보 전체를 권위값으로 덮어쓴다.
    """
    await db.execute(
        update(Supplier)
        .where(Supplier.supplier_id == supplier_id)
        .values(**data.model_dump())
    )
    await db.flush()


# ── 섹션 0: 공장 (supplier_factories replace-all) ─────────────────────────
async def write_master_form_factories(
    db: AsyncSession, supplier_id: UUID, factories: List[MasterFormFactory]
) -> List[UUID]:
    """
    섹션 0 공장 — supplier_factories replace-all. 입력 순서를 보존한 factory_id
    리스트를 반환한다(섹션 1 탄소선언·섹션 5 교육의 factory_index 연결에 사용).
    좌표는 GeoPoint(lat/lng) → EWKT 'SRID=4326;POINT(lng lat)'로 변환(PostGIS=lng/lat).
    """
    await db.execute(delete(SupplierFactory).where(SupplierFactory.supplier_id == supplier_id))
    factory_ids: List[UUID] = []
    for f in factories:
        location = None
        if f.coordinates is not None:
            # PostGIS POINT 순서는 lng/lat. 입력이 lat/lng 명시 필드라 혼선 차단됨.
            location = f"SRID=4326;POINT({f.coordinates.longitude} {f.coordinates.latitude})"
        obj = SupplierFactory(
            supplier_id=supplier_id,
            factory_name=f.factory_name,
            factory_name_en=f.factory_name_en,
            address=f.address,
            country=f.country,
            region=f.region,
            location=location,
            factory_role=f.factory_role,
            operating_period_from=f.operating_period_from,
            operating_period_to=f.operating_period_to,
            monthly_capacity=f.monthly_capacity,
            destination=f.destination,
            destination_detail=f.destination_detail,
            applicable_regulations=f.applicable_regulations,
            supply_ratio_percent=f.supply_ratio_percent,
            supply_quantity=f.supply_quantity,
        )
        db.add(obj)
        await db.flush()   # factory_id 확보(이후 섹션에서 FK로 참조)
        factory_ids.append(obj.factory_id)
    return factory_ids


# ── 섹션 0: PIC (supplier_contacts replace-all) ───────────────────────────
async def write_master_form_contacts(
    db: AsyncSession, supplier_id: UUID, contacts: List[MasterFormContact]
) -> None:
    """섹션 0 담당자 — supplier_contacts replace-all."""
    await db.execute(delete(SupplierContact).where(SupplierContact.supplier_id == supplier_id))
    for c in contacts:
        db.add(SupplierContact(supplier_id=supplier_id, **c.model_dump()))
    await db.flush()


# ── 섹션 1: 탄소발자국 (manufacturer_details + factory_carbon_declarations) ─
async def write_master_form_manufacturing(
    db: AsyncSession,
    supplier_id: UUID,
    factory_ids: List[UUID],
    data: MasterFormManufacturing,
) -> None:
    """
    섹션 1 — supplier_manufacturer_details(1-per-supplier, replace) +
    factory_carbon_declarations(공장별 다건, replace). 탄소선언은 factory_index로
    공장을 가리키며 factory_ids[idx]로 FK를 연결한다. 가리키는 공장이 없으면 스킵
    (추측 저장 금지).
    """
    # manufacturer_details replace
    await db.execute(
        delete(SupplierManufacturerDetail).where(SupplierManufacturerDetail.supplier_id == supplier_id)
    )
    db.add(SupplierManufacturerDetail(
        supplier_id=supplier_id,
        manufacturing_process=data.manufacturing_process,
        energy_source=data.energy_source,
        capacity=data.capacity,
        carbon_intensity=data.carbon_intensity,
    ))

    # factory_carbon_declarations replace (이 협력사 공장들에 한해)
    if factory_ids:
        await db.execute(
            delete(FactoryCarbonDeclaration).where(FactoryCarbonDeclaration.factory_id.in_(factory_ids))
        )
    for decl in data.factory_declarations:
        fid = _factory_id_at(decl.factory_index, factory_ids)
        if fid is None:
            continue
        db.add(FactoryCarbonDeclaration(
            factory_id=fid,
            carbon_intensity=decl.carbon_intensity,
            methodology=decl.methodology,
            declared_at=decl.declared_at,
            valid_from=decl.valid_from,
            valid_to=decl.valid_to,
            source=decl.source,
        ))
    await db.flush()


# ── 섹션 2: 재활용 (supplier_recycler_details replace) ─────────────────────
async def write_master_form_recycling(
    db: AsyncSession, supplier_id: UUID, data: MasterFormRecycling
) -> None:
    """
    섹션 2 — supplier_recycler_details(1-per-supplier, replace).
    recycled_materials는 RecycledMaterialsSchema(B·C 공유 계약)를 dict로 직렬화해 저장.

    recycling_efficiency(소재별 회수율 {"Li":80,"Co":90,...})는 D팀 Wave 0 DDL이
    develop에 머지되어(supplier_recycler_details 컬럼) 함께 저장한다.
    recycled_content_ratio(완성품 내 재활용 패널 비율)와는 별개 축이다.
    """
    await db.execute(
        delete(SupplierRecyclerDetail).where(SupplierRecyclerDetail.supplier_id == supplier_id)
    )
    recycled = (
        data.recycled_materials.model_dump(exclude_none=True)
        if data.recycled_materials is not None else None
    )
    db.add(SupplierRecyclerDetail(
        supplier_id=supplier_id,
        recycled_materials=recycled,
        recycling_certification=data.recycling_certification,
        input_source=data.input_source,
        recycled_content_ratio=data.recycled_content_ratio,
        recycling_efficiency=data.recycling_efficiency,  # 소재별 회수율(D DDL 머지됨)
    ))


async def upsert_miner_details(
    db: AsyncSession,
    supplier_id: UUID,
    mine_name: Optional[str] = None,
    mining_method: Optional[str] = None,
    extraction_volume: Optional[float] = None,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    active_period_from=None,
    active_period_to=None,
) -> None:
    """
    MF 섹션 3 — supplier_miner_details UPSERT. flush까지만(커밋은 service).

    PostGIS 좌표 순서: ST_MakePoint(lng, lat) — PostGIS는 X(경도) 먼저.
    입력 lat/lng는 Leaflet 표기법(lat 먼저)이므로 여기서 swap한다.
    lat=None이거나 lng=None이면 mine_coordinates를 NULL로 유지.
    """
    existing = await db.execute(
        select(SupplierMinerDetail).where(SupplierMinerDetail.supplier_id == supplier_id)
    )
    row = existing.scalars().first()

    # PostGIS: X=경도(lng), Y=위도(lat) — ST_MakePoint(lng, lat)
    coords_sql: Optional[str] = None
    coords_params: dict = {}
    if lat is not None and lng is not None:
        coords_sql = "ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)"
        coords_params = {"lng": lng, "lat": lat}

    if row is None:
        if coords_sql:
            stmt = text(f"""
                INSERT INTO supplier_miner_details
                    (supplier_id, mine_name, mining_method, extraction_volume,
                     mine_coordinates, active_period_from, active_period_to)
                VALUES
                    (:supplier_id, :mine_name, :mining_method, :extraction_volume,
                     {coords_sql}, :active_period_from, :active_period_to)
            """)
        else:
            stmt = text("""
                INSERT INTO supplier_miner_details
                    (supplier_id, mine_name, mining_method, extraction_volume,
                     mine_coordinates, active_period_from, active_period_to)
                VALUES
                    (:supplier_id, :mine_name, :mining_method, :extraction_volume,
                     NULL, :active_period_from, :active_period_to)
            """)
        await db.execute(stmt, {
            "supplier_id": str(supplier_id),
            "mine_name": mine_name,
            "mining_method": mining_method,
            "extraction_volume": extraction_volume,
            "active_period_from": active_period_from,
            "active_period_to": active_period_to,
            **coords_params,
        })
    else:
        if coords_sql:
            stmt = text(f"""
                UPDATE supplier_miner_details SET
                    mine_name          = :mine_name,
                    mining_method      = :mining_method,
                    extraction_volume  = :extraction_volume,
                    mine_coordinates   = {coords_sql},
                    active_period_from = :active_period_from,
                    active_period_to   = :active_period_to
                WHERE supplier_id = :supplier_id
            """)
        else:
            stmt = text("""
                UPDATE supplier_miner_details SET
                    mine_name          = :mine_name,
                    mining_method      = :mining_method,
                    extraction_volume  = :extraction_volume,
                    mine_coordinates   = NULL,
                    active_period_from = :active_period_from,
                    active_period_to   = :active_period_to
                WHERE supplier_id = :supplier_id
            """)
        await db.execute(stmt, {
            "supplier_id": str(supplier_id),
            "mine_name": mine_name,
            "mining_method": mining_method,
            "extraction_volume": extraction_volume,
            "active_period_from": active_period_from,
            "active_period_to": active_period_to,
            **coords_params,
        })
    await db.flush()