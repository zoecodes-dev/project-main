"""
domains/supplychain/router.py  (담당: 팀원 D · 영수)

SupplyChain Domain REST 엔드포인트. 스펙 5-1 엔드포인트 목록 기준.
import 경로를 package 기준으로 수정 (flat → backend.* 패키지).

[W4 변경]
  - GET /supply-chain/tree          : N차 공급망 재귀 CTE 트리 조회
  - GET /supply-chain/alternatives  : 특정 부품 대체 공급사 풀 조회
  - GET /supply-chain/geo-risks     : 지정학 공간 리스크 조회
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
    product_id: Optional[UUID] = None,
    bom_version_id: Optional[UUID] = None,
    service: SupplyChainService = Depends(get_supply_chain_service),
):
    """N차 공급망 트리 (재귀 CTE)."""
    # 프론트 트리 렌더용 평면 리스트(hop_level, parent-child 포함) 반환
    # service.get_supply_tree는 product_id(str)만 인자로 받음
    return await service.get_supply_tree(
        product_id=str(product_id) if product_id else ""
    )


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
