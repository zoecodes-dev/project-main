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
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domains.supplychain.repository import SupplyChainRepository
from backend.domains.supplychain.service import SupplyChainService
from backend.infrastructure.database import get_db
from backend.infrastructure.trace import trace_tool

router = APIRouter(prefix="/supply-chain", tags=["Supply Chain Domain"])


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
            "supplier_type": "manufacturer",
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
