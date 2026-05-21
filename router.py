from fastapi import APIRouter, Depends
from typing import List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db_session

from repository import SupplyChainRepository
from service import SupplyChainService

router = APIRouter(prefix="/supply-chain", tags=["Supply Chain Domain"])

def get_supply_chain_service(session: AsyncSession = Depends(get_db_session)) -> SupplyChainService:
    """
    요청(Request)마다 새로운 Repository와 Service 인스턴스를 생성하여 주입함.
    """
    repository = SupplyChainRepository(session)
    return SupplyChainService(repository)

@router.get("/{root_supplier_id}/tree", response_model=List[Dict[str, Any]])
async def get_supply_tree(
    root_supplier_id: str,
    service: SupplyChainService = Depends(get_supply_chain_service)
):
    """
    특정 공급사를 기점으로 하위 N차 공급망 트리를 조회함.
    """
    return await service.repository.get_n_tier_supply_chain(root_supplier_id)

@router.post("/geo-audit/run", response_model=List[Dict[str, Any]])
async def run_geo_audit(
    service: SupplyChainService = Depends(get_supply_chain_service)
):
    """
    협력사 공장 위치 기반 Geo Audit을 수행하고 위험 지역 판별 시 이벤트를 발행함.
    """
    return await service.execute_geo_audit()