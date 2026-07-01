import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.state import BatchState
from backend.agents.summary_batch import render_key_risks, render_summary
from backend.domains.risk.state_machine import calculate_risk_level


RECOMMENDED_ACTIONS = {
    "fail": "규제 위반 확인 — 배치 반려 및 협력사 시정조치(CAPA) 요구",
    "conditional": "회색지대/위험 신호 존재 — HITL 심사 후 조건부 승인 검토",
    "pass": "이상 없음 — 승인 진행",
}


def roll_up_verdict(verdicts, geo_risk, risk_level):
    values = list((verdicts or {}).values())
    if (
        any(v in ("compliance_violation", "compliance_reject") for v in values)
        or risk_level == "critical"
    ):
        return "fail"
    if (
        any(v == "compliance_warning" for v in values)
        or bool(geo_risk)
        or risk_level == "high"
    ):
        return "conditional"
    return "pass"


def build_metrics(verdicts, geo_flags, geo_risk, risk_score, risk_level):
    values = list((verdicts or {}).values())
    ordered_geo_flags = sorted(str(flag) for flag in (geo_flags or []))
    return {
        "violation": sum(
            1 for v in values
            if v in ("compliance_violation", "compliance_reject")
        ),
        "warning": (
            sum(1 for v in values if v == "compliance_warning")
            + (1 if geo_risk else 0)
            + (1 if risk_level == "high" else 0)
        ),
        "passed": sum(1 for v in values if v == "compliance_passed"),
        "geo_flags": ordered_geo_flags,
        "risk_score": int(risk_score or 0),
        "risk_level": risk_level,
    }


async def _load_verdicts(db: AsyncSession, batch_id: UUID):
    rows = (await db.execute(
        text("""
            SELECT r.regulation_code, cr.verdict
            FROM compliance_results cr
            JOIN regulations r ON r.regulation_id = cr.regulation_id
            WHERE cr.batch_id = :batch_id
            ORDER BY r.regulation_code
        """),
        {"batch_id": str(batch_id)},
    )).mappings().fetchall()
    return {row["regulation_code"]: row["verdict"] for row in rows}


async def _load_geo(db: AsyncSession, batch_id: UUID):
    row = (await db.execute(
        text("""
            SELECT risk_detected, risk_flags
            FROM geo_audit_results
            WHERE batch_id = :batch_id
        """),
        {"batch_id": str(batch_id)},
    )).mappings().fetchone()
    if row is None:
        return False, []
    return bool(row["risk_detected"]), row["risk_flags"] or []


async def _load_risk_score(db: AsyncSession, batch_id: UUID):
    row = (await db.execute(
        text("""
            SELECT
                MAX(srp.overall_risk_score)  AS max_risk_score,
                BOOL_OR(srp.is_high_risk_flag) AS has_high_risk
            FROM batches b
            JOIN supply_chain_map scm ON scm.bom_version_id = b.bom_version_id
            JOIN supplier_risk_profiles srp
                ON srp.supplier_id = scm.child_supplier_id
            WHERE b.batch_id = :batch_id
        """),
        {"batch_id": str(batch_id)},
    )).mappings().fetchone()
    if row is None or row["max_risk_score"] is None:
        return 0
    return int(row["max_risk_score"])


async def final_judgment_node(state: BatchState, db: AsyncSession) -> BatchState:
    batch_id = UUID(state["batch_id"])

    compliance_result = state.get("compliance_result") or {}
    verdicts = compliance_result.get("verdicts") or await _load_verdicts(db, batch_id)

    geo_result = state.get("geo_result") or {}
    if geo_result:
        geo_risk = bool(geo_result.get("risk_detected"))
        geo_flags = geo_result.get("risk_flags") or []
    else:
        geo_risk, geo_flags = await _load_geo(db, batch_id)

    risk_score = await _load_risk_score(db, batch_id)
    risk_level = calculate_risk_level(risk_score)
    overall_verdict = roll_up_verdict(verdicts, geo_risk, risk_level)
    metrics = build_metrics(verdicts, geo_flags, geo_risk, risk_score, risk_level)
    executive_summary = render_summary(overall_verdict, metrics)
    key_risks = render_key_risks(metrics)
    recommended_action = RECOMMENDED_ACTIONS[overall_verdict]
    confidence = state.get("confidence_score")

    await db.execute(
        text("""
            INSERT INTO batch_final_judgment
                (batch_id, overall_verdict, executive_summary, key_risks,
                 recommended_action, confidence, created_at)
            VALUES
                (:batch_id, :overall_verdict, :executive_summary,
                 CAST(:key_risks AS jsonb), :recommended_action,
                 :confidence, now())
            ON CONFLICT (batch_id)
            DO UPDATE SET
                overall_verdict = EXCLUDED.overall_verdict,
                executive_summary = EXCLUDED.executive_summary,
                key_risks = EXCLUDED.key_risks,
                recommended_action = EXCLUDED.recommended_action,
                confidence = EXCLUDED.confidence,
                created_at = now()
        """),
        {
            "batch_id": str(batch_id),
            "overall_verdict": overall_verdict,
            "executive_summary": executive_summary,
            "key_risks": json.dumps(key_risks, ensure_ascii=False),
            "recommended_action": recommended_action,
            "confidence": confidence,
        },
    )
    await db.execute(
        text("""
            UPDATE batches
            SET current_stage = 'stage_judgment'
            WHERE batch_id = :batch_id
        """),
        {"batch_id": str(batch_id)},
    )
    await db.commit()

    final_judgment = {
        "overall_verdict": overall_verdict,
        "executive_summary": executive_summary,
        "key_risks": key_risks,
        "recommended_action": recommended_action,
        "confidence": confidence,
    }
    return {
        **state,
        "current_stage": "stage_judgment",
        "final_judgment": final_judgment,
    }
