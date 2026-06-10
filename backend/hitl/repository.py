import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text

from backend.hitl.models import HitlReview

class HitlRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_queue_by_status(self, status: str):
        stmt = select(HitlReview).where(HitlReview.status == status)
        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def get_by_batch_id(self, batch_id: uuid.UUID) -> HitlReview | None:
        stmt = select(HitlReview).where(HitlReview.batch_id == batch_id)
        result = await self.db.execute(stmt)
        return result.scalars().first()

    async def get_queue_with_batch_info(self, status: str) -> list[dict]:
        """
        [도메인 격리 준수] batches 테이블을 조인하여 에이전트 신뢰도(confidence_score)를 가져옵니다.
        """
        stmt = text("""
            SELECT 
                h.review_id, h.batch_id, h.reason, h.trigger_stage, h.status, h.created_at,
                b.confidence_score
            FROM hitl_reviews h
            JOIN batches b ON h.batch_id = b.batch_id
            WHERE h.status = :status
            ORDER BY h.created_at ASC
        """)
        result = await self.db.execute(stmt, {"status": status})
        return [dict(row._mapping) for row in result.fetchall()]

    async def get_review_context_raw(self, batch_id: uuid.UUID) -> dict:
        """
        [도메인 격리 준수] 타 도메인 ORM import 없이 Raw SQL로 
        컴플라이언스 이력, 협력사 마스터, 공장 GPS, 증빙 URL을 한 번에 조회합니다.
        """
        comp_stmt = text("""
            SELECT verdict, reasoning_text, supplier_id, regulation_id
            FROM compliance_results
            WHERE batch_id = :batch_id
        """)
        comp_rows = (await self.db.execute(comp_stmt, {"batch_id": str(batch_id)})).mappings().fetchall()
        
        supplier_id = comp_rows[0].get("supplier_id") if comp_rows else None

        supplier_master = {}
        factory_gps = []
        evidence_urls = []
        
        if supplier_id:
            sup_stmt = text("SELECT company_name, risk_level FROM suppliers WHERE supplier_id = :supplier_id")
            sup_row = (await self.db.execute(sup_stmt, {"supplier_id": supplier_id})).mappings().first()
            if sup_row:
                supplier_master = dict(sup_row)

            fac_stmt = text("""
                SELECT factory_name, ST_AsGeoJSON(location) as location_geojson 
                FROM supplier_factories 
                WHERE supplier_id = :supplier_id AND is_active = TRUE
            """)
            factory_gps = [dict(r) for r in (await self.db.execute(fac_stmt, {"supplier_id": supplier_id})).mappings().fetchall()]

            doc_stmt = text("SELECT document_id, file_url, file_name, doc_category FROM submission_documents WHERE supplier_id = :supplier_id")
            evidence_urls = [dict(r) for r in (await self.db.execute(doc_stmt, {"supplier_id": supplier_id})).mappings().fetchall()]

        return {
            "compliance_history": [dict(r) for r in comp_rows],
            "supplier_master": supplier_master,
            "factory_gps": factory_gps,
            "evidence_urls": evidence_urls
        }
