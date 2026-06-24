"""
agents/geo_audit.py  (담당: 팀원 D · 영수)

Geo Audit Agent. 공장·광산 좌표 진위성 검사 + 고위험 지역 판정.
스펙 5-2 기준. W1은 시그니처 + 깡통, 실제 PostGIS 공간 쿼리는 W3.

W5 변경 (R3·R5):
  R3: geo_result.risk_detected = bool, geo_result.risk_flags = [risk_type, ...] 추가
      compliance.py가 geo.get("risk_flags", [])로 읽으므로 키 이름 일치 필수.
  R5: geo_audit_results 테이블에 배치별 판정 결과 저장.
"""
import json
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.state import BatchState
from backend.domains.supplychain.repository import SupplyChainRepository
from backend.domains.supplychain.service import SupplyChainService


async def geo_audit_node(state: BatchState, db: AsyncSession) -> BatchState:
    """
    배치의 모든 공장 좌표를 검사하고 결과를 state에 기록.
    고위험 발견 시 GeoRiskDetected 발행은 service 계층에 위임.
    """
    batch_id = state.get("batch_id")

    repo = SupplyChainRepository(db)
    service = SupplyChainService(repo)

    # 실제 좌표 루프 및 고위험 지역 검사 수행 (리스크 큐 적재 포함)
    detected_risks = await service.execute_geo_audit(db, batch_id=batch_id)

    # R3: compliance가 geo.get("risk_flags", [])로 읽는 키를 채운다.
    #     risk_type 값 예: "xinjiang" | "country_mismatch" | "eudr_deforestation"
    risk_flags = list({r["risk_type"] for r in detected_risks if r.get("risk_type")})
    risk_detected = bool(detected_risks)

    error_reason = state.get("error_reason", None)
    hitl_required = state.get("hitl_required", False)

    if detected_risks:
        error_reason = "geographical_risk"
        hitl_required = True

    # R5: geo_audit_results DB 저장 (배치별 최신 결과 upsert)
    if batch_id:
        await db.execute(
            text("""
                INSERT INTO geo_audit_results
                    (batch_id, risk_detected, risk_flags, detected_risks, created_at)
                VALUES
                    (:batch_id, :risk_detected,
                     CAST(:risk_flags AS jsonb),
                     CAST(:detected_risks AS jsonb),
                     :created_at)
                ON CONFLICT (batch_id)
                DO UPDATE SET
                    risk_detected  = EXCLUDED.risk_detected,
                    risk_flags     = EXCLUDED.risk_flags,
                    detected_risks = EXCLUDED.detected_risks
            """),
            {
                "batch_id": batch_id,
                "risk_detected": risk_detected,
                "risk_flags": json.dumps(risk_flags),
                "detected_risks": json.dumps(detected_risks, default=str),
                "created_at": datetime.now(timezone.utc),
            },
        )
        await db.commit()

    return {
        **state,
        "geo_result": {
            "risk_detected": risk_detected,
            "risk_flags": risk_flags,
        },
        "error_reason": error_reason,
        "hitl_required": hitl_required,
        "current_stage": "stage_geo",
    }
