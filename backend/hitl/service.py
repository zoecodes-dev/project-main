import uuid
import json
import asyncio
import logging
import boto3
from botocore.exceptions import ClientError
from sqlalchemy import text
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
from backend.domains.audit.repository import list_trail_by_batch

logger = logging.getLogger(__name__)

# [BYPASS:C4] config 이동 예정(W5 인프라)
S3_BUCKET = "kira-documents-423937245947-ap-northeast-2-an"
AWS_REGION = "ap-northeast-2"

_s3_client = boto3.client("s3", region_name=AWS_REGION)

def _generate_presigned_url(s3_key: str, expiration: int = 3600) -> str:
    """S3 객체 키를 받아 접근 가능한 Presigned URL로 변환합니다."""
    if not s3_key or s3_key.startswith("http"):
        return s3_key
        
    # s3:// 로 시작하는 URI 형식일 경우 순수 S3 Key만 추출
    clean_key = s3_key
    if s3_key.startswith("s3://"):
        # 예: s3://kira-docs/ds_factory.xlsx -> ds_factory.xlsx
        clean_key = s3_key.split("/", 3)[-1]
        
    try:
        return _s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': S3_BUCKET, 'Key': clean_key},
            ExpiresIn=expiration
        )
    # [BYPASS:C1] AWS 권한 부재 시 dummy URL 폴백 — 프론트 감지용 마커 포함
    except Exception:
        # 로컬 테스트 환경 등 AWS 권한이 없을 경우 프론트엔드 화면이 깨지지 않게 임시 URL 반환
        logger.error("Presigned URL 발급 실패 — dummy URL 폴백: %s", clean_key)
        return f"https://{S3_BUCKET}.s3.{AWS_REGION}.amazonaws.com/{clean_key}?dummy_presigned=true"

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
        # [BYPASS:C2] LLM 실패 시 오류 문자열 반환(정직한 폴백)
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

        # 3. 반려 시 연관 제출 건 처리를 위해 Submission 도메인으로 신호 발행 (직접 SQL 수정 금지)
        if resolution == 'reject':
            # batches.status 전이 — WHERE의 현재상태 조건은 멱등성용
            # (schema.sql batches 테이블엔 updated_at 컬럼이 없어 status만 갱신)
            await db.execute(
                text("""UPDATE batches
                        SET status = 'batch_rejected'
                        WHERE batch_id = :b AND status = 'batch_hitl_wait'"""),
                {"b": str(batch_id)},
            )
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

        # Audit Trail(감사 로그) 조회 연동
        audit_records = await list_trail_by_batch(db, batch_id)
        audit_history = [
            {
                "step": r.step_number,
                "node_name": r.node_name,
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                "decision_text": r.decision_text
            }
            for r in audit_records
        ]

        compliance_data = {
            "results": comp_history,
            "audit_trail": audit_history
        }

        supplier_master = {}
        factory_gps = []
        evidence_urls = []

        if supplier_id:
            sc_repo = SupplyChainRepository(db)
            sup_data = await sc_repo.get_supplier_master_and_gps_dto(supplier_id)
            supplier_master = sup_data.get("supplier_master", {})
            factory_gps = sup_data.get("factory_gps", [])

            # 제출 증빙 URL 조회 및 Presigned URL 동적 발급
            raw_evidences = await get_evidence_urls_dto(db, supplier_id)
            for ev in raw_evidences:
                ev["presigned_url"] = await asyncio.to_thread(_generate_presigned_url, ev.get("file_url"))
            evidence_urls = raw_evidences

        context_data = {
            "compliance_history": compliance_data,
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
