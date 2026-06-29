"""
handlers/supplier_document_ingest.py — 협력사 필요문서 → AI 파싱 파이프라인 다리

SupplierDocumentUploaded 이벤트를 수신해 업로드된 문서를 파싱 흐름에 태운다:
  1) data_request_log 행 생성 (target_supplier_id, requested_data_type='self_upload:<kind>')
     → AI 추출 결과(document_extraction_results)가 ai-extractions 조회(INNER JOIN)에
       걸리려면 request_id가 필요하다. 'self_upload:' 접두어로 식별·일괄정리 가능.
  2) submission_documents 행 생성 (file_url=S3 키, file_type 파생, doc_category 매핑)
  3) document_parse 큐에 enqueue → 워커가 data_gateway로 S3에서 읽어 파싱

멱등성: 같은 S3 키로 이미 submission_documents 행이 있으면 스킵(중복 행/중복 파싱 방지).

도메인 경계: 이 핸들러(슬롯)는 submission 도메인 소유 테이블에만 쓴다.
  supplier 도메인은 SupplierDocumentUploaded 이벤트만 발행하고 직접 쓰지 않는다.
발행 순서: DB 커밋 성공 후에 enqueue 한다(롤백 불일치 방지).
"""
import uuid
import logging

from backend.infrastructure.database import AsyncSessionLocal
from backend.infrastructure.queue import enqueue, DOCUMENT_PARSE_QUEUE
from backend.domains.submission.models import DataRequestLog
from backend.domains.submission.repository import (
    create_data_request,
    create_submission_document,
    get_submission_document_by_file_url,
)

logger = logging.getLogger(__name__)

# doc_kind → submission_documents.doc_category (CHECK 제약 허용값에 매핑)
_DOC_CATEGORY = {
    "business_reg": "certification",
    "environmental_report": "carbon_data",
    "self_assessment": "audit_report",
}

# 확장자 → file_type (CHECK 제약: pdf/xlsx/csv/image/docx/other)
# data_gateway는 'image'만 안정 파싱(pdf는 변환 전제, 그 외는 미파싱). 파싱 가부는 워커가 판단.
_FILE_TYPE_BY_EXT = {
    "png": "image", "jpg": "image", "jpeg": "image", "gif": "image", "webp": "image",
    "pdf": "pdf", "xlsx": "xlsx", "xls": "xlsx", "csv": "csv",
    "docx": "docx", "doc": "docx",
}


def _derive_file_type(file_name: str | None) -> str:
    ext = file_name.rsplit(".", 1)[-1].lower() if file_name and "." in file_name else ""
    return _FILE_TYPE_BY_EXT.get(ext, "other")


async def on_supplier_document_uploaded(payload: dict) -> None:
    """SupplierDocumentUploaded 수신 → 요청/문서 행 생성 + 파싱 큐 enqueue."""
    supplier_id = payload.get("supplier_id")
    s3_key = payload.get("s3_key")
    file_name = payload.get("file_name")
    doc_kind = payload.get("doc_kind") or "other"

    if not supplier_id or not s3_key:
        logger.warning("[doc_ingest] supplier_id/s3_key 누락 — 스킵: %s", payload)
        return

    async with AsyncSessionLocal() as db:
        # 멱등성: 같은 S3 키가 이미 등록됐으면 재처리 안 함.
        if await get_submission_document_by_file_url(db, s3_key) is not None:
            logger.info("[doc_ingest] 이미 등록된 문서(s3_key=%s) — 스킵", s3_key)
            return

        # 1) data_request_log (self_upload 식별 태그)
        req = await create_data_request(db, DataRequestLog(
            target_supplier_id=uuid.UUID(str(supplier_id)),
            requested_data_type=f"self_upload:{doc_kind}",
        ))
        # 2) submission_documents (file_url = S3 키)
        doc = await create_submission_document(
            db,
            request_id=req.request_id,
            supplier_id=uuid.UUID(str(supplier_id)),
            file_url=s3_key,
            file_name=file_name,
            file_type=_derive_file_type(file_name),
            doc_category=_DOC_CATEGORY.get(doc_kind, "other"),
        )
        await db.commit()
        request_id, document_id = str(req.request_id), str(doc.document_id)

    # 3) 커밋 후 파싱 큐 enqueue (멱등 키: document_id)
    await enqueue(
        DOCUMENT_PARSE_QUEUE,
        "process_document_parse",
        job_id=f"document_parse:{document_id}",
        document_id=document_id,
        request_id=request_id,
    )
    logger.info("[doc_ingest] 파싱 enqueue 완료 doc=%s req=%s", document_id, request_id)
