import uuid
import json
from sqlalchemy.ext.asyncio import AsyncSession
from backend.hitl.repository import HitlRepository
from backend.hitl.state_machine import HitlStateMachine
from backend.infrastructure.event_bus import publish
from backend.infrastructure.trace import trace_node, trace_tool
from langchain_core.messages import HumanMessage, SystemMessage
from backend.llm.bedrock_factory import get_llm_for_agent
from backend.domains.verification.service import get_compliance_history_dto
from backend.domains.supplychain.repository import SupplyChainRepository
from backend.domains.submission.service import get_evidence_urls_dto
from backend.infrastructure.queue import enqueue, HITL_QUEUE

@trace_tool("summarize_hitl_context")
async def _summarize_hitl_context(context_data: dict) -> str:
    """
    [도구] HITL 심사관을 위한 Haiku 컨텍스트 요약 (참고용)
    """
    try:
        # bedrock_factory.py에 정의된 Haiku용 에이전트 키("lightweight")를 사용해요.
        llm = get_llm_for_agent("lightweight")
        system_msg = SystemMessage(
            content="당신은 배터리 공급망 컴플라이언스 심사관을 돕는 AI 비서입니다. "
                    "제공된 데이터를 바탕으로 이 배치가 왜 보류(HITL)되었는지, "
                    "주요 규제 위반 의심 사항을 3줄 이내의 한국어로 요약해주세요. "
                    "승인/반려 결정은 하지 마세요."
        )
        human_msg = HumanMessage(
            content=json.dumps(context_data, ensure_ascii=False, default=str)
        )
        
        resp = await llm.ainvoke([system_msg, human_msg])
        return resp.content if isinstance(resp.content, str) else str(resp.content)
    except Exception as e:
        return f"AI 요약을 생성하지 못했습니다. (오류: {str(e)})"

class HitlService:
    def __init__(self, repo: HitlRepository):
        self.repo = repo

    async def get_pending_queue(self):
        # Raw SQL을 통해 batches 테이블의 confidence_score를 조인해서 가져와요.
        return await self.repo.get_queue_with_batch_info('hitl_pending')

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
        
        # LangGraph 파이프라인 재개는 무거우므로 ARQ 워커(hitl_queue)로 위임합니다.
        await enqueue(
            HITL_QUEUE,
            "process_hitl_resolution",
            batch_id=str(batch_id),
            resolution=resolution,
            job_id=f"hitl_resume_{batch_id}"
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

    async def get_review_context(self, db: AsyncSession, batch_id: uuid.UUID) -> dict:
        review = await self.repo.get_by_batch_id(batch_id)
        if not review:
            raise ValueError("Review not found for given batch_id")
            
        # 각 도메인의 조회 헬퍼 함수(DTO 인터페이스)를 경유하여 데이터를 수집합니다.
        comp_history = await get_compliance_history_dto(db, batch_id)
        supplier_id = comp_history[0].get("supplier_id") if comp_history else None

        supplier_master = {}
        factory_gps = []
        evidence_urls = []

        if supplier_id:
            sc_repo = SupplyChainRepository(db)
            sup_data = await sc_repo.get_supplier_master_and_gps_dto(supplier_id)
            supplier_master = sup_data["supplier_master"]
            factory_gps = sup_data["factory_gps"]
            evidence_urls = await get_evidence_urls_dto(db, supplier_id)

        context_data = {
            "compliance_history": comp_history,
            "supplier_master": supplier_master,
            "factory_gps": factory_gps,
            "evidence_urls": evidence_urls
        }
        
        context_data["review_info"] = {
            "review_id": str(review.review_id),
            "batch_id": str(review.batch_id),
            "reason": review.reason,
            "trigger_stage": review.trigger_stage,
            "status": review.status,
            "created_at": review.created_at.isoformat() if review.created_at else None
        }
        
        # Haiku 요약은 가벼운 참고용으로 덧붙여 줍니다.
        context_data["ai_summary"] = await _summarize_hitl_context(context_data)
        return context_data
