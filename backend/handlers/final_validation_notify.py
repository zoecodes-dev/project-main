"""
handlers/final_validation_notify.py — 공급망 완결 시 원청에 '최종 검증' 알림

흐름: "모든 데이터가 입력되었을 때 [원청]이 공급망 최종 검증 알림을 확인한다."

SubmissionApproved(협력사 데이터 승인) 수신 → 그 배치 제품의 공급망을 재평가해,
새로 완결(ready_for_final=True)됐으면 원청 담당자에게 in-app 알림을 발송한다.

완결 판정은 supplychain의 get_validation_summary(get_gaps + 비율검증)를 그대로 쓴다.
멱등: dedup_key(final_validation:{product}:{bom}:{user})로 맵당 1회만 — 알림 워커의
notifications.dedup_key UNIQUE가 중복 INSERT를 막는다.

도메인 경계: 이 핸들러(통합 슬롯)는 batches/users를 읽고 supplychain 서비스를 조회에만
쓰며, 알림은 큐로 넘긴다. (batch_trigger와 같은 통합 계층 패턴)
"""
import logging

from sqlalchemy import text

from backend.infrastructure.database import AsyncSessionLocal
from backend.infrastructure.queue import enqueue, NOTIFICATION_QUEUE
from backend.domains.supplychain.repository import SupplyChainRepository
from backend.domains.supplychain.service import SupplyChainService

logger = logging.getLogger(__name__)

# 원청(발주사) 역할 — 최종 검증 알림 수신 대상. (협력사 역할 supplier_* 제외)
_OEM_ROLES = ("owner_esg", "owner_purchasing", "admin")


async def notify_final_validation_ready(payload: dict) -> None:
    """SubmissionApproved 수신 → 제품 공급망이 새로 완결됐으면 원청에 최종 검증 알림."""
    batch_id = payload.get("batch_id")
    if not batch_id:
        return

    async with AsyncSessionLocal() as db:
        # 배치 → 제품/BOM/테넌트
        row = (await db.execute(
            text("SELECT product_id, bom_version_id, tenant_id FROM batches WHERE batch_id = :b"),
            {"b": str(batch_id)},
        )).first()
        if not row or not row[0]:
            return
        product_id = str(row[0])
        bom_version_id = str(row[1]) if row[1] else None
        tenant_id = str(row[2]) if row[2] else None
        if not tenant_id:
            tenant_id = (await db.execute(
                text("SELECT tenant_id FROM products WHERE product_id = :p"), {"p": product_id},
            )).scalar()
            tenant_id = str(tenant_id) if tenant_id else None
        if not tenant_id:
            return

        # 완결 판정 (조회 전용) — supplychain 요약/판정 재사용
        service = SupplyChainService(SupplyChainRepository(db))
        summary = await service.get_validation_summary(product_id, tenant_id, bom_version_id)
        if not summary.get("ready_for_final"):
            return

        # 원청 담당자 조회 (역할은 하드코딩 상수 → 안전하게 인라인)
        roles_sql = ", ".join(f"'{r}'" for r in _OEM_ROLES)
        oem_users = (await db.execute(
            text(f"""
                SELECT user_id FROM users
                WHERE tenant_id = :t AND role IN ({roles_sql}) AND is_active = TRUE
            """),
            {"t": tenant_id},
        )).fetchall()

    if not oem_users:
        logger.warning("[final_validation] 원청 담당자 없음 (tenant=%s)", tenant_id)
        return

    scope = f"{product_id}:{bom_version_id or '-'}"
    for r in oem_users:
        uid = str(r[0])
        await enqueue(
            NOTIFICATION_QUEUE,
            "process_notification",
            user_id=uid,
            channel="in-app",
            notification_type="approval_needed",
            subject="공급망 최종 검증 준비 완료",
            body=(
                f"공급망 데이터가 모두 입력되어 최종 검증이 가능합니다. "
                f"(협력사 {summary.get('supplier_count')}곳 · 최대 {summary.get('max_tier')}차)"
            ),
            dedup_key=f"final_validation:{scope}:{uid}",
        )
    logger.info("[final_validation] 최종 검증 알림 enqueue product=%s users=%d", product_id, len(oem_users))
