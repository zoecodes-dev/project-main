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
from backend.domains.supplier.models import Supplier, SupplierOnboarding, SupplierRiskProfile
from backend.events.types import RiskProfileUpdatedEvent, SupplierInvitedEvent
from backend.infrastructure.event_bus import publish
from backend.infrastructure.trace import trace_node

# ── 정책 상수 ───────────────────────────────────────────────
# 협력사 온보딩 SLA. PROJECT_CORE: 14일 미응답 → Reminder, 21일 → Escalation.
SUPPLIER_SLA_DAYS = 14


async def create_supplier_and_invite(
    db: AsyncSession,
    supplier_data: dict,
    email: str,
) -> Supplier:
    """
    협력사를 생성하고 초대 이벤트를 발행한다. (B-9 완료기준: CTI/onboarding/SLA/발행을
    단일 트랜잭션으로)

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

    # 3) 커밋 성공 후 이벤트 발행
    event = SupplierInvitedEvent(
        supplier_id=supplier.supplier_id,
        email=email,
        sla_due_date=sla_due_date,
    )
    await publish("SupplierInvited", dataclasses.asdict(event))

    return supplier


async def get_supplier(db: AsyncSession, supplier_id: UUID) -> Optional[Supplier]:
    """단건 조회. 비즈니스 로직 없이 repository에 위임."""
    return await repository.get_supplier_by_id(db, supplier_id)


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
    if expected_attr is not None and getattr(supplier, expected_attr, None) is None:
        print(
            f"[CTI 점검] supplier {supplier_id} type={supplier.supplier_type} "
            f"이지만 {expected_attr} 미적재 (자료 미수집 가능)"
        )
    return supplier


async def list_suppliers(
    db: AsyncSession,
    status: Optional[str] = None,
    tier: Optional[int] = None,
    risk_level: Optional[str] = None,
    feoc_status: Optional[str] = None,
    page: int = 1,
    size: int = 20,
) -> List[Supplier]:
    """목록 조회(필터 + 페이지네이션)."""
    return await repository.get_suppliers(
        db, status, tier, risk_level, feoc_status, page, size
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