import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from backend.hitl.repository import HitlRepository
from backend.hitl.state_machine import HitlStateMachine
from backend.infrastructure.event_bus import publish
from backend.infrastructure.trace import trace_node

class HitlService:
    def __init__(self, repo: HitlRepository):
        self.repo = repo

    async def get_pending_queue(self):
        # TODO: 실제로는 여기서 에이전트 판정 요약과 confidence_score를 포함한 DTO를 반환하게 묶어줍니다.
        reviews = await self.repo.get_queue_by_status('hitl_pending')
        return [
            {
                "review_id": r.review_id,
                "batch_id": r.batch_id,
                "reason": r.reason,
                "trigger_stage": r.trigger_stage,
                "status": r.status,
                "created_at": r.created_at
            }
            for r in reviews
        ]

    @trace_node(node_name="hitl_human_review", node_type="human")
    async def resolve_batch(self, db: AsyncSession, *, batch_id: uuid.UUID, resolution: str, decision_text: str, user_id: uuid.UUID | None = None):
        review = await self.repo.get_by_batch_id(batch_id)
        if not review:
            raise ValueError("Review not found for given batch_id")

        # 1. 상태 전이 (SQL 직접 수정 금지)
        review = HitlStateMachine.resolve_review(review, resolution, decision_text, user_id)
        # Session commit 은 이 서비스를 호출한 router(의 Depends) 쪽에서 일괄 처리된다고 가정해요.

        # 2. LangGraph 재개(resume) 신호 비동기 발행 (지혜 graph 트리거용)
        await publish(
            event_name="hitl.resolved",
            payload={
                "batch_id": str(batch_id),
                "resolution": resolution
            }
        )

        # 3. 반려 시 연관 제출 건 처리를 위해 Submission 도메인으로 신호 발행 (직접 SQL 수정 금지)
        if resolution == 'reject':
            await publish(
                event_name="submission.reject_requested",
                payload={
                    "batch_id": str(batch_id),
                    "reason": decision_text
                }
            )
            
        return review

    async def get_review_context(self, batch_id: uuid.UUID) -> dict:
        # 단일 JSON 응답을 위해 필요한 컴플라이언스 이력, 공장 GPS, 협력사 마스터, 증빙 URL 등을 
        # API Gateway/BFF 또는 CQRS Read 모델에서 조합해서 가져온다고 가정해요.
        # 다른 도메인 직접 import 금지 원칙에 따라 여기서는 Mocking 형태로 뼈대만 잡습니다.
        review = await self.repo.get_by_batch_id(batch_id)
        if not review:
            raise ValueError("Review not found for given batch_id")
            
        return {
            "review_info": {
                "review_id": review.review_id,
                "batch_id": review.batch_id,
                "reason": review.reason,
                "trigger_stage": review.trigger_stage,
                "status": review.status,
                "created_at": review.created_at
            },
            "compliance_history": [],
            "factory_gps": {},
            "supplier_master": {},
            "evidence_urls": []
        }
