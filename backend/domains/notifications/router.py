from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from backend.infrastructure.auth import CurrentUser, get_current_user
from backend.infrastructure.database import get_db

router = APIRouter(prefix="/notifications", tags=["notifications"])

# DB notification_type → 프론트 4종으로 정규화
_TYPE_MAP: dict[str, str] = {
    "sla_warning":      "sla_warning",
    "violation":        "violation",
    "approval_needed":  "approval_needed",
    "reminder":         "sla_warning",
    "training_overdue": "info",
}

# notification_type → 협력사 포털 탭 딥링크
_DEEP_LINK: dict[str, str] = {
    "sla_warning":      "submit-documents",
    "violation":        "submission-status",
    "approval_needed":  "ai-parsing",
    "reminder":         "submit-documents",
    "training_overdue": "company-info",
}


@router.get("")
async def list_notifications(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """로그인한 사용자의 in-app 알림 목록 (최근 50건, 실패 제외)."""
    result = await db.execute(
        text("""
            SELECT notification_id, notification_type, subject, body, status, created_at
            FROM notifications
            WHERE user_id = :user_id
              AND channel = 'in-app'
              AND status != 'failed'
            ORDER BY created_at DESC
            LIMIT 50
        """),
        {"user_id": str(current_user.user_id)},
    )
    rows = result.mappings().all()
    return [
        {
            "notification_id": str(r["notification_id"]),
            "notification_type": _TYPE_MAP.get(r["notification_type"] or "", "info"),
            "subject": r["subject"] or "",
            "body": r["body"] or "",
            "status": "read" if r["status"] == "read" else "pending",
            "created_at": r["created_at"].isoformat() if r["created_at"] else "",
            "deep_link": _DEEP_LINK.get(r["notification_type"] or ""),
        }
        for r in rows
    ]


@router.patch("/{notification_id}/read", status_code=204)
async def mark_notification_read(
    notification_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """알림 한 건 읽음 처리 — 본인 알림만 업데이트."""
    await db.execute(
        text("""
            UPDATE notifications
            SET status = 'read', read_at = now()
            WHERE notification_id = :nid
              AND user_id = :user_id
        """),
        {"nid": notification_id, "user_id": str(current_user.user_id)},
    )
    await db.commit()
