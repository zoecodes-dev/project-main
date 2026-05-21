import os
import asyncio
from arq.connections import RedisSettings

async def process_geo_risk_event(ctx, event_payload: dict):
    """
    Redis 큐에서 GeoRiskDetected 이벤트를 소비하여 후속 비즈니스 로직을 처리함.
    """
    print(f"[WORKER CONSUMED] Risk Event Received for Supplier: {event_payload['supplier_id']}")
    await asyncio.sleep(1) # 위험도 평가 로직 시뮬레이션
    return True

class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(os.getenv("REDIS_URL", "redis://redis:6379/0"))
    functions = [process_geo_risk_event]