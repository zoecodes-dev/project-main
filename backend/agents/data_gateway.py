# backend/agents/data_gateway.py

import base64
import json
from uuid import UUID

import asyncio
import boto3
from botocore.exceptions import ClientError

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.database import AsyncSessionLocal
from backend.infrastructure.trace import trace_node, trace_tool
from backend.domains.submission import repository as submission_repo
from backend.domains.supplychain.repository import SupplyChainRepository  # D 제공
from backend.agents.state import BatchState
from backend.llm.bedrock_factory import get_llm_for_agent
from backend.events.types import ValidationResult

CONFIDENCE_THRESHOLD = 0.85

# 모델에 "이 형식으로만 답하라"고 못박는 지시. JSON만 받아야 json.loads가 안전하다.
_EXTRACTION_SYSTEM = (
    "You are a document data-extraction engine for a battery supply-chain "
    "compliance system. Read the document and return ONLY a JSON object — "
    "no prose, no markdown fences. Schema:\n"
    '{"parsed_fields": {<field>: <value>, ...}, '
    '"confidence_map": {<field>: <0.0~1.0 float>, ...}, '
    '"unparsed_fields": [<field name you could not read>, ...]}\n'
    "confidence_map must have the same keys as parsed_fields."
)

# image 블록에 넣을 mime 매핑 (schema file_type → mime_type)
_IMAGE_MIME = {"image": "image/png"}  # 필요 시 jpg 등 세분화

# 협력사 문서 비공개 버킷 (서울). file_url 컬럼엔 이 버킷 안의 "키"가 저장된다.
#   예: "submissions/req-001/factory_cert.pdf"   (영구 URL이 아니라 키)
S3_BUCKET = "kira-documents-423937245947-ap-northeast-2-an"
AWS_REGION = "ap-northeast-2"

# boto3 client는 스레드 안전하므로 모듈 레벨에서 1회 생성해 재사용한다.
# 자격증명은 EC2 IAM Role이 자동 주입 — 키를 넘기지 않는다.
_s3_client = boto3.client("s3", region_name=AWS_REGION)

def _get_object_sync(key: str) -> bytes:
    """boto3 get_object (동기). to_thread로 감싸 호출한다."""
    resp = _s3_client.get_object(Bucket=S3_BUCKET, Key=key)
    return resp["Body"].read()


async def _load_document_bytes(s3_key: str) -> bytes:
    """
    S3 비공개 버킷에서 문서 바이트를 읽어온다.
    동기 boto3 호출이라 asyncio.to_thread로 감싸 이벤트 루프를 막지 않는다.
    s3_key: submission_documents.file_url에 저장된 버킷 내 키.
    """
    try:
        return await asyncio.to_thread(_get_object_sync, s3_key)
    except ClientError as exc:
        # 없는 키/권한 문제 등. 추측으로 채우지 않고 호출부가 미파싱 처리하도록 올린다.
        raise FileNotFoundError(f"S3 object load failed (key={s3_key}): {exc}") from exc


@trace_tool("parse_document")
async def parse_document(document_id: str, db: AsyncSession) -> dict:
    """
    문서 한 개를 Bedrock(Sonnet 4.6 + Vision)으로 읽어 정형 데이터로 추출하고,
    document_extraction_results에 적재한다. db를 인자로 받으므로 audit_trail에 기록된다.
    반환: {"parsed_fields": {...}, "confidence_map": {...}, "unparsed_fields": [...]}
    """
    # ── 1) 원본 문서 메타 조회 (schema: file_url / file_name / file_type) ──────
    row = await db.execute(
        text(
            """
            SELECT request_id, file_url, file_name, file_type
            FROM submission_documents
            WHERE document_id = :document_id
            """
        ),
        {"document_id": document_id},
    )
    doc = row.first()
    if doc is None:
        return {"parsed_fields": {}, "confidence_map": {},
                "unparsed_fields": ["document_not_found"]}
    request_id, file_url, file_name, file_type = doc

    # ── 2) 파일 바이트 확보 → base64 ──────────────────────────────────────────
    # file_url 컬럼엔 S3 키가 저장돼 있다 (영구 URL 아님). 그 키로 바이트를 읽는다.
    raw_bytes = await _load_document_bytes(file_url)        # ← 저장 방식 확정 후 구현
    b64 = base64.b64encode(raw_bytes).decode("utf-8")

    # ── 3) 파일 타입별 content 블록 구성 ──────────────────────────────────────
    if file_type == "image":
        # 검증된 멀티모달 형식: text + base64 image (mime_type 필수)
        doc_block = {"type": "image", "base64": b64, "mime_type": _IMAGE_MIME["image"]}
    elif file_type == "pdf":
        # 주의: ChatBedrockConverse의 base64 PDF(document) 지원이 버전마다 다르다.
        #       Converse document 블록 형식이 확정되면 여기에 넣는다.
        #       당장 안 되면 PDF→이미지 변환(pdf2image) 후 image 블록으로 우회.
        #       (PDF 파싱 라이브러리 검토는 W3 B 과제 — 그 결과로 이 분기를 확정한다.)
        doc_block = {"type": "image", "base64": b64, "mime_type": "image/png"}  # 임시: 변환 전제
    else:
        # xlsx/csv/docx 등은 Vision 대상이 아니다. 텍스트 추출 경로가 따로 필요.
        # 추측해서 이미지로 보내면 깨지므로, 일단 미파싱으로 표시해 넘긴다.
        return {"parsed_fields": {}, "confidence_map": {},
                "unparsed_fields": [f"unsupported_for_vision:{file_type}"]}

    # ── 4) Bedrock 호출 (은진 = Sonnet 4.6, IAM Role 인증, temperature 0) ──────
    llm = get_llm_for_agent("data_gateway")
    messages = [
        SystemMessage(content=_EXTRACTION_SYSTEM),
        HumanMessage(content=[
            {"type": "text",
             "text": f"Extract all compliance-relevant fields from this document "
                     f"(filename: {file_name})."},
            doc_block,
        ]),
    ]
    resp = await llm.ainvoke(messages)

    # ── 5) 응답 JSON 안전 파싱 (모델이 펜스를 붙이면 제거) ─────────────────────
    text_out = resp.content if isinstance(resp.content, str) else str(resp.content)
    cleaned = text_out.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        extracted = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        # 모델이 JSON을 안 지키면 추측으로 채우지 않고 미파싱으로 표시
        extracted = {"parsed_fields": {}, "confidence_map": {},
                     "unparsed_fields": ["llm_non_json_response"]}

    parsed_fields = extracted.get("parsed_fields", {})
    confidence_map = extracted.get("confidence_map", {})
    unparsed_fields = extracted.get("unparsed_fields", [])

    # ── 6) document_extraction_results 적재 (submission repository 위임) ──────
    #   JSONB 컬럼이라 dict/list를 그대로 넘긴다 (json.dumps로 문자열화하면
    #   이중 직렬화돼서 "{...}" 문자열이 박힌다 — 넘기지 않는다).
    #   request_id는 submission_documents에서 읽은 UUID 그대로 (str 변환 불필요).
    await submission_repo.create_extraction_result(
        db,
        request_id=request_id,
        document_id=document_id,
        parsed_fields=parsed_fields,
        confidence_map=confidence_map,
        unparsed_fields=unparsed_fields,
    )
    await db.commit()   # 노드(도구)가 트랜잭션 경계 소유 — repository는 flush까지만
    
    
    return {"parsed_fields": parsed_fields,
            "confidence_map": confidence_map,
            "unparsed_fields": unparsed_fields}


# ── validate_schema (spec 3-5 핵심 함수) ──────────────────────────────────
 
# 단위 정규화 맵 (spec 예시: kgCO2/kg → kgCO2eq/kg). 필요 시 확장.
_UNIT_NORMALIZE = {
    "kgCO2/kg": "kgCO2eq/kg",
}
 
 
async def validate_schema(parsed: dict, provider_type: str) -> ValidationResult:
    """
    onboarding_data_requirements(provider_type)의 required_fields를 조회해
    필수 필드 누락 여부를 검사하고 단위를 정규화한다. (spec 3-5)
 
    * schema 컬럼명은 provider_type (spec 본문의 supplier_type과 동의어).
    * required_fields는 JSONB 리스트로 가정(["carbon_intensity", ...]).
    """
    async with AsyncSessionLocal() as db:
        row = await db.execute(
            text(
                """
                SELECT required_fields
                FROM onboarding_data_requirements
                WHERE provider_type = :ptype
                """
            ),
            {"ptype": provider_type},
        )
        rec = row.first()
 
    # required_fields JSONB: 리스트(["f1","f2"]) 또는 dict({"f1":...}) 둘 다 방어.
    # (seed 데이터 미확정 — 스키마는 JSONB로만 정의됨. 키 목록만 필요하므로 정규화.)
    raw = (rec[0] if rec and rec[0] else []) or []
    required = list(raw.keys()) if isinstance(raw, dict) else list(raw)
    present = set(parsed.keys())
    missing = [f for f in required if f not in present]
 
    # 단위 정규화: 값이 "<num> <unit>" 형태면 unit만 표준으로 치환.
    normalized = {}
    for k, v in parsed.items():
        if isinstance(v, str):
            for old, new in _UNIT_NORMALIZE.items():
                if v.endswith(old):
                    v = v[: -len(old)] + new
        normalized[k] = v
 
    return ValidationResult(ok=(len(missing) == 0), missing_fields=missing, normalized=normalized)

@trace_node("data_gateway", "agent")
async def data_gateway_node(state: BatchState) -> BatchState:
    """
    batch에 연관된 문서 추출결과(document_extraction_results)를 모아
    신뢰도/스키마를 검증하고 분기한다. 파싱은 하지 않는다(워커 몫).
 
    분기:
      - 추출결과 중 신뢰도 < 0.85 또는 스키마 누락 또는 미확인(supplier_confirmed=False)
        → error_reason="low_confidence" → supervisor가 supplier_reverify로 라우팅
      - 모두 통과 → error_reason=None → 다음 단계(verification)
    """
    product_id = state.get("product_id")
    if product_id is None:
        return {
            **state,
            "current_stage": "stage_extraction",
            "error_reason": "low_confidence",
            "confidence_score": 0.0,
            "extraction_result": {"checked": False, "note": "no product_id in state"},
        }
 
    async with AsyncSessionLocal() as db:
        # (1) product_id → 공급망 트리 → 공급사 목록 (D 제공 조회)
        sc_repo = SupplyChainRepository(db)
        rows = await sc_repo.get_n_tier_supply_chain(str(product_id))
        # 반환은 flat List[Dict]. 공급사는 child_supplier_id 키에 들어온다(중복 제거).
        supplier_ids = list({
            r["child_supplier_id"]
            for r in rows
            if r.get("child_supplier_id")
        })
 
        # (2) 그 공급사들의 추출결과를 모은다 (E 제공 조회)
        results = await submission_repo.list_extraction_results_by_suppliers(
            db, [UUID(str(s)) for s in supplier_ids]
        )
 
    # (3) 집계: 최저 신뢰도 + 미확인/누락 검사
    if not results:
        # 모을 추출결과가 없음 — 추측 금지, 사람에게 넘긴다.
        return {
            **state,
            "current_stage": "stage_extraction",
            "error_reason": "low_confidence",
            "confidence_score": 0.0,
            "extraction_result": {"checked": True, "count": 0, "note": "no extraction results"},
        }
 
    lowest = 1.0
    unconfirmed = 0
    has_missing = False
    for r, supplier_type in results:
        cmap = r.confidence_map or {}
        if cmap:
            lowest = min(lowest, min(cmap.values()))
        if not r.supplier_confirmed:
            unconfirmed += 1
        # 스키마 누락 검사 (spec 노드 정의: "validate_schema를 통한 누락 필드 검사").
        # supplier_type(=provider_type)으로 onboarding_data_requirements를 조회한다.
        if supplier_type:
            vr = await validate_schema(r.parsed_fields or {}, supplier_type)
            if not vr.ok:
                has_missing = True
 
    low_conf = lowest < CONFIDENCE_THRESHOLD or unconfirmed > 0 or has_missing
    error_reason = "low_confidence" if low_conf else None
 
    return {
        **state,
        "current_stage": "stage_extraction",
        "confidence_score": lowest,
        "error_reason": error_reason,
        "extraction_result": {
            "checked": True,
            "count": len(results),
            "lowest_confidence": lowest,
            "unconfirmed": unconfirmed,
            "has_missing": has_missing,
        },
    }