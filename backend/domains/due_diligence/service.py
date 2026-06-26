"""
domains/due_diligence/service.py

Due Diligence 비즈니스 로직.
커밋은 이 계층에서만 수행(CLAUDE.md §1).
이벤트 발행이 필요한 경우 commit 후 publish().
도메인 간 직접 import 금지.
"""
import json
import uuid
from typing import Any, Dict, List, Optional

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domains.due_diligence.repository import DueDiligenceRepository
from backend.domains.files import service as file_service


class DueDiligenceService:
    def __init__(self, repository: DueDiligenceRepository):
        self.repository = repository

    async def list_audits(
        self,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        status: Optional[str],
        search: Optional[str],
        page: int,
        size: int,
    ) -> tuple[List[Dict[str, Any]], int]:
        items = await self.repository.list_audits(tenant_id, status, search, page, size)
        total = await self.repository.count_audits(tenant_id, status, search)
        return items, total

    async def get_audit_detail(
        self,
        audit_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> Optional[Dict[str, Any]]:
        row = await self.repository.get_audit_detail(audit_id, tenant_id)
        if row is None:
            return None
        # findings / capa: JSONB → list 정규화
        row["findings"] = _normalize_jsonb(row.get("findings")) or []
        row["capa"] = _normalize_jsonb(row.get("capa")) or []
        return row

    async def create_audit(
        self,
        db: AsyncSession,
        supplier_id: Optional[uuid.UUID],
        factory_id: Optional[uuid.UUID],
        audit_name: str,
        audit_scope: str,
    ) -> Dict[str, Any]:
        result = await self.repository.create_audit(
            supplier_id=supplier_id,
            factory_id=factory_id,
            audit_name=audit_name,
            audit_scope=audit_scope,
        )
        await db.commit()
        return result

    async def upload_report(
        self,
        db: AsyncSession,
        audit_id: uuid.UUID,
        tenant_id: uuid.UUID,
        file: UploadFile,
        result: Optional[str],
        score: Optional[float],
    ) -> Optional[Dict[str, Any]]:
        """
        보고서 multipart 업로드 → /files 모듈로 S3 저장 → audit record 갱신.
        커밋: file_service.upload_file 내부에서 1회, audit update 후 1회.
        """
        data = await file.read()
        file_meta = await file_service.upload_file(
            db,
            file_name=file.filename or "report",
            data=data,
            content_type=file.content_type,
            context="due_diligence_report",
            tenant_id=tenant_id,
            uploaded_by=None,
        )
        report_file_id = uuid.UUID(file_meta["file_id"])

        updated = await self.repository.update_report(
            audit_id=audit_id,
            tenant_id=tenant_id,
            result=result,
            score=score,
            report_file_id=report_file_id,
        )
        if updated is None:
            return None
        await db.commit()
        return updated

    async def update_capa_status(
        self,
        db: AsyncSession,
        audit_id: uuid.UUID,
        capa_id: str,
        status: str,
        tenant_id: uuid.UUID,
    ) -> Optional[Dict[str, Any]]:
        updated = await self.repository.update_capa_status(
            audit_id=audit_id,
            capa_id=capa_id,
            status=status,
            tenant_id=tenant_id,
        )
        if updated is None:
            return None
        await db.commit()
        return updated


def _normalize_jsonb(value: Any) -> Optional[list]:
    """
    JSONB 컬럼 값을 list로 정규화.
    raw text() 쿼리에서 asyncpg는 JSONB를 JSON '문자열'로 반환하므로
    문자열이면 json.loads로 파싱한다(ORM Column(JSONB)가 아니면 자동 역직렬화 안 됨).
    """
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return []
    if isinstance(value, list):
        return value
    return []
