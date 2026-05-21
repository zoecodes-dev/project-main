"""
workers/geo_risk_worker.py  (담당: 팀원 D · 영수)

risk_queue 컨슈머. GeoRiskDetected 이벤트를 소비해 후속 리스크 처리.
(기존 flat worker.py에서 이동 + Idempotency 골격 추가)

W1 범위: 깡통 컨슈머 (실제 리스크 평가 로직은 W3).
"""
import os

from arq.connections import RedisSettings

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

# 이미 처리한 이벤트 추적 (W1 인메모리; W2에서 Redis SET 기반으로 교체)
_processed_keys: set[str] = set()


async def process_geo_risk_event(ctx, event_payload: dict) -> bool:
    """
    risk_queue에서 GeoRiskDetected 이벤트를 소비.
    Idempotency: 동일 (factory_id, event_name) 조합은 한 번만 처리.
    """
    idempotency_key = (
        f"{event_payload.get('event_name')}:{event_payload.get('factory_id')}"
    )
    if idempotency_key in _processed_keys:
        print(f"[WORKER SKIP] 이미 처리된 이벤트: {idempotency_key}")
        return True

    print(
        f"[WORKER CONSUMED] Geo Risk for supplier="
        f"{event_payload.get('supplier_id')} factory={event_payload.get('factory_id')}"
    )
    # TODO(W3): 리스크 점수 계산 → RiskDetected/RiskEscalated 분기
    _processed_keys.add(idempotency_key)
    return True


class WorkerSettings:
    """ARQ 워커 설정. risk_queue 전용 (한 워커 = 한 Queue, 스펙 5-3)."""
    redis_settings = RedisSettings.from_dsn(REDIS_URL)
    queue_name = "risk_queue"
    functions = [process_geo_risk_event]
    max_tries = 3  # 지수 백오프 재시도 (스펙 1-3)
