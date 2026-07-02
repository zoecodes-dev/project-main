"""
domains/supplychain/router.py  (담당: 팀원 D · 영수)

SupplyChain Domain REST 엔드포인트. 스펙 5-1 엔드포인트 목록 기준.
import 경로를 package 기준으로 수정 (flat → backend.* 패키지).

[W4 변경]
  - GET /supply-chain/tree          : N차 공급망 재귀 CTE 트리 조회
  - GET /supply-chain/alternatives  : 특정 부품 대체 공급사 풀 조회
  - GET /supply-chain/geo-risks     : 지정학 공간 리스크 조회

[ADR 축 분리 신설]
  - GET /supply-chain/by-bom-depth/{n} : 부품 tier(bom_depth, 0-base) 기준 필터
  - GET /supply-chain/by-hop/{n}       : 공급망 차수(hop_level, 경로 순번) 기준 필터
"""
import io
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domains.supplychain.repository import SupplyChainRepository
from backend.domains.supplychain.service import SupplyChainService
from backend.domains.submission.service import create_and_request_submission
from backend.infrastructure.osm_geocode import geocode_candidates, reverse_geocode_osm
from backend.infrastructure.auth import CurrentUser, get_current_user
from backend.infrastructure.database import get_db, AsyncSessionLocal
from backend.infrastructure.trace import trace_tool

router = APIRouter(prefix="/supply-chain", tags=["Supply Chain Domain"])
product_supply_chain_router = APIRouter(prefix="/products", tags=["Supply Chain Domain"])


def get_supply_chain_service(
    session: AsyncSession = Depends(get_db),
) -> SupplyChainService:
    """요청마다 Repository + Service 인스턴스를 생성해 주입."""
    repository = SupplyChainRepository(session)
    return SupplyChainService(repository)


class SupplyRelationCreate(BaseModel):
    bom_version_id: str
    parent_supplier_id: Optional[str] = None
    child_supplier_id: str
    part_id: str


class SupplierCorrectionRequest(BaseModel):
    sender_supplier_id: str
    target_supplier_id: str
    reason: str
    due_date: str
    required_documents: list[str]


class SourceChangeDeclaration(BaseModel):
    bom_version_id: str
    parent_supplier_id: str
    new_child_supplier_id: str
    part_id: str
    reason: str


@router.post("", response_model=Dict[str, Any])
@trace_tool("create_supply_relation")
async def create_supply_relation(
    body: SupplyRelationCreate,
    service: SupplyChainService = Depends(get_supply_chain_service),
):
    """공급망 parent-child 관계 등록 (순환 참조 사전 검증 포함)."""
    return await service.register_relation(
        bom_version_id=body.bom_version_id,
        parent_supplier_id=body.parent_supplier_id,
        child_supplier_id=body.child_supplier_id,
        part_id=body.part_id,
    )


@router.get("/tree")
@trace_tool("get_supply_tree")
async def get_supply_chain_tree_endpoint(
    product_id: UUID,
    service: SupplyChainService = Depends(get_supply_chain_service),
):
    """N차 공급망 트리 (재귀 CTE)."""
    # 프론트 트리 렌더용 평면 리스트(hop_level, parent-child 포함) 반환
    # service.get_supply_tree는 product_id(str)만 인자로 받음
    return await service.get_supply_tree(
        product_id=str(product_id)
    )


@router.get("/by-bom-depth/{n}")
@trace_tool("get_by_bom_depth")
async def get_by_bom_depth_endpoint(
    n: int,
    service: SupplyChainService = Depends(get_supply_chain_service),
):
    """부품 tier(bom_depth, 0-base) 기준 공급망 노드 필터.

    ADR 분리축: '부품 계층'(Pack=0 … 광산=6) 단위 횡단 조회. hop(차수)과 독립.
    """
    return await service.get_by_bom_depth(n)


@router.get("/by-hop/{n}")
@trace_tool("get_by_hop")
async def get_by_hop_endpoint(
    n: int,
    service: SupplyChainService = Depends(get_supply_chain_service),
):
    """공급망 차수(hop_level, 원청 0 기준 경로 순번) 기준 노드 필터.

    ADR 분리축: '공급망 차수' 단위 횡단 조회. bom_depth(부품 tier)와 독립.
    """
    return await service.get_by_hop(n)


@router.get("/gaps")
@trace_tool("get_supply_chain_gaps")
async def get_supply_chain_gaps_endpoint(
    product_id: UUID,
    service: SupplyChainService = Depends(get_supply_chain_service),
):
    """
    C2 맵 gap 계산 API.

    제품 공급망 내 각 협력사 노드별로 적용 규제 대비 미보유 필수 필드 목록 반환.
    응답 예시:
      {
        "product_id": "...",
        "nodes": [
          {
            "supplier_id": "...",
            "provider_type": "manufacturer",
            "depth": 0,
            "missing_fields": [
              {"field_name": "carbon_intensity", "regulation_code": "EU_BATTERY_ART7", ...}
            ],
            "gap_count": 1
          }
        ]
      }
    """
    return await service.get_gaps(product_id=str(product_id))


@router.get("/alternatives")
@trace_tool("get_alternatives")
async def get_supply_chain_alternatives_endpoint(
    product_id: UUID,
    part_id: UUID,
    service: SupplyChainService = Depends(get_supply_chain_service),
):
    """동일 부품의 대체 공급망 탐색."""
    # 특정 부품 공급 중단 시 프론트에 대안 협력사 풀 제시
    return await service.get_alternatives(
        product_id=str(product_id),
        part_id=str(part_id)
    )


@router.get("/geo-risks")
@trace_tool("get_geo_risks")
async def get_geo_risks_endpoint(
    session: AsyncSession = Depends(get_db),
    service: SupplyChainService = Depends(get_supply_chain_service),
):
    """지정학 공간 리스크(신장, 위장공장) 노출 목록."""
    # check_geo_audit_risk_zone(신장) + check_coordinate_authenticity(위장공장) 결과 통합 반환
    return await service.get_geo_risks(session)


@router.get("/geocode/search")
@trace_tool("geocode_search")
async def geocode_search_endpoint(
    q: str,
    country: Optional[str] = None,
    limit: int = 5,
):
    """
    [픽커용] 지명/주소 → 후보 목록(동명 지명 해소). 프론트가 지도에 띄워 사용자가 선택.
    - q: 지명/주소 (한글·현지어·영문). country: ISO alpha-2(있으면 그 나라로 한정, 없으면 전세계).
    - 각 후보: {lat, lon, display_name, admin(행정구역), country_code, is_xinjiang}.
    """
    return {"query": q, "candidates": await geocode_candidates(q, country, limit)}


@router.get("/geocode/reverse")
@trace_tool("geocode_reverse")
async def geocode_reverse_endpoint(
    lat: float,
    lon: float,
):
    """
    [픽커용] 확정한 핀 좌표 → {lat, lon, display_name, admin, country_code, is_xinjiang} | null.
    사용자가 지도에서 고른 위치의 국가·행정구역을 역추출해 폼 자동입력(country/region)에 사용.
    """
    return await reverse_geocode_osm(lat, lon)


@router.post("/notifications/correction", response_model=Dict[str, Any])
@trace_tool("request_supplier_correction")
async def request_supplier_correction_endpoint(
    body: SupplierCorrectionRequest,
    service: SupplyChainService = Depends(get_supply_chain_service),
):
    """회사 경계를 넘는 반려/시정요청 통지 발송."""
    return await service.request_supplier_correction(
        sender_id=body.sender_supplier_id,
        target_supplier_id=body.target_supplier_id,
        reason=body.reason,
        due_date=body.due_date,
        required_docs=body.required_documents
    )


@router.post("/declarations/source-change", response_model=Dict[str, Any])
@trace_tool("declare_source_change")
async def declare_source_change_endpoint(
    body: SourceChangeDeclaration,
    service: SupplyChainService = Depends(get_supply_chain_service),
):
    """협력사의 자진 공급원 변경 신고."""
    return await service.declare_source_change(
        bom_version_id=body.bom_version_id,
        parent_supplier_id=body.parent_supplier_id,
        new_child_supplier_id=body.new_child_supplier_id,
        part_id=body.part_id,
        reason=body.reason
    )


# [REVERT-NON-SUPPLIER:BEGIN] STEP3 협력사 '확인'(verify) — supply_chain_map.verification_status 갱신.
#   supplier 외(supplychain) 도메인. 최종 작업 시 이 모델 + 아래 /verify 엔드포인트 주석/삭제.
class VerifySupplierBody(BaseModel):
    bom_version_id: UUID
    supplier_id: UUID
    verified: bool = True


@router.post("/verify", response_model=Dict[str, Any])
@trace_tool("verify_supplier_link")
async def verify_supplier_endpoint(
    body: VerifySupplierBody,
    service: SupplyChainService = Depends(get_supply_chain_service),
):
    """원청이 연결 협력사를 '확인' 처리(verified) 또는 해제(unverified)한다."""
    return await service.set_supplier_verification(
        bom_version_id=str(body.bom_version_id),
        supplier_id=str(body.supplier_id),
        verified=body.verified,
    )
# [REVERT-NON-SUPPLIER:END]


class TriggerDataRequestsBody(BaseModel):
    product_id: UUID
    supplier_ids: Optional[List[UUID]] = None  # None = gap 있는 노드 전체
    requester_user_id: UUID
    actor_id: UUID
    due_date: Optional[datetime] = None


@router.post("/trigger-data-requests", response_model=Dict[str, Any])
@trace_tool("trigger_data_requests_for_gaps")
async def trigger_data_requests_for_gaps_endpoint(
    body: TriggerDataRequestsBody,
    service: SupplyChainService = Depends(get_supply_chain_service),
):
    """
    C3 맵 gap→데이터요청 트리거.

    공급망 맵에서 규제 필수 필드 gap이 있는 노드(협력사)를 대상으로
    submission 도메인의 POST /data-requests를 일괄 호출한다.
    supplier_ids 미지정 시 gap_count > 0인 모든 노드에 요청 생성.
    """
    gaps = await service.get_gaps(product_id=str(body.product_id))
    nodes = gaps.get("nodes", [])

    target_ids = {str(sid) for sid in body.supplier_ids} if body.supplier_ids else None

    created = []
    for node in nodes:
        if node["gap_count"] == 0:
            continue
        if target_ids and node["supplier_id"] not in target_ids:
            continue

        missing_types = ",".join(f["field_name"] for f in node["missing_fields"])
        # gap 조회 세션(service)과 분리된 독립 세션으로 write — 세션 충돌 방지
        async with AsyncSessionLocal() as db:
            data_request = await create_and_request_submission(
                db=db,
                requester_user_id=body.requester_user_id,
                target_supplier_id=UUID(node["supplier_id"]),
                requested_data_type=missing_types,
                due_date=body.due_date,
                actor_id=body.actor_id,
            )
        created.append({
            "request_id":           str(data_request.request_id),
            "supplier_id":          node["supplier_id"],
            "requested_data_type":  missing_types,
            "gap_count":            node["gap_count"],
            "is_root_anchor":       node.get("is_root_anchor", False),
        })

    return {
        "product_id":    str(body.product_id),
        "created_count": len(created),
        "requests":      created,
    }


# ============================================================
# 10.2a  GET /products/{product_id}/supply-chain-map
# ============================================================

@product_supply_chain_router.get("/{product_id}/supply-chain-map")
async def get_supply_chain_map_endpoint(
    product_id: UUID,
    bom_version_id: Optional[str] = None,
    period_from: Optional[str] = None,
    period_to: Optional[str] = None,
    factory_id: Optional[str] = None,
    po_number: Optional[str] = None,
    current_user: CurrentUser = Depends(get_current_user),
    service: SupplyChainService = Depends(get_supply_chain_service),
):
    """
    10.2a: 제품 공급망 맵 조회.
    응답: supply_chain_map / supply_chain_ratios / suppliers / supplier_factories
    products.tenant_id → bom_versions 경로로 tenant 격리.
    """
    if current_user.tenant_id is None:
        raise HTTPException(status_code=403, detail="테넌트 정보가 없습니다.")
    return await service.get_supply_chain_map(
        product_id=str(product_id),
        tenant_id=str(current_user.tenant_id),
        bom_version_id=bom_version_id,
        period_from=period_from,
        period_to=period_to,
        factory_id=factory_id,
        po_number=po_number,
    )


# ============================================================
# P4  GET /products/{product_id}/supply-chain-map/validation-summary
#   최종 검증 판정(ready_for_final) + 공급망 요약 롤업(협력사 수/최대 차수/미보유 필드).
#   원청이 '최종 검증' 전에 확인 + 고객사 제출용 엑셀 다운로드 게이트.
# ============================================================

@product_supply_chain_router.get("/{product_id}/supply-chain-map/validation-summary")
async def get_validation_summary_endpoint(
    product_id: UUID,
    bom_version_id: Optional[str] = None,
    current_user: CurrentUser = Depends(get_current_user),
    service: SupplyChainService = Depends(get_supply_chain_service),
):
    """공급망 최종 검증 요약. products.tenant_id 경로로 tenant 격리."""
    if current_user.tenant_id is None:
        raise HTTPException(status_code=403, detail="테넌트 정보가 없습니다.")
    return await service.get_validation_summary(
        product_id=str(product_id),
        tenant_id=str(current_user.tenant_id),
        bom_version_id=bom_version_id,
    )


# ============================================================
# P4  GET /products/{product_id}/supply-chain-map/export
#   고객사 제출용 공급망 엑셀(xlsx) 서버 생성 다운로드.
# ============================================================

@product_supply_chain_router.get("/{product_id}/supply-chain-map/export")
async def export_supply_chain_map_endpoint(
    product_id: UUID,
    bom_version_id: Optional[str] = None,
    current_user: CurrentUser = Depends(get_current_user),
    service: SupplyChainService = Depends(get_supply_chain_service),
):
    """공급망 맵 엑셀 다운로드(서버 생성). products.tenant_id 경로로 tenant 격리."""
    if current_user.tenant_id is None:
        raise HTTPException(status_code=403, detail="테넌트 정보가 없습니다.")
    content = await service.export_supply_chain_xlsx(
        product_id=str(product_id),
        tenant_id=str(current_user.tenant_id),
        bom_version_id=bom_version_id,
    )
    filename = f"supply_chain_{product_id}.xlsx"
    return StreamingResponse(
        io.BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ============================================================
# 10.2b  POST /supply-chain/maps/{map_id}/confirm
# ============================================================

class ConfirmMapRequest(BaseModel):
    confirmed: bool


@router.post("/maps/{map_id}/confirm")
async def confirm_supply_chain_map_endpoint(
    map_id: UUID,
    body: ConfirmMapRequest,
    current_user: CurrentUser = Depends(get_current_user),
    service: SupplyChainService = Depends(get_supply_chain_service),
):
    """
    10.2b: 공급망 맵 확인(confirm). link_status → supplychain_confirmed.
    products.tenant_id 경로로 tenant 격리.
    """
    if not body.confirmed:
        raise HTTPException(status_code=400, detail="confirmed must be true")
    if current_user.tenant_id is None:
        raise HTTPException(status_code=403, detail="테넌트 정보가 없습니다.")
    result = await service.confirm_supply_chain_map(
        map_id=str(map_id),
        tenant_id=str(current_user.tenant_id),
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Supply chain map not found")
    return result


# ============================================================
# Pool 확정 — POST /supply-chain/maps/{map_id}/pool/confirm
#   풀 = 맵 그 자체. "확정"은 그 맵의 Tier-1(hop_level=1) 엣지 link_status 전이.
#   supplier_ids 미지정 시 맵의 모든 Tier-1 엣지 확정.
# ============================================================

class ConfirmPoolRequest(BaseModel):
    supplier_ids: Optional[List[str]] = None


@router.post("/maps/{map_id}/pool/confirm")
async def confirm_pool_endpoint(
    map_id: UUID,
    body: ConfirmPoolRequest,
    current_user: CurrentUser = Depends(get_current_user),
    service: SupplyChainService = Depends(get_supply_chain_service),
):
    """1차 협력사 풀 확정. 선택된 Tier-1 협력사(없으면 전체) 엣지를 confirmed 로 전이."""
    if current_user.tenant_id is None:
        raise HTTPException(status_code=403, detail="테넌트 정보가 없습니다.")
    return await service.confirm_pool(
        map_id=str(map_id),
        tenant_id=str(current_user.tenant_id),
        supplier_ids=body.supplier_ids,
    )


# [REVERT-NON-SUPPLIER:BEGIN] supplier 외(supplychain) — 공급망 맵 헤더(맵 그 자체) 관리 API.
class MapStatusUpdate(BaseModel):
    status: str  # building / completed


@router.get("/maps")
async def list_maps_endpoint(
    current_user: CurrentUser = Depends(get_current_user),
    service: SupplyChainService = Depends(get_supply_chain_service),
):
    """내 테넌트의 공급망 맵 목록(map_id 단위 + 엣지 수·상태)."""
    if current_user.tenant_id is None:
        raise HTTPException(status_code=403, detail="테넌트 정보가 없습니다.")
    return await service.list_maps(str(current_user.tenant_id))


@router.get("/maps/{map_id}")
async def get_map_endpoint(
    map_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: SupplyChainService = Depends(get_supply_chain_service),
):
    """공급망 맵 단건(map_id). 내 테넌트 소유만(아니면 404)."""
    if current_user.tenant_id is None:
        raise HTTPException(status_code=403, detail="테넌트 정보가 없습니다.")
    result = await service.get_map(str(map_id), str(current_user.tenant_id))
    if result is None:
        raise HTTPException(status_code=404, detail="Supply chain map not found")
    return result


@router.patch("/maps/{map_id}")
async def update_map_status_endpoint(
    map_id: UUID,
    body: MapStatusUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    service: SupplyChainService = Depends(get_supply_chain_service),
):
    """공급망 맵 완료/전송 상태 변경(building/completed). 내 테넌트 소유만."""
    if body.status not in ("building", "completed"):
        raise HTTPException(status_code=422, detail="status must be building or completed")
    if current_user.tenant_id is None:
        raise HTTPException(status_code=403, detail="테넌트 정보가 없습니다.")
    result = await service.set_map_status(
        str(map_id), body.status, str(current_user.user_id), str(current_user.tenant_id)
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Supply chain map not found")
    return result
# [REVERT-NON-SUPPLIER:END]


@router.get("/current-supply-source")
async def get_current_supply_source(
    bom_version_id: str,
    part_id: str,
    parent_supplier_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    자가신고 폼 '기존 공급사' 조회 — bom_version_id·part_id·parent_supplier_id 기준으로
    supply_chain_map에서 현재 child 공급사 정보를 반환한다.
    """
    from sqlalchemy import text
    result = await db.execute(
        text("""
            SELECT
                s.company_name,
                s.country,
                p.part_name,
                p.material_type,
                sc.email AS contact_email
            FROM supply_chain_map scm
            JOIN suppliers s ON s.supplier_id = scm.child_supplier_id
            JOIN parts p ON p.part_id = scm.part_id
            LEFT JOIN supplier_contacts sc
                   ON sc.supplier_id = scm.child_supplier_id AND sc.is_primary = TRUE
            WHERE scm.bom_version_id = :bom_version_id
              AND scm.part_id        = :part_id
              AND scm.parent_supplier_id = :parent_supplier_id
            ORDER BY scm.created_at DESC
            LIMIT 1
        """),
        {
            "bom_version_id": bom_version_id,
            "part_id": part_id,
            "parent_supplier_id": parent_supplier_id,
        },
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="현재 공급원 정보를 찾을 수 없습니다.")
    return {
        "name": row["company_name"] or "",
        "country": row["country"] or "",
        "material": row["part_name"] or row["material_type"] or "",
        "contact": row["contact_email"] or "",
    }
