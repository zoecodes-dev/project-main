import uuid
from datetime import datetime, timezone
from backend.hitl.models import HitlReview

class HitlStateMachine:
    VALID_STATUSES = ['hitl_pending', 'hitl_in_review', 'hitl_resolved']
    VALID_RESOLUTIONS = ['approve', 'reject', 'escalate']

    @staticmethod
    def resolve_review(review: HitlReview, resolution: str, decision_text: str, user_id: uuid.UUID | None) -> HitlReview:
        # 이미 처리된 건인지 방어 로직
        if review.status == 'hitl_resolved':
            raise ValueError("This review is already resolved.")
            
        if resolution not in HitlStateMachine.VALID_RESOLUTIONS:
            raise ValueError(f"Invalid resolution: {resolution}")

        # 상태 및 이력 업데이트
        review.status = 'hitl_resolved'
        review.resolution = resolution
        review.decision_text = decision_text
        review.decided_by = user_id
        review.decided_at = datetime.now(timezone.utc)

        return review
