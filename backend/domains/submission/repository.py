"""
Submission 도메인 Data Access 계층 (Repository)

[설계 의도]
이곳에는 SQLAlchemy를 이용한 순수한 DB I/O(SELECT, INSERT) 로직만 위치합니다.
비즈니스 로직이나 복잡한 상태 전이(상태 머신 호출)는 포함하지 않음으로써,
데이터베이스 접근 로직과 비즈니스 룰을 명확히 분리(Separation of Concerns)합니다.
"""
import uuid
from typing import Optional
from sqlalchemy import select, asc
from sqlalchemy import select, Table, Column, MetaData, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from backend.domains.submission.models import (
    DataRequestLog,
    DataCompletenessStatus,
    SubmissionStatusHistory,
    DocumentExtractionResult,
    ProcessedJob,
)
# suppliers는 supplier 도메인 소유 테이블이라 ORM 모델을 import(약속 6번 위반)하지 않고,
# supplier_type만 읽기 위한 최소 Table 매핑을 둔다. (dpp/supplychain repo의 raw 조인과 동일 취지)
_supplier_meta = MetaData()
_suppliers_tbl = Table(
    "suppliers", _supplier_meta,
    Column("supplier_id", PG_UUID(as_uuid=True), primary_key=True),
    Column("supplier_type", String(30)),
)

async def create_data_request(db: AsyncSession, log_record: DataRequestLog) -> DataRequestLog:
    """
    [INSERT] 새로운 공급망 데이터 요청(DataRequestLog) 마스터 기록을 DB에 삽입합니다.
    * 주의: 초기 REQUESTED 상태 삽입 목적으로만 사용합니다.
      이후의 상태 전이(UPDATE)는 반드시 state_machine.py의 transition_submission()을 거쳐야 
      submission_status_history에 감사 이력이 남습니다.
    """
    db.add(log_record)
    await db.flush()
    return log_record

async def get_data_request(db: AsyncSession, request_id: uuid.UUID) -> Optional[DataRequestLog]:
    """
    [SELECT] 단건 데이터 요청 내역을 PK(request_id) 기반으로 조회합니다.
    상태 변경 전후로 최신 상태를 불러오거나 상세 페이지를 렌더링할 때 사용됩니다.
    """
    stmt = select(DataRequestLog).where(DataRequestLog.request_id == request_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()

async def list_data_requests(
    db: AsyncSession,
    supplier_id: Optional[uuid.UUID] = None,
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 100
) -> list[DataRequestLog]:
    """
    [SELECT] 조건에 맞는 데이터 요청 목록을 필터링하여 조회합니다.
    - 프론트엔드 UI의 협력사 리스트 필터링 및 페이징 처리를 위해 동적으로 WHERE 절을 구성합니다.
    - 최신 요청이 먼저 보이도록 requested_at 기준 내림차순 정렬을 기본으로 적용합니다.
    """
    stmt = select(DataRequestLog)
    if supplier_id:
        stmt = stmt.where(DataRequestLog.target_supplier_id == supplier_id)
    if status:
        stmt = stmt.where(DataRequestLog.submission_status == status)
    stmt = stmt.order_by(DataRequestLog.requested_at.desc()).offset(skip).limit(limit)
    
    result = await db.execute(stmt)
    return list(result.scalars().all())

async def get_completeness_by_supplier(db: AsyncSession, supplier_id: uuid.UUID) -> Optional[DataCompletenessStatus]:
    """[SELECT] 특정 협력사의 데이터 완성도 현황을 단건 조회합니다."""
    stmt = select(DataCompletenessStatus).where(
        DataCompletenessStatus.entity_type == 'supplier',
        DataCompletenessStatus.entity_id == supplier_id
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()

async def get_timeline_by_supplier(db: AsyncSession, supplier_id: uuid.UUID) -> list[SubmissionStatusHistory]:
    """[SELECT] 특정 협력사의 모든 데이터 제출 요청 상태 변경 이력을 시간순으로 정렬하여 반환합니다."""
    stmt = select(SubmissionStatusHistory).join(DataRequestLog).where(
        DataRequestLog.target_supplier_id == supplier_id
    ).order_by(asc(SubmissionStatusHistory.changed_at))
    
    result = await db.execute(stmt)
    return list(result.scalars().all())

async def create_extraction_result(
    db: AsyncSession,
    *,
    request_id: uuid.UUID | str,
    document_id: uuid.UUID | str,
    parsed_fields: dict,
    confidence_map: dict,
    unparsed_fields: list,
) -> DocumentExtractionResult:
    """
    [INSERT] AI(data_gateway)가 문서에서 추출한 정형 데이터를
    document_extraction_results에 적재합니다.
 
    * request_id / document_id는 str·UUID 둘 다 받아 여기서 UUID로 정규화한다.
      (호출부 출처마다 타입이 다름: document_id=str, request_id=UUID.
       변환 책임을 이 경계 한 곳에 모아 호출부가 타입을 신경쓰지 않게 한다.)
    * supplier_confirmed는 기본 FALSE(모델 server_default) — 협력사가 추후
      "확인" 버튼을 누르기 전까지는 미확정 상태로 둡니다.
    * extraction_id / created_at은 모델 server_default(uuid_generate_v4 / now())에
      맡깁니다. commit은 호출부 책임(기존 repository 규약 동일).
    """
    record = DocumentExtractionResult(
        request_id=uuid.UUID(str(request_id)),
        document_id=uuid.UUID(str(document_id)),
        parsed_fields=parsed_fields,
        confidence_map=confidence_map,
        unparsed_fields=unparsed_fields,
    )
    db.add(record)
    await db.flush()
    return record

# 협력사 문서추출결과 추출 결과 수집 함수
async def list_extraction_results_by_suppliers(
    db: AsyncSession,
    supplier_ids: list[uuid.UUID],
) -> list[tuple[DocumentExtractionResult, str]]:   # ← 반환 타입 변경
    """
    [SELECT] 주어진 협력사들의 문서 추출결과를 supplier_type과 함께 모은다.
    document_extraction_results → data_request_log(target_supplier_id)
      → suppliers(supplier_type) 3-단 조인.
    node(data_gateway)는 (추출결과, supplier_type) 튜플로 신뢰도·확인여부와
    validate_schema 누락 검사(spec 노드 정의 #2단계)를 집계한다.
    """
    if not supplier_ids:
        return []

    stmt = (
        select(DocumentExtractionResult, _suppliers_tbl.c.supplier_type)   # ← 튜플 select
        .join(
            DataRequestLog,
            DataRequestLog.request_id == DocumentExtractionResult.request_id,
        )
        .join(
            _suppliers_tbl,
            _suppliers_tbl.c.supplier_id == DataRequestLog.target_supplier_id,
        )
        .where(DataRequestLog.target_supplier_id.in_(supplier_ids))
    )
    result = await db.execute(stmt)
    return list(result.all())

# ============================================================================
# [동작] idempotency_key를 PK로 INSERT 시도 →
#   - 성공: 이 작업 첫 실행. claim 반환(처리 진행).
#   - PK 충돌(IntegrityError): 이미 누군가 처리/처리 중. 재실행 막음.
# ============================================================================
async def claim_job(
    db: AsyncSession,
    *,
    idempotency_key: str,
    queue_name: str,
    job_id: str | None = None,
) -> bool:
    """
    [멱등성 claim] idempotency_key를 PK INSERT로 선점한다.
    - True  : 첫 실행 → 작업을 진행하라.
    - False : 이미 존재(중복/재시도) → 작업을 건너뛰라.
 
    PK 충돌을 멱등성 신호로 쓰므로, 호출부는 별도 SELECT 없이 이 반환값만 보면 된다.
    commit은 호출부(워커)가 트랜잭션 경계로 잡는다 — 여기선 flush까지.
    충돌 시 savepoint를 되돌려 같은 세션을 계속 쓸 수 있게 한다.
    """
    sp = await db.begin_nested()   # savepoint: 충돌 나도 바깥 트랜잭션 안 깨지게
    try:
        db.add(ProcessedJob(
            idempotency_key=idempotency_key,
            queue_name=queue_name,
            job_id=job_id,
            status="processing",
        ))
        await db.flush()
        return True
    except IntegrityError:
        await sp.rollback()
        return False
 
 
async def mark_job_done(
    db: AsyncSession,
    *,
    idempotency_key: str,
    result: dict | None = None,
) -> None:
    """[완료 표시] claim 후 작업이 성공하면 status='done' + 결과 캐시."""
    job = await db.get(ProcessedJob, idempotency_key)
    if job is not None:
        job.status = "done"
        if result is not None:
            job.result = result
    await db.flush()
 
 
async def mark_job_failed(
    db: AsyncSession,
    *,
    idempotency_key: str,
    error_text: str,
) -> None:
    """[실패 표시] 작업 실패 시 status='failed' + 사유. retry_count 증가."""
    job = await db.get(ProcessedJob, idempotency_key)
    if job is not None:
        job.status = "failed"
        job.error_text = error_text
        job.retry_count = (job.retry_count or 0) + 1
    await db.flush()