"""
domains/supplychain/router.py  (담당: 팀원 D · 영수)

SupplyChain Domain REST 엔드포인트. 스펙 5-1 엔드포인트 목록 기준.
import 경로를 package 기준으로 수정 (flat → backend.* 패키지).
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.database import get_db
from domains.supplychain.repository import SupplyChainRepository
from domains.supplychain.service import SupplyChainService

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


@router.get("/{product_id}/tree", response_model=List[Dict[str, Any]])
async def get_supply_tree(
    product_id: str,
    service: SupplyChainService = Depends(get_supply_chain_service),
):
    """product_id 기준 N차 공급망 트리 (재귀 CTE)."""
    return await service.get_supply_tree(product_id)


@router.get(
    "/{product_id}/alternatives/{part_id}",
    response_model=List[Dict[str, Any]],
)
async def get_alternatives(
    product_id: str,
    part_id: str,
    service: SupplyChainService = Depends(get_supply_chain_service),
):
    """동일 부품의 대체 공급망 탐색."""
    return await service.get_alternatives(product_id, part_id)


@router.post("/geo-audit/run", response_model=List[Dict[str, Any]])
async def run_geo_audit(
    session: AsyncSession = Depends(get_db),
    service: SupplyChainService = Depends(get_supply_chain_service),
):
    """협력사 공장 위치 기반 Geo Audit 수행, 위험 판정 시 GeoRiskDetected 발행."""
    return await service.execute_geo_audit(session)
