import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from backend.infrastructure.auth import CurrentUser, get_current_user
from backend.infrastructure.database import get_db
from backend.infrastructure.queue import enqueue, HITL_QUEUE, NOTIFICATION_QUEUE
import logging
logger = logging.getLogger(__name__)
from backend.hitl.repository import HitlRepository
from backend.hitl.service import HitlService

router = APIRouter(prefix="/hitl", tags=["HITL"])

class ResolveRequest(BaseModel):
    resolution: str
    decision_text: str

class DecisionRequest(BaseModel):
    decision_text: str

class DecisionIn(BaseModel):
    """§2.4c 스펙 결정 바디 — decision/reason/additionalActions."""
    decision: str  # approve | reject | escalate
    reason: str
    additional_actions: list[str] = []

def get_hitl_service(db: AsyncSession = Depends(get_db)) -> HitlService:
    return HitlService(HitlRepository(db))

# 1. 미처리 보류 건 목록 조회
@router.get("/queue", dependencies=[Depends(get_current_user)])
async def get_hitl_queue(status: str = 'hitl_pending', service: HitlService = Depends(get_hitl_service)):
    if status != 'hitl_pending':
        raise HTTPException(status_code=400, detail="Currently only hitl_pending status is supported for queue")
    return await service.get_pending_queue()

# 4. 검토에 필요한 모든 컨텍스트 단일 JSON 조회 (순서를 위해 위로 올렸어요)
@router.get("/{batch_id}/context", dependencies=[Depends(get_current_user)])
async def get_hitl_context(batch_id: uuid.UUID, service: HitlService = Depends(get_hitl_service), db: AsyncSession = Depends(get_db)):
    try:
        return await service.get_review_context(db, batch_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

# 2. 범용 Resolve 엔드포인트
@router.post("/{batch_id}/resolve")
async def resolve_hitl_review(
    batch_id: uuid.UUID,
    request: ResolveRequest,
    current_user: CurrentUser = Depends(get_current_user),
    service: HitlService = Depends(get_hitl_service),
    db: AsyncSession = Depends(get_db),
):
    try:
        review = await service.resolve_batch(
            db,
            batch_id=batch_id,
            resolution=request.resolution,
            decision_text=request.decision_text,
            user_id=current_user.user_id,
        )
        await db.commit()
        if request.resolution != "reject":
            await enqueue(
                HITL_QUEUE,
                "process_hitl_resolution",
                batch_id=str(batch_id),
                resolution=request.resolution,
                job_id=f"hitl_resume:{batch_id}",
            )
        return {"status": "success", "review_id": review.review_id, "resolution": review.resolution}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

# 3. 승인(Approve) 명시적 엔드포인트
@router.post("/{batch_id}/approve")
async def approve_hitl_review(
    batch_id: uuid.UUID,
    request: DecisionRequest,
    current_user: CurrentUser = Depends(get_current_user),
    service: HitlService = Depends(get_hitl_service),
    db: AsyncSession = Depends(get_db),
):
    try:
        review = await service.resolve_batch(
            db,
            batch_id=batch_id,
            resolution="approve",
            decision_text=request.decision_text,
            user_id=current_user.user_id,
        )
        await db.commit()
        await enqueue(
            HITL_QUEUE,
            "process_hitl_resolution",
            batch_id=str(batch_id),
            resolution="approve",
            job_id=f"hitl_resume:{batch_id}",
        )
        return {"status": "success", "review_id": review.review_id, "resolution": "approve"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

# 3. 반려(Reject) 명시적 엔드포인트
@router.post("/{batch_id}/reject")
async def reject_hitl_review(
    batch_id: uuid.UUID,
    request: DecisionRequest,
    current_user: CurrentUser = Depends(get_current_user),
    service: HitlService = Depends(get_hitl_service),
    db: AsyncSession = Depends(get_db),
):
    try:
        review = await service.resolve_batch(
            db,
            batch_id=batch_id,
            resolution="reject",
            decision_text=request.decision_text,
            user_id=current_user.user_id,
        )
        await db.commit()
        return {"status": "success", "review_id": review.review_id, "resolution": "reject"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── §2.4b/c reviewId 경로 (스펙 계약) ─────────────────────────────────────

@router.get("/review/{review_id}/context", dependencies=[Depends(get_current_user)])
async def get_hitl_context_by_review(
    review_id: uuid.UUID,
    service: HitlService = Depends(get_hitl_service),
    db: AsyncSession = Depends(get_db),
):
    """[API] GET /hitl/review/{reviewId} — 스펙 shape(agentVerdict/evidenceRows/attachments)로 반환."""
    from sqlalchemy import text as _text
    review = await service.repo.get_by_review_id(review_id)
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")
    try:
        ctx = await service.get_review_context(db, review.batch_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    # compliance_history → agentVerdict + evidenceRows
    comp = ctx.get("compliance_history") or []
    verdict_map = {
        "compliance_passed": "pass", "compliance_warning": "review",
        "compliance_violation": "fail", "compliance_reject": "fail",
    }
    agent_verdict = verdict_map.get(comp[0].get("verdict", ""), "review") if comp else "review"
    evidence_rows = [
        {
            "source": str(c.get("regulation_id", "")),
            "label": c.get("verdict", ""),
            "value": c.get("reasoning_text", ""),
            "verdict": verdict_map.get(c.get("verdict", ""), "review"),
        }
        for c in comp
    ]

    # evidence_urls → attachments
    attachments = [
        {
            "file_id": ev.get("document_id"),
            "file_name": ev.get("file_name"),
            "page_count": None,
        }
        for ev in (ctx.get("evidence_urls") or [])
    ]

    # supplierName
    supplier_name = ctx.get("supplier_master", {}).get("company_name")

    # productName — batch → product 조회
    product_row = (await db.execute(
        _text("""
            SELECT pr.product_name FROM batches b
            JOIN products pr ON pr.product_id = b.product_id
            WHERE b.batch_id = :bid LIMIT 1
        """),
        {"bid": str(review.batch_id)},
    )).mappings().fetchone()
    product_name = product_row["product_name"] if product_row else None

    return {
        "agent_verdict": agent_verdict,
        "evidence_rows": evidence_rows,
        "attachments": attachments,
        "product_name": product_name,
        "supplier_name": supplier_name,
        "review_info": ctx.get("review_info", {}),
    }


@router.post("/review/{review_id}/decision")
async def decide_hitl_review(
    review_id: uuid.UUID,
    body: DecisionIn,
    current_user: CurrentUser = Depends(get_current_user),
    service: HitlService = Depends(get_hitl_service),
    db: AsyncSession = Depends(get_db),
):
    """
    [API] POST /hitl/review/{reviewId}/decision — 스펙 결정 엔드포인트.
    decision → resolution 매핑. escalate 시 resume enqueue 제외.
    """
    review = await service.repo.get_by_review_id(review_id)
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")

    batch_id = review.batch_id
    resolution = body.decision  # approve | reject | escalate

    try:
        await service.resolve_batch(
            db,
            batch_id=batch_id,
            resolution=resolution,
            decision_text=body.reason,
            user_id=current_user.user_id,
        )
        await db.commit()

        if resolution not in ("reject", "escalate"):
            await enqueue(
                HITL_QUEUE,
                "process_hitl_resolution",
                batch_id=str(batch_id),
                resolution=resolution,
                job_id=f"hitl_resume:{batch_id}",
            )

        # additionalActions 처리 (commit 후)
        from sqlalchemy import text as _text
        for action in (body.additional_actions or []):
            if action == "notify_supplier":
                supplier_row = (await db.execute(
                    _text("SELECT target_supplier_id FROM data_request_log WHERE batch_id = :bid LIMIT 1"),
                    {"bid": str(batch_id)},
                )).fetchone()
                if not supplier_row:
                    logger.warning("[hitl] notify_supplier: batch에 연결된 제출 건 없음 (batch_id=%s)", batch_id)
                else:
                    supplier_id = supplier_row[0]
                    users = (await db.execute(
                        _text("""
                            SELECT u.user_id FROM users u
                            WHERE u.tenant_id = (SELECT tenant_id FROM suppliers WHERE supplier_id = :sid)
                              AND u.role IN ('supplier_ceo', 'supplier_esg')
                              AND u.is_active = TRUE
                        """),
                        {"sid": str(supplier_id)},
                    )).fetchall()
                    for row in users:
                        uid = str(row[0])
                        await enqueue(
                            NOTIFICATION_QUEUE,
                            "process_notification",
                            user_id=uid,
                            channel="in-app",
                            notification_type="approval_needed",
                            subject="HITL 결정이 완료됐습니다",
                            body=f"배치 {batch_id} 심사 결정: {resolution}",
                            dedup_key=f"hitl_decision:{batch_id}:{uid}",
                        )
            elif action == "audit":
                logger.info("[hitl] audit 액션 기록 (batch_id=%s, resolution=%s)", batch_id, resolution)
            elif action == "legal_review":
                logger.info("[hitl] legal_review 수신 기록 (stub) (batch_id=%s)", batch_id)

        return {"status": "success", "review_id": str(review_id), "resolution": resolution}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
