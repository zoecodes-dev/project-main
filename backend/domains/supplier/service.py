"""
domains/supplier/service.py  (담당: 팀원 B)

★ W2 "이벤트 발행 레퍼런스". 다른 도메인(product/submission/...)이 이 패턴을 복사한다.

레이어 규칙 (PROJECT_CORE 5-1):
  router → service → repository → models  (단방향)
  - service는 비즈니스 로직 + 이벤트 발행만. 직접 SQL 금지(그건 repository).
  - 다른 도메인을 import하지 않는다. 통신은 events/types.py 이벤트 + publish()로만.

이벤트 발행 규칙 (PROJECT_CORE 5-2):
  - publish(event_name, payload)  ← 2-인자. db를 넘기지 않는다.
  - payload는 dataclasses.asdict(이벤트객체)로 만든 dict.
  - ★ 발행은 "DB 커밋이 성공한 뒤"에 한다.
"""
import dataclasses
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend.domains.supplier import repository
from backend.domains.supplier.models import (
    MasterFormRequest,
    Supplier,
    SupplierOnboarding,
    SupplierRiskProfile,
)
from backend.events.types import (
    RiskProfileUpdatedEvent,
    SupplierInvitedEvent,
    SupplierDocumentUploadedEvent,
)
from backend.infrastructure.event_bus import publish
from backend.infrastructure.trace import trace_node
# AP: 추출결과 read는 E 제공(submission repository), 마스터폼 prefill 변환은 B(supplier)
#   masterform_prefill. 둘 다 무거운 LLM 스택을 끌어오지 않는 가벼운 호출이다.
from backend.domains.submission import repository as submission_repo
from backend.domains.supplier import masterform_prefill

# ── 정책 상수 ───────────────────────────────────────────────
# 협력사 온보딩 SLA. PROJECT_CORE: 14일 미응답 → Reminder, 21일 → Escalation.
SUPPLIER_SLA_DAYS = 14


async def create_supplier_and_invite(
    db: AsyncSession,
    supplier_data: dict,
    email: str,
    inviter_supplier_id: Optional[UUID] = None,
) -> Supplier:
    """
    협력사를 생성하고 초대 이벤트를 발행한다. (B-9 완료기준: CTI/onboarding/SLA/발행을
    단일 트랜잭션으로)

    [G1 협력사→협력사 초대 — contract-first 착수]
      inviter_supplier_id: 이 협력사를 '초대한' 상위 협력사(이동 주체). 원청 직접
      등록이면 None. 값이 있으면 SupplierInvited 이벤트에 실어 발행하고, D(supplychain)
      가 이를 수신해 supply_chain_map.discovered_via 에 기록한다(빈 상태→pool 구축).
      ※ 도메인 경계: B는 supply_chain_map에 직접 쓰지 않는다(D 도메인). 이벤트로만 전달.

    ★ 이벤트 발행 순서 (다른 도메인이 그대로 따른다):
      1) repository로 DB 변경  2) await db.commit()  3) 커밋 성공 후에만 publish()
      이유: publish()는 별도 커넥션으로 NOTIFY를 즉시 보낸다. 커밋 전 발행하면
      롤백 시 "이벤트는 나갔는데 데이터는 없는" 불일치가 생긴다.

    ※ @trace_node 미적용: audit_trail은 batch_id 기반 해시 체인인데, 협력사 생성은
      AI 배치 파이프라인 '밖'이라 batch_id가 없다.
    """
    # 1) 협력사 INSERT
    supplier = await repository.create_supplier(db, supplier_data)

    # 1-b) 기본 리스크 프로필 초기화(조회 시 값 없음 방지)
    db.add(SupplierRiskProfile(
        supplier_id=supplier.supplier_id,
        overall_risk_score=0,
        risk_level="low",
        feoc_status="unknown",
        is_high_risk_flag=False,
    ))

    # 1-c) 온보딩 row 동시 생성 + SLA 마감 자동 설정 (B-9: 등록과 동시에 sla_due_date)
    sla_due_date = datetime.now(timezone.utc) + timedelta(days=SUPPLIER_SLA_DAYS)
    db.add(SupplierOnboarding(
        supplier_id=supplier.supplier_id,
        consent_status="consent_pending",
        agreement_status="pending",
        last_invited_at=datetime.now(timezone.utc),
        sla_due_date=sla_due_date,
        reminder_count=0,
    ))

    # 2) 커밋 — 영속화 확정 (repository는 flush만, 커밋은 service 책임)
    await db.commit()
    await db.refresh(supplier)

    # 3) 커밋 성공 후 이벤트 발행 (G1: inviter 동봉 → D가 discovered_via 기록)
    event = SupplierInvitedEvent(
        supplier_id=supplier.supplier_id,
        email=email,
        sla_due_date=sla_due_date,
        inviter_supplier_id=inviter_supplier_id,
    )
    await publish("SupplierInvited", dataclasses.asdict(event))

    return supplier


async def get_supplier(
    db: AsyncSession, supplier_id: UUID, tenant_id: Optional[UUID] = None
) -> Optional[Supplier]:
    """단건 조회. 비즈니스 로직 없이 repository에 위임. tenant_id 지정 시 소유 테넌트만(§0.2)."""
    return await repository.get_supplier_by_id(db, supplier_id, tenant_id)


async def supplier_in_tenant(
    db: AsyncSession, supplier_id: UUID, tenant_id: Optional[UUID]
) -> bool:
    """소유권 게이트(§0.2). 하위 리소스 조회 전 라우터가 호출. repository에 위임."""
    return await repository.supplier_in_tenant(db, supplier_id, tenant_id)


async def submit_master_form(
    db: AsyncSession, supplier_id: UUID, form: MasterFormRequest
) -> Optional[dict]:
    """
    마스터폼(표준화된 단일 입력양식) 제출 진입점 — POST /suppliers/{id}/master-form. (§4)

    협력사가 보는 '하나의 양식'을 통째로 받아, service가 섹션별로 쪼개 각 도메인의
    write 함수를 호출하고 ★단일 트랜잭션으로 commit(atomic)★ 한다. 한 섹션이라도
    실패하면 commit에 도달하지 않고 전체 롤백된다(부분 저장 금지).

    섹션 → 저장 책임:
      0 회사·공장·PIC      B (repository.write_master_form_*)
      1 탄소발자국         B (manufacturer_details + factory_carbon_declarations)

    반환: 저장된 섹션 키 목록을 담은 dict. 없는 supplier_id면 None(→ router 404).
    """
    # 존재 확인 — 없는 supplier_id로 분배 저장(FK 위반 직전까지 진행) 방지.
    if await repository.get_supplier_by_id(db, supplier_id) is None:
        return None

    sections_saved: List[str] = []
    try:
        # ── 섹션 0: 회사·공장·PIC (B) — 공장 먼저 생성해 factory_ids 확보 ──────
        await repository.write_master_form_company(db, supplier_id, form.company)
        sections_saved.append("company")

        factory_ids = await repository.write_master_form_factories(db, supplier_id, form.factories)
        if form.factories:
            sections_saved.append("factories")

        await repository.write_master_form_contacts(db, supplier_id, form.contacts)
        if form.contacts:
            sections_saved.append("contacts")

        # ── 섹션 1: 탄소발자국 (B) — 탄소선언이 factory_ids를 FK로 참조 ────────
        if form.manufacturing is not None:
            await repository.write_master_form_manufacturing(
                db, supplier_id, factory_ids, form.manufacturing
            )
            sections_saved.append("manufacturing")

        # ── 규제: 실사 자가진단 결과 → supplier_risk_profiles.self_reported_risk_level ──
        if form.self_reported_risk_level is not None:
            await repository.set_self_reported_risk_level(
                db, supplier_id, form.self_reported_risk_level
            )
            sections_saved.append("self_assessment")

        # ── 단일 커밋 (atomic) — 여기 도달해야만 영속화 ───────────────────────
        await db.commit()
    except Exception:
        # 한 섹션이라도 실패하면 전체 롤백(부분 저장 방지). 원인은 그대로 올린다.
        await db.rollback()
        raise

    return {
        "supplier_id": supplier_id,
        "status": "submitted",
        "sections_saved": sections_saved,
    }


async def get_master_form_prefill(db: AsyncSession, supplier_id: UUID) -> Optional[dict]:
    """
    AP(AI 자동 채움): 협력사 보완 문서의 추출결과를 모아 마스터폼 prefill 초안을 만든다.

    경로: 협력사 문서 업로드 → (E enqueue) document_parse_worker → parse_document
      (마스터폼 필드 인식형 추출) → document_extraction_results 적재 → 이 함수가
      supplier의 추출결과를 모아 마스터폼 섹션 구조로 되돌린다.

    여러 문서에 같은 필드가 있으면 '신뢰도 높은 값'을 채택한다. 신뢰도 임계치 미만
    필드는 prefill에 채우되 low_confidence_fields로 함께 반환해 협력사 확인을 유도한다.

    반환: prefill 초안 dict. 없는 supplier_id면 None(→ router 404).
          추출결과가 0건이면 prefill은 비고 document_count=0(업로드 전 정상 상태).
    """
    if await repository.get_supplier_by_id(db, supplier_id) is None:
        return None

    results = await submission_repo.list_extraction_results_by_suppliers(db, [supplier_id])

    merged_fields: dict = {}
    merged_conf: dict = {}
    unconfirmed = 0
    for record, _provider_type in results:
        parsed = record.parsed_fields or {}
        cmap = record.confidence_map or {}
        for key, value in parsed.items():
            try:
                conf = float(cmap.get(key, 0.0))
            except (TypeError, ValueError):
                conf = 0.0
            # 같은 필드가 여러 문서에 → 더 높은 신뢰도 값으로 갱신(최선값 채택).
            if key not in merged_conf or conf > merged_conf[key]:
                merged_fields[key] = value
                merged_conf[key] = conf
        if not record.supplier_confirmed:
            unconfirmed += 1

    assembled = masterform_prefill.to_master_form_prefill(merged_fields, merged_conf)
    return {
        "supplier_id": supplier_id,
        "document_count": len(results),
        "unconfirmed_documents": unconfirmed,
        "prefill": assembled["prefill"],
        "low_confidence_fields": assembled["low_confidence_fields"],
    }


# 원청(OEM, tier0) 노드 — manufacturer지만 CTI 수집 대상 아님 → 점검 예외.
_OEM_SUPPLIER_ID = UUID("a0000000-0000-4000-8000-000000000000")

# provider_type → 채워야 할 CTI relationship 속성명 매핑
_CTI_ATTR_BY_TYPE = {
    "manufacturer": "manufacturer_detail",
    "miner": "miner_detail",
}

# 국가명 → ISO 3166-1 alpha-2. suppliers.country 는 VARCHAR(2)라, 입력 경로(AI 파싱
# 배선·자료제출·회원가입)가 '대한민국'·'China'·'대한민국 (KR)' 같은 자유 표기를 보내면
# 영속화 직전에 코드로 정규화한다(_normalize_country_to_iso2). 국가 추가는 여기 한 줄.
_COUNTRY_NAME_TO_ISO2 = {
    "대한민국": "KR", "한국": "KR", "southkorea": "KR", "korea": "KR", "republicofkorea": "KR",
    "중국": "CN", "china": "CN", "prchina": "CN", "peoplesrepublicofchina": "CN",
    "미국": "US", "usa": "US", "us": "US", "unitedstates": "US",
    "unitedstatesofamerica": "US", "america": "US",
    "일본": "JP", "japan": "JP",
    "호주": "AU", "australia": "AU",
    "칠레": "CL", "chile": "CL",
    "콩고민주공화국": "CD", "콩고민주": "CD", "콩고": "CD",
    "drc": "CD", "democraticrepublicofthecongo": "CD", "congo": "CD",
    "인도네시아": "ID", "indonesia": "ID",
    "필리핀": "PH", "philippines": "PH",
    "베트남": "VN", "vietnam": "VN",
    "캐나다": "CA", "canada": "CA",
    "아르헨티나": "AR", "argentina": "AR",
    "볼리비아": "BO", "bolivia": "BO",
    "페루": "PE", "peru": "PE",
    "독일": "DE", "germany": "DE", "deutschland": "DE",
    "프랑스": "FR", "france": "FR",
    "영국": "GB", "uk": "GB", "unitedkingdom": "GB", "britain": "GB",
    "폴란드": "PL", "poland": "PL",
    "핀란드": "FI", "finland": "FI",
    "스웨덴": "SE", "sweden": "SE",
    "노르웨이": "NO", "norway": "NO",
    "모로코": "MA", "morocco": "MA",
    "남아프리카공화국": "ZA", "남아공": "ZA", "southafrica": "ZA",
    "짐바브웨": "ZW", "zimbabwe": "ZW",
    "브라질": "BR", "brazil": "BR",
    "멕시코": "MX", "mexico": "MX",
    "인도": "IN", "india": "IN",
    "대만": "TW", "taiwan": "TW",
}
_KNOWN_ISO2 = set(_COUNTRY_NAME_TO_ISO2.values())


def _normalize_country_to_iso2(value: Optional[str]) -> Optional[str]:
    """국가 표기 → ISO 3166-1 alpha-2. 코드면 그대로(대문자), '대한민국 (KR)'면 괄호 코드
    우선, 한/영 국가명이면 매핑. 해석 불가하면 None."""
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    # 괄호 안 alpha-2 우선: "대한민국 (KR)" → KR
    start, end = raw.find("("), raw.find(")")
    if start != -1 and end == start + 3:
        code = raw[start + 1:end].upper()
        if code.isalpha() and code in _KNOWN_ISO2:
            return code
    # 입력 자체가 alpha-2 코드
    if len(raw) == 2 and raw.isalpha() and raw.upper() in _KNOWN_ISO2:
        return raw.upper()
    # 이름 매핑(소문자·공백/구두점 제거)
    key = "".join(ch for ch in raw.lower() if ch not in " \t.()")
    return _COUNTRY_NAME_TO_ISO2.get(key)


async def update_supplier_detail(
    db: AsyncSession, supplier_id: UUID, tenant_id: Optional[UUID], fields: dict
) -> Optional[Supplier]:
    """
    협력사 '자료 제출' — 기업 기본정보 수정. 소유 테넌트만(§0.2, 아니면 None→404).
    repository로 변경 후 여기서 커밋(서비스 일원화). 갱신된 상세를 반환한다.
    """
    supplier = await repository.get_supplier_by_id(db, supplier_id, tenant_id)
    if supplier is None:
        return None
    # 필요문서 URL 컬럼의 '커밋 전' 값을 미리 떠둔다 — 새 S3 키로 바뀐 것만
    # 커밋 후 SupplierDocumentUploaded로 발행해 파싱 파이프라인을 태우기 위함.
    # (필드명 → 이벤트 doc_kind 코드)
    doc_url_kinds = {
        "business_reg_doc_url": "business_reg",
        "environmental_report_url": "environmental_report",
        "self_assessment_doc_url": "self_assessment",
    }
    prev_doc_urls = {col: getattr(supplier, col, None) for col in doc_url_kinds}
    # 입력 양식 영속화 — 테이블별로 분배(보낸 필드만).
    fields = dict(fields)
    # 국가 정규화 — suppliers.country 는 VARCHAR(2)(alpha-2). 자유 표기('대한민국' 등)를
    # 코드로 변환. 해석 불가하면 country를 빼서(=쓰지 않음) 잘못된 값으로 덮어쓰지 않는다.
    if "country" in fields:
        iso = _normalize_country_to_iso2(fields.get("country"))
        if iso:
            fields["country"] = iso
        else:
            fields.pop("country")
    manuf = {k: fields.pop(k) for k in ("carbon_intensity", "energy_source") if k in fields}
    self_risk = fields.pop("self_reported_risk_level", None)
    if fields:                         # 나머지는 suppliers 컬럼(core_minerals 포함)
        await repository.update_supplier_fields(db, supplier_id, fields)
    if manuf:                          # 탄소발자국 → manufacturer_details
        await repository.upsert_manufacturer_fields(db, supplier_id, manuf)
    if self_risk is not None:          # 실사 자가진단 → risk_profiles
        await repository.set_self_reported_risk_level(db, supplier_id, self_risk)
    await db.commit()

    # ── 커밋 성공 후 발행 (PROJECT_CORE 5-2) ──────────────────────────────
    # 새로 들어온(이전 값과 다른) 비어있지 않은 필요문서 S3 키만 발행 → 중복 파싱 방지.
    for col, kind in doc_url_kinds.items():
        new_val = fields.get(col)
        if new_val and new_val != prev_doc_urls.get(col):
            await publish(
                "SupplierDocumentUploaded",
                dataclasses.asdict(SupplierDocumentUploadedEvent(
                    supplier_id=supplier_id,
                    s3_key=new_val,
                    file_name=new_val.rsplit("/", 1)[-1] if "/" in new_val else new_val,
                    doc_kind=kind,
                )),
            )

    return await get_supplier_detail(db, supplier_id, tenant_id)


async def get_supplier_detail(
    db: AsyncSession, supplier_id: UUID, tenant_id: Optional[UUID] = None
) -> Optional[Supplier]:
    """
    단건 상세 조회 (CTI 상세 포함). repository가 selectinload로 4종 CTI를 미리 로드한다.
    [목요일 연결 점검] provider type과 실제 적재된 CTI가 불일치하면 경고 로그를 남긴다
    (예: provider_type='manufacturer'인데 manufacturer_detail이 없음 = 자료 미수집).
    엣지 케이스를 삼키지 않고 드러내기 위한 점검이며, 응답 자체는 정상 반환한다.
    """
    supplier = await repository.get_supplier_by_id(db, supplier_id, tenant_id)
    if supplier is None:
        return None

    expected_attr = _CTI_ATTR_BY_TYPE.get(supplier.provider_type)
    if supplier_id != _OEM_SUPPLIER_ID and expected_attr is not None and getattr(supplier, expected_attr, None) is None:
        print(
            f"[CTI 점검] supplier {supplier_id} type={supplier.provider_type} "
            f"이지만 {expected_attr} 미적재 (자료 미수집 가능)"
        )
    return supplier


async def list_suppliers(
    db: AsyncSession,
    status: Optional[str] = None,
    risk_level: Optional[str] = None,
    feoc_status: Optional[str] = None,
    page: int = 1,
    size: int = 20,
    tenant_id: Optional[UUID] = None,
) -> List[Supplier]:
    """목록 조회(필터 + 페이지네이션). tenant_id 지정 시 소유 테넌트만(§0.2)."""
    return await repository.get_suppliers(
        db, status, risk_level, feoc_status, page, size, tenant_id
    )


async def count_suppliers(
    db: AsyncSession,
    status: Optional[str] = None,
    risk_level: Optional[str] = None,
    feoc_status: Optional[str] = None,
    tenant_id: Optional[UUID] = None,
) -> int:
    """목록 전체 건수(필터 동일, 페이지 무관). X-Total-Count 헤더용(§0.6)."""
    return await repository.count_suppliers(
        db, status, risk_level, feoc_status, tenant_id
    )


def _score_to_risk_level(score: int) -> str:
    """
    overall_risk_score 구간을 risk_level로 변환 (가점식, 높을수록 위험).
    schema.sql / 3-4절 기준: 0~29 low / 30~49 medium / 50~69 high / 70~100 critical.
    """
    if score >= 70:
        return "critical"
    if score >= 50:
        return "high"
    if score >= 30:
        return "medium"
    return "low"


@trace_node(node_name="upsert_risk_score", node_type="agent")
async def upsert_risk_score(
    supplier_id: UUID,
    score: int,
    db: AsyncSession,
    batch_id: str = None,
) -> SupplierRiskProfile:
    """
    Verification/Risk Domain(E)에서 검증 완료 시 호출.
    1) supplier_risk_profiles upsert  2) overall_risk_score → risk_level 자동 재계산
    3) suppliers.risk_level 비정규화 캐시 동기화  4) 커밋  5) RiskProfileUpdated 발행
    """
    score = max(0, min(100, score))   # 0~100 클램프
    new_level = _score_to_risk_level(score)

    profile = await repository.upsert_risk_profile(
        db,
        supplier_id=supplier_id,
        overall_risk_score=score,
        risk_level=new_level,
        last_risk_review_at=datetime.now(timezone.utc),
    )
    await repository.update_supplier_risk_level(db, supplier_id, new_level)

    await db.commit()
    await db.refresh(profile)

    event = RiskProfileUpdatedEvent(
        supplier_id=supplier_id,
        overall_risk_score=score,
        risk_level=new_level,
    )
    await publish("RiskProfileUpdated", dataclasses.asdict(event))

    return profile


async def get_risk_profile(supplier_id: UUID, db: AsyncSession) -> SupplierRiskProfile | None:
    """supplier_risk_profiles 단건 조회 (하위 3개 JOIN은 W4 종합 스코어링에서 확장)."""
    return await repository.get_risk_profile_by_supplier(db, supplier_id)


# ============================================================
# BE-3: 7탭 모달 조회 (기존 테이블 SELECT 전용)
#   존재하지 않는 협력사면 None을 반환 → router가 404로 매핑.
# ============================================================
async def get_factories(db: AsyncSession, supplier_id: UUID) -> Optional[dict]:
    """사업장 탭 — 공장/광산 목록(좌표 lat/lng 포함)."""
    if await repository.get_supplier_by_id(db, supplier_id) is None:
        return None
    return {
        "supplier_id": supplier_id,
        "factories": await repository.get_factories(db, supplier_id),
    }


async def get_contacts(db: AsyncSession, supplier_id: UUID) -> Optional[dict]:
    """담당자 연락처 탭 — supplier_contacts 목록(대표 우선)."""
    if await repository.get_supplier_by_id(db, supplier_id) is None:
        return None
    return {
        "supplier_id": supplier_id,
        "contacts": await repository.get_contacts(db, supplier_id),
    }


async def get_completeness(db: AsyncSession, supplier_id: UUID) -> Optional[dict]:
    """입력 완성도 — data_completeness_status 단건. 협력사 없으면 None,
    집계 전이면 빈 기본값으로 안전 반환."""
    if await repository.get_supplier_by_id(db, supplier_id) is None:
        return None
    comp = await repository.get_completeness(db, supplier_id)
    if comp is None:
        comp = {
            "required_field_count": None,
            "filled_field_count": None,
            "completion_rate": None,
            "missing_fields": [],
            "last_updated_at": None,
        }
    return {"supplier_id": supplier_id, **comp}


async def get_supplied_items(db: AsyncSession, supplier_id: UUID) -> Optional[dict]:
    """공급 품목 — 이 협력사가 공급망 맵에서 공급하는 부품 distinct."""
    if await repository.get_supplier_by_id(db, supplier_id) is None:
        return None
    return {
        "supplier_id": supplier_id,
        "items": await repository.get_supplied_items(db, supplier_id),
    }


async def get_carbon_declarations(db: AsyncSession, supplier_id: UUID) -> Optional[dict]:
    """환경성적서(탄소발자국) — 공장별 factory_carbon_declarations. 최종 검증(STEP4) 핵심 자료."""
    if await repository.get_supplier_by_id(db, supplier_id) is None:
        return None
    return {
        "supplier_id": supplier_id,
        "declarations": await repository.get_carbon_declarations(db, supplier_id),
    }


async def get_reliability(db: AsyncSession, supplier_id: UUID) -> Optional[dict]:
    """
    Reliability(신뢰도) 탭 — 완성도 + 리스크 프로필 + 온보딩 SLA + 실사 요약.
    리스크 프로필/온보딩이 아직 없을 수 있으므로 각 필드는 안전하게 None fallback.
    """
    supplier = await repository.get_supplier_by_id(db, supplier_id)
    if supplier is None:
        return None

    profile = await repository.get_risk_profile_by_supplier(db, supplier_id)
    onboarding = await repository.get_onboarding_by_supplier(db, supplier_id)
    audits = await repository.get_audit_records(db, supplier_id)
    # 실사 기록은 audit_date 내림차순 → 첫 행이 최신.
    latest_audit = audits[0] if audits else None

    return {
        "supplier_id": supplier_id,
        "completeness_score": supplier.completeness_score,
        "overall_risk_score": profile.overall_risk_score if profile else None,
        "risk_level": profile.risk_level if profile else None,
        "feoc_status": profile.feoc_status if profile else None,
        "is_high_risk_flag": profile.is_high_risk_flag if profile else None,
        "last_risk_review_at": profile.last_risk_review_at if profile else None,
        "consent_status": onboarding.consent_status if onboarding else None,
        "agreement_status": onboarding.agreement_status if onboarding else None,
        "sla_due_date": onboarding.sla_due_date if onboarding else None,
        "reminder_count": onboarding.reminder_count if onboarding else None,
        "last_reminded_at": onboarding.last_reminded_at if onboarding else None,
        "total_audits": len(audits),
        "last_audit_date": latest_audit.audit_date if latest_audit else None,
        "last_audit_result": latest_audit.result if latest_audit else None,
    }