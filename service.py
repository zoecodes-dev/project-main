from typing import List, Dict, Any
from repository import SupplyChainRepository
from redis_client import get_redis_pool

class SupplyChainService:
    def __init__(self, repository: SupplyChainRepository):
        self.repository = repository

    async def execute_geo_audit(self) -> List[Dict[str, Any]]:
        """
        협력사 공장의 위치 데이터를 기반으로 Geo Audit을 수행하고,
        고위험 지역(신장 위구르 등)에 포함될 경우 GeoRiskDetected 이벤트를 발행함.
        """
        audit_results = await self.repository.check_geo_audit_risk_zone()
        
        detected_risks = []
        for result in audit_results:
            # Repository 조회 결과에서 위험 지역 포함 여부 확인
            if result.get("is_in_risk_zone"):
                risk_event = {
                    "event_type": "GeoRiskDetected",
                    "supplier_id": str(result["supplier_id"]),
                    "company_name": result["company_name"],
                    "factory_id": str(result["factory_id"]),
                    "coordinates": result["coordinates"]
                }
                await self._publish_event(risk_event)
                detected_risks.append(risk_event)
        
        return detected_risks

    async def _publish_event(self, event_payload: dict) -> None:
        """
        Redis/arq 작업 큐를 통해 이벤트를 비동기 발행(Publish)함.
        """
        redis = await get_redis_pool()
        await redis.enqueue_job("process_geo_risk_event", event_payload)
        print(f"[EVENT ENQUEUED] {event_payload['event_type']} -> Redis Queue")