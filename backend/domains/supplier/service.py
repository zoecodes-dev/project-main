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
from backend.events.types import RiskProfileUpdatedEvent, SupplierInvitedEvent
from backend.infrastructure.event_bus import publish
from backend.infrastructure.trace import trace_node
# 마스터폼 섹션 4~6 write는 E가 제공(submission/masterform.py). B의 오케스트레이터가
# '동일 db 세션'으로 호출해 단일 트랜잭션 atomic 묶음으로 commit한다(§4). 도메인 코드를
# 대신 구현하는 게 아니라 제공된 계약 함수를 호출만 한다.
from backend.domains.submission import masterform as e_masterform
# AP: 추출결과 read는 E 제공(submission repository), 마스터폼 prefill 변환은 B(supplier)
#   masterform_prefill. 둘 다 무거운 LLM 스택을 끌어오지 않는 가벼운 호출이다.
from backend.domains.submission import repository as submission_repo
from backend.domains.supplier import masterform_prefill
# NOTE: 섹션 3 원산지 증명서 write(C: regulation.save_origin_certificates)는 섹션 3
#   블록 안에서 '지연(lazy) import'한다 — regulation.repository가 LLM 임베딩 스택
#   (langchain_aws)을 끌어오므로, 모듈 로드 시점에 가져오면 supplier.service import가
#   그 무거운 스택에 묶인다. 실제 호출 시점(런타임)에만 가져와 import를 가볍게 유지한다.

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


async def get_supplier(db: AsyncSession, supplier_id: UUID) -> Optional[Supplier]:
    """단건 조회. 비즈니스 로직 없이 repository에 위임."""
    return await repository.get_supplier_by_id(db, supplier_id)


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
      2 재활용             B (recycler_details · recycling_efficiency 포함)
      3 원산지·GPS         D GPS(miner_details) + C 원산지 증명서(save_origin_certificates, 지연 import)
      4 지분·FEOC          E (e_masterform.write_supplier_trader_details)
      5 인권·중대·교육     E (e_masterform.write_supplier_social)
      6 EoL·인증서         E (e_masterform.write_supplier_certifications)

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

        # ── 섹션 2: 재활용 (B) ────────────────────────────────────────────────
        if form.recycling is not None:
            await repository.write_master_form_recycling(db, supplier_id, form.recycling)
            sections_saved.append("recycling")

        # ── 섹션 3: 원산지·GPS ────────────────────────────────────────────────
        #   GPS(supplier_miner_details)      = D 제공(repository.upsert_miner_details).
        #   원산지 증명서(origin_certificates) = C 제공(regulation.save_origin_certificates).
        #   PostGIS 좌표 변환(lng/lat swap)은 upsert_miner_details 내부가 담당한다.
        if form.origin is not None:
            origin = form.origin
            coords = origin.mine_coordinates
            await repository.upsert_miner_details(
                db,
                supplier_id,
                mine_name=origin.mine_name,
                mining_method=origin.mining_method,
                extraction_volume=origin.extraction_volume,
                lat=coords.latitude if coords else None,
                lng=coords.longitude if coords else None,
                active_period_from=origin.active_period_from,
                active_period_to=origin.active_period_to,
            )
            sections_saved.append("origin")
            # 원산지 증명서(C 제공) — 같은 db 세션으로 호출(commit은 이 service가 일괄).
            # 지연 import: regulation.repository가 LLM 임베딩 스택을 끌어오므로 호출 시점에만.
            if origin.origin_certificates:
                from backend.domains.regulation.service import save_origin_certificates
                await save_origin_certificates(
                    db=db,
                    supplier_id=str(supplier_id),
                    certificates=[c.model_dump() for c in origin.origin_certificates],
                )
                sections_saved.append("origin_certificates")

        # ── 섹션 4~6: E 제공 write 함수 호출 (동일 트랜잭션) ──────────────────
        if form.ownership is not None:
            await e_masterform.write_supplier_trader_details(db, supplier_id, form.ownership)
            sections_saved.append("ownership")
        if form.social is not None:
            await e_masterform.write_supplier_social(db, supplier_id, factory_ids, form.social)
            sections_saved.append("social")
        if form.certifications is not None:
            await e_masterform.write_supplier_certifications(db, supplier_id, form.certifications)
            sections_saved.append("certifications")

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
    for record, _supplier_type in results:
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

# supplier_type → 채워야 할 CTI relationship 속성명 매핑
_CTI_ATTR_BY_TYPE = {
    "manufacturer": "manufacturer_detail",
    "recycler": "recycler_detail",
    "trader": "trader_detail",
    "miner": "miner_detail",
}


async def get_supplier_detail(db: AsyncSession, supplier_id: UUID) -> Optional[Supplier]:
    """
    단건 상세 조회 (CTI 상세 포함). repository가 selectinload로 4종 CTI를 미리 로드한다.
    [목요일 연결 점검] provider type과 실제 적재된 CTI가 불일치하면 경고 로그를 남긴다
    (예: supplier_type='manufacturer'인데 manufacturer_detail이 없음 = 자료 미수집).
    엣지 케이스를 삼키지 않고 드러내기 위한 점검이며, 응답 자체는 정상 반환한다.
    """
    supplier = await repository.get_supplier_by_id(db, supplier_id)
    if supplier is None:
        return None

    expected_attr = _CTI_ATTR_BY_TYPE.get(supplier.supplier_type)
    if supplier_id != _OEM_SUPPLIER_ID and expected_attr is not None and getattr(supplier, expected_attr, None) is None:
        print(
            f"[CTI 점검] supplier {supplier_id} type={supplier.supplier_type} "
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
) -> List[Supplier]:
    """목록 조회(필터 + 페이지네이션)."""
    return await repository.get_suppliers(
        db, status, risk_level, feoc_status, page, size
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
async def get_esg(db: AsyncSession, supplier_id: UUID) -> Optional[dict]:
    """ESG 탭 — 인증서(E) + 인권 이슈/산업재해(S) + 실사 기록(G)을 묶어 반환."""
    if await repository.get_supplier_by_id(db, supplier_id) is None:
        return None
    return {
        "supplier_id": supplier_id,
        "certifications": await repository.get_certifications(db, supplier_id),
        "human_rights_issues": await repository.get_human_rights_issues(db, supplier_id),
        "industrial_accidents": await repository.get_industrial_accidents(db, supplier_id),
        "audit_records": await repository.get_audit_records(db, supplier_id),
    }


async def get_training(db: AsyncSession, supplier_id: UUID) -> Optional[dict]:
    """Training 탭 — 교육 이수 기록(교육 자료 메타 포함)."""
    if await repository.get_supplier_by_id(db, supplier_id) is None:
        return None
    return {
        "supplier_id": supplier_id,
        "records": await repository.get_training_records(db, supplier_id),
    }


async def get_factories(db: AsyncSession, supplier_id: UUID) -> Optional[dict]:
    """사업장 탭 — 공장/광산 목록(좌표 lat/lng 포함)."""
    if await repository.get_supplier_by_id(db, supplier_id) is None:
        return None
    return {
        "supplier_id": supplier_id,
        "factories": await repository.get_factories(db, supplier_id),
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