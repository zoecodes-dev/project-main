"""
domains/supplychain/service.py  (담당: 팀원 D · 영수)

공급망 비즈니스 로직. 이벤트 발행은 반드시 infrastructure 계층 경유.
직접 redis import 금지 (수정됨) → event_bus.publish() + queue.enqueue() 사용.
도메인 간 직접 import 금지 → events/types.py의 dataclass로만 통신.
"""
from dataclasses import asdict
from typing import Any, Dict, List

from sqlalchemy.ext.asyncio import AsyncSession

from backend.events.types import GeoRiskDetectedEvent
from backend.infrastructure.event_bus import publish
from backend.infrastructure.trace import trace_node
from backend.domains.supplychain.repository import SupplyChainRepository


class SupplyChainCycleError(ValueError):
    """순환 참조를 만드는 공급망 관계 등록 시도."""


class SupplyRatioExceededError(ValueError):
    """공급 비율 합이 100을 초과."""


class SupplyChainService:
    def __init__(self, repository: SupplyChainRepository):
        self.repository = repository

    # ---------- 공급망 그래프 ----------
    async def get_supply_tree(self, product_id: str) -> List[Dict[str, Any]]:
        """product_id 기준 N차 공급망 트리 조회."""
        return await self.repository.get_n_tier_supply_chain(product_id)

    async def register_relation(
        self,
        bom_version_id: str,
        parent_supplier_id: str | None,
        child_supplier_id: str,
        part_id: str,
    ) -> Dict[str, Any]:
        """
        공급망 관계 등록. 스펙 5-1 유효성 검증:
        1. parent == child 면 거부
        2. 순환 참조 사전 검사 (재귀 CTE)
        """
        if parent_supplier_id is not None and parent_supplier_id == child_supplier_id:
            raise ValueError("parent_supplier_id와 child_supplier_id가 동일할 수 없습니다.")

        if parent_supplier_id is not None:
            if await self.repository.would_create_cycle(
                parent_supplier_id, child_supplier_id
            ):
                raise SupplyChainCycleError(
                    "해당 관계는 공급망에 순환 참조를 발생시킵니다."
                )

        return await self.repository.create_supply_relation(
            bom_version_id, parent_supplier_id, child_supplier_id, part_id
        )

    async def get_alternatives(
        self, product_id: str, part_id: str
    ) -> List[Dict[str, Any]]:
        return await self.repository.get_alternatives(product_id, part_id)

    async def get_geo_risks(self, db: AsyncSession) -> Dict[str, Any]:
        """
        조회 전용 인터페이스: 이벤트를 발행하지 않고 지정학 리스크 결과를 반환합니다.
        """
        xinjiang_risks = await self.repository.check_geo_audit_risk_zone()
        mismatch_risks = await self.repository.check_coordinate_authenticity(db)
        eudr_risks = await self.repository.check_eudr_deforestation(db)
        
        return {
            "xinjiang_adjacent": xinjiang_risks,
            "country_mismatch": mismatch_risks,
            "eudr_deforestation": eudr_risks
        }

    # ---------- Geo Audit ----------
    @trace_node("geo_audit_execute", "agent")
    async def execute_geo_audit(self, db: AsyncSession, batch_id: str | None = None) -> List[Dict[str, Any]]:
        """
        공장 위치 기반 Geo Audit 수행. 고위험 지역(신장 등) 판정 시
        GeoRiskDetected 이벤트를 발행한다.
        db 인자는 @trace_node가 audit_trail 기록에 사용.
        """
        audit_results = await self.repository.check_geo_audit_risk_zone()
        mismatch_results = await self.repository.check_coordinate_authenticity(db)
        eudr_results = await self.repository.check_eudr_deforestation(db)

        detected_risks: List[Dict[str, Any]] = []
        for result in audit_results:
            if result.get("is_in_risk_zone"):
                event = GeoRiskDetectedEvent(
                    batch_id=batch_id,
                    factory_id=result["factory_id"],
                    risk_type="xinjiang",
                    supplier_id=result["supplier_id"],
                    company_name=result["company_name"],
                    coordinates=result["coordinates"],
                )
                await self._publish_geo_risk(event)
                detected_risks.append(asdict(event))

        for result in mismatch_results:
            if not result.get("country_match"):
                event = GeoRiskDetectedEvent(
                    batch_id=batch_id,
                    factory_id=result["factory_id"],
                    risk_type="country_mismatch",
                    supplier_id=result["supplier_id"],
                    company_name=result["company_name"],
                    coordinates=result["coordinates"],
                )
                await self._publish_geo_risk(event)
                detected_risks.append(asdict(event))

        for result in eudr_results:
            if result.get("is_deforested"):
                event = GeoRiskDetectedEvent(
                    batch_id=batch_id,
                    factory_id=result["factory_id"],
                    risk_type="eudr_deforestation",
                    supplier_id=result["supplier_id"],
                    company_name=result["company_name"],
                    coordinates=result["coordinates"],
                )
                await self._publish_geo_risk(event)
                detected_risks.append(asdict(event))

        return detected_risks

    async def _publish_geo_risk(self, event: GeoRiskDetectedEvent) -> None:
        """
        GeoRiskDetected 이벤트 발행 (후속 처리는 risk_worker가 통합 처리)
        """
        payload = asdict(event)
        await publish(event.event_name, payload)
