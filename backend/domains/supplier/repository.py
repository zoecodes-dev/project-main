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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.domains.supplier.models import (
    Supplier,
    SupplierMinerDetail,
    SupplierRiskProfile,
    SupplierAuditRecord,
    SupplierOnboarding,
    SupplierFactory,
    SupplierContact,
    SupplierManufacturerDetail,
    FactoryCarbonDeclaration,
    MasterFormCompany,
    MasterFormFactory,
    MasterFormContact,
    MasterFormManufacturing,
)

async def create_supplier(db: AsyncSession, supplier_data: dict) -> Supplier:
    """협력사 INSERT. flush까지만(커밋은 service)."""
    supplier = Supplier(**supplier_data)
    db.add(supplier)
    await db.flush()
    return supplier

async def update_supplier_fields(db: AsyncSession, supplier_id: UUID, fields: dict) -> None:
    """협력사 기본정보 부분 업데이트. flush까지만(커밋은 service)."""
    if not fields:
        return
    await db.execute(
        update(Supplier).where(Supplier.supplier_id == supplier_id).values(**fields)
    )
    await db.flush()


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

# 원청(자기 회사) 제외 조건 — 공급망 루트(hop_level=0)에 오는 협력사는 원청이므로 협력사 목록에서 뺀다.
_EXCLUDE_OEM_ROOT = text(
    "suppliers.supplier_id NOT IN "
    "(SELECT child_supplier_id FROM supply_chain_map "
    "WHERE hop_level = 0 AND child_supplier_id IS NOT NULL)"
)


async def get_suppliers(
    db: AsyncSession,
    status: Optional[str] = None,
    risk_level: Optional[str] = None,
    page: int = 1,
    size: int = 20,
    tenant_id: Optional[UUID] = None,
) -> List[Supplier]:
    """목록 조회(필터 + 페이지네이션). 기본값 None 인자는 Optional로 명시.

    원청(자기 회사)은 supply_chain_map hop_level=0(공급망 루트)으로 존재하므로 협력사 목록에서 제외한다.
    (협력사 = 우리를 제외한 공급사. KIRA Energy Solutions 같은 자기 자신이 목록에 뜨지 않게.)
    """
    stmt = select(Supplier).where(_EXCLUDE_OEM_ROOT)
    if tenant_id is not None:
        stmt = stmt.where(Supplier.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(Supplier.status == status)
    if risk_level:
        stmt = stmt.where(Supplier.risk_level == risk_level)

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
    tenant_id: Optional[UUID] = None,
) -> int:
    """목록 전체 건수(필터 적용, 페이지 무관). X-Total-Count 헤더용(§0.6). 원청(hop0) 제외."""
    stmt = select(func.count()).select_from(Supplier).where(_EXCLUDE_OEM_ROOT)
    if tenant_id is not None:
        stmt = stmt.where(Supplier.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(Supplier.status == status)
    if risk_level:
        stmt = stmt.where(Supplier.risk_level == risk_level)
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
 
 
async def upsert_manufacturer_fields(db: AsyncSession, supplier_id: UUID, fields: dict) -> None:
    """탄소발자국 등 manufacturer_details 부분 갱신(공급사당 1행). 행이 없으면 INSERT.
    제공된 필드만 갱신해 다른 컬럼(공정·capacity)은 보존. flush만 — 커밋은 service."""
    if not fields:
        return
    res = await db.execute(
        update(SupplierManufacturerDetail)
        .where(SupplierManufacturerDetail.supplier_id == supplier_id)
        .values(**fields)
    )
    if res.rowcount == 0:
        db.add(SupplierManufacturerDetail(supplier_id=supplier_id, **fields))
    await db.flush()


async def set_self_reported_risk_level(
    db: AsyncSession,
    supplier_id: UUID,
    level: str,
) -> None:
    """협력사 실사 자가진단 결과 갱신(supplier_risk_profiles.self_reported_risk_level).
    프로필 row는 초대 시점에 생성돼 존재하므로 UPDATE. flush만 — 커밋은 service."""
    await db.execute(
        update(SupplierRiskProfile)
        .where(SupplierRiskProfile.supplier_id == supplier_id)
        .values(self_reported_risk_level=level)
    )
    await db.flush()


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
async def get_audit_records(
    db: AsyncSession, supplier_id: UUID
) -> List[SupplierAuditRecord]:
    """Reliability 탭 — 실사(Due Diligence) 기록. 실사일 최신순."""
    stmt = (
        select(SupplierAuditRecord)
        .where(SupplierAuditRecord.supplier_id == supplier_id)
        .order_by(SupplierAuditRecord.audit_date.desc())
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


async def mark_consent_agreed(db: AsyncSession, supplier_id: UUID, signed_at) -> None:
    """회원가입 제출 — supplier_onboarding 동의 전이(consent_status='consent_agreed').
    온보딩 row 는 초대 시점(create_supplier_and_invite)에 이미 존재하므로 UPDATE.
    flush까지만 — 커밋은 service 가 단일 트랜잭션으로."""
    await db.execute(
        update(SupplierOnboarding)
        .where(SupplierOnboarding.supplier_id == supplier_id)
        .values(consent_status="consent_agreed", consent_signed_at=signed_at)
    )
    await db.flush()


async def set_supplier_status(db: AsyncSession, supplier_id: UUID, status: str) -> None:
    """suppliers.status 전이(회원가입 제출 → 'supplier_review'). flush까지만(커밋은 service)."""
    await db.execute(
        update(Supplier).where(Supplier.supplier_id == supplier_id).values(status=status)
    )
    await db.flush()


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
            SupplierFactory.factory_manager_name,
            SupplierFactory.factory_manager_role,
            SupplierFactory.factory_manager_phone,
            SupplierFactory.factory_manager_email,
            func.ST_Y(SupplierFactory.location).label("latitude"),
            func.ST_X(SupplierFactory.location).label("longitude"),
        )
        .where(SupplierFactory.supplier_id == supplier_id)
        .order_by(SupplierFactory.created_at.asc())
    )
    result = await db.execute(stmt)
    return [dict(row._mapping) for row in result]


async def get_contacts(db: AsyncSession, supplier_id: UUID) -> List[dict]:
    """
    담당자 연락처 탭 — supplier_contacts 목록.
    대표(is_primary) 우선, 그다음 등록순(created_at) 정렬.
    """
    stmt = (
        select(
            SupplierContact.contact_id,
            SupplierContact.factory_id,
            SupplierContact.name,
            SupplierContact.name_en,
            SupplierContact.role,
            SupplierContact.department,
            SupplierContact.email,
            SupplierContact.phone,
            SupplierContact.mobile,
            SupplierContact.is_primary,
            SupplierContact.language,
        )
        .where(SupplierContact.supplier_id == supplier_id)
        .order_by(SupplierContact.is_primary.desc(), SupplierContact.created_at.asc())
    )
    result = await db.execute(stmt)
    return [dict(row._mapping) for row in result]


async def get_completeness(db: AsyncSession, supplier_id: UUID) -> Optional[dict]:
    """
    입력 완성도 — data_completeness_status(entity_type='supplier') 단건.
    completion_rate / missing_fields(JSONB) / filled·required count 반환. 미집계면 None.
    """
    stmt = text(
        """
        SELECT required_field_count, filled_field_count, completion_rate,
               missing_fields, last_updated_at
        FROM data_completeness_status
        WHERE entity_type = 'supplier' AND entity_id = :sid
        LIMIT 1
        """
    )
    row = (await db.execute(stmt, {"sid": str(supplier_id)})).mappings().first()
    if row is None:
        return None
    data = dict(row)
    mf = data.get("missing_fields")
    if isinstance(mf, str):
        import json
        try:
            data["missing_fields"] = json.loads(mf)
        except Exception:
            data["missing_fields"] = []
    elif mf is None:
        data["missing_fields"] = []
    return data


async def get_carbon_declarations(db: AsyncSession, supplier_id: UUID) -> List[dict]:
    """환경성적서(EU 배터리법 Art7 탄소발자국) — 이 협력사 공장별 factory_carbon_declarations. 유효만료 임박 순."""
    stmt = text(
        """
        SELECT c.declaration_id, c.factory_id, f.factory_name,
               c.carbon_intensity, c.methodology, c.declared_at,
               c.valid_from, c.valid_to, c.source, c.is_active
        FROM factory_carbon_declarations c
        JOIN supplier_factories f ON f.factory_id = c.factory_id
        WHERE f.supplier_id = :sid AND c.is_active = TRUE
        ORDER BY c.valid_to ASC NULLS LAST
        """
    )
    rows = (await db.execute(stmt, {"sid": str(supplier_id)})).mappings().all()
    return [dict(r) for r in rows]


async def get_supplied_items(db: AsyncSession, supplier_id: UUID) -> List[dict]:
    """
    공급 품목 — 이 협력사(child_supplier_id)가 supply_chain_map에서 공급하는 부품(parts) distinct.
    (supply_chain_map·parts는 타 도메인 테이블이지만 읽기 전용 SELECT만; 모델 import 없음.)
    """
    # [공급원 변경 자진신고 배선] bom_version_id 동봉 — 협력사 포털이 declare_source_change
    # 실호출에 쓸 (bom_version, part) 컨텍스트를 여기서 제공한다. 한 부품이 여러 BOM 버전에
    # 걸치면 (part, bom_version)별로 행이 분리된다. version_number는 드롭다운 표시용.
    stmt = text(
        """
        SELECT DISTINCT p.part_id, p.part_code, p.part_name, p.tier_level, p.material_type,
               scm.bom_version_id, bv.version_number AS bom_version_number
        FROM supply_chain_map scm
        JOIN parts p ON p.part_id = scm.part_id
        LEFT JOIN bom_versions bv ON bv.bom_version_id = scm.bom_version_id
        WHERE scm.child_supplier_id = :sid
        ORDER BY p.tier_level NULLS LAST, p.part_code
        """
    )
    rows = (await db.execute(stmt, {"sid": str(supplier_id)})).mappings().all()
    return [dict(r) for r in rows]


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


# ── 섹션 0: 공장 (supplier_factories upsert) ──────────────────────────────
async def write_master_form_factories(
    db: AsyncSession, supplier_id: UUID, factories: List[MasterFormFactory]
) -> List[UUID]:
    """
    섹션 0 공장 — supplier_factories upsert. 입력 순서를 보존한 factory_id
    리스트를 반환한다(섹션 1 탄소선언·섹션 5 교육의 factory_index 연결에 사용).

    replace-all(DELETE+INSERT)이 아니라 upsert인 이유: supply_ratio·supplier_audit_records가
    supplier_factories(factory_id)를 ON DELETE 없이 참조한다. 기존 공장을 통째로
    삭제하면 공급망 편입(supply_ratio)·실사 이력 이후 재제출 시 FK 위반으로 실패한다.
      ① 입력에 factory_id가 있고 이 협력사 소유 → UPDATE(id 보존 → FK 유지)
      ② factory_id가 없거나 남의 것 → INSERT(신규 id 발급)
      ③ 이번 입력에 없는 기존 공장 → 하드 DELETE 시도, FK가 참조 중이면(공급망·실사에
         편입됨) is_active=FALSE 소프트 삭제로 폴백. 소프트 삭제는 활성 목록·신규 공급망
         후보에서 빠지되 기존 supply_ratio 기여도(ART7 탄소 가중평균·누적 기여도 트리에서
         소비)·실사 이력 등 원산지 이력은 보존한다.
    좌표는 GeoPoint(lat/lng) → EWKT 'SRID=4326;POINT(lng lat)'로 변환(PostGIS=lng/lat).
    """
    # 이 협력사의 기존 공장 id — UPDATE 대상 판별 + 미포함 공장 삭제에 사용.
    existing_ids = set(
        (
            await db.execute(
                select(SupplierFactory.factory_id).where(SupplierFactory.supplier_id == supplier_id)
            )
        ).scalars().all()
    )

    factory_ids: List[UUID] = []
    seen_ids: set[UUID] = set()
    for f in factories:
        location = None
        if f.coordinates is not None:
            # PostGIS POINT 순서는 lng/lat. 입력이 lat/lng 명시 필드라 혼선 차단됨.
            location = f"SRID=4326;POINT({f.coordinates.longitude} {f.coordinates.latitude})"
        values = dict(
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
            factory_manager_name=f.factory_manager_name,
            factory_manager_role=f.factory_manager_role,
            factory_manager_phone=f.factory_manager_phone,
            factory_manager_email=f.factory_manager_email,
        )
        if f.factory_id is not None and f.factory_id in existing_ids:
            # ① 기존 공장 — id 보존 UPDATE(supply_ratio FK 유지).
            await db.execute(
                update(SupplierFactory)
                .where(SupplierFactory.factory_id == f.factory_id)
                .values(**values)
            )
            fid = f.factory_id
        else:
            # ② 신규 공장 — INSERT(factory_id가 없거나 이 협력사 소유가 아니면 신규 발급).
            obj = SupplierFactory(supplier_id=supplier_id, **values)
            db.add(obj)
            await db.flush()   # factory_id 확보(이후 섹션에서 FK로 참조)
            fid = obj.factory_id
        factory_ids.append(fid)
        seen_ids.add(fid)

    # ③ 이번 입력에 없는 기존 공장 처리(현재 집합 동기화).
    #    하드 DELETE를 시도하되, 다른 도메인(supply_ratio·audit 등)이 참조 중이면 FK 위반이 난다.
    #    특정 참조 테이블을 이 계층이 알면 도메인 격리가 깨지므로, SAVEPOINT로 삭제를 시도하고
    #    IntegrityError면 그 공장만 롤백 후 is_active=FALSE로 소프트 삭제(원산지 이력 보존).
    stale_ids = existing_ids - seen_ids
    for fid in stale_ids:
        try:
            async with db.begin_nested():   # SAVEPOINT — 실패해도 바깥 트랜잭션은 유지
                await db.execute(delete(SupplierFactory).where(SupplierFactory.factory_id == fid))
        except IntegrityError:
            # 참조 중(공급망 편입·실사 이력 등) → 하드 삭제 불가. 이력 보존용 소프트 삭제.
            await db.execute(
                update(SupplierFactory)
                .where(SupplierFactory.factory_id == fid)
                .values(is_active=False)
            )

    await db.flush()
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