"""
domains/supplier/service.py  (담당: 팀원 B)

★ 이 파일은 W2 "이벤트 발행 레퍼런스"다. 다른 도메인(product/submission/...)이
   이 패턴을 복사한다.

레이어 규칙 (PROJECT_CORE 5-1):
  router → service → repository → models  (단방향)
  - service는 비즈니스 로직 + 이벤트 발행만. 직접 SQL 금지(그건 repository).
  - 다른 도메인을 import하지 않는다. 통신은 events/types.py 이벤트 + publish()로만.

이벤트 발행 규칙 (PROJECT_CORE 5-2):
  - publish(event_name, payload)  ← 2-인자. db를 넘기지 않는다.
  - payload는 dataclasses.asdict(이벤트객체)로 만든 dict.
  - ★ 발행은 "DB 커밋이 성공한 뒤"에 한다. (아래 create_supplier_and_invite 참고)
"""
import dataclasses
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend.domains.supplier import repository
from backend.domains.supplier.models import Supplier, SupplierRiskProfile
from backend.events.types import RiskProfileUpdatedEvent, SupplierInvitedEvent
from backend.infrastructure.event_bus import publish
from backend.infrastructure.trace import trace_node

# ── 정책 상수 ───────────────────────────────────────────────
# 협력사 온보딩 SLA. PROJECT_CORE: 14일 미응답 → Reminder, 21일 → Escalation.
# 매직넘버를 코드에 박지 않고 상수로 둔다(레퍼런스 습관).
SUPPLIER_SLA_DAYS = 14


async def create_supplier_and_invite(
    db: AsyncSession,
    supplier_data: dict,
    email: str,
) -> Supplier:
    """
    협력사를 생성하고 초대 이벤트를 발행한다.

    ★ 이벤트 발행 순서 (다른 도메인이 이 순서를 그대로 따른다):
      1) repository로 DB 변경
      2) await db.commit()  ← 여기서 영속화가 확정된다
      3) 커밋이 성공한 뒤에만 publish()

      왜 이 순서인가:
      publish()는 별도 커넥션으로 NOTIFY를 즉시 보낸다(event_bus 참고). 만약 커밋
      전에 발행하면, 이후 트랜잭션이 롤백될 때 "이벤트는 나갔는데 데이터는 없는"
      불일치가 생긴다. 그래서 반드시 커밋 성공 후 발행한다.
      (트랜잭션과 발행을 한 단위로 묶는 완전한 outbox 패턴은 W2 후반 과제.)

    ※ trace 데코레이터: 이 함수에는 @trace_node를 붙이지 않는다.
      audit_trail은 batch_id 기반 해시 체인인데, 협력사 생성은 AI 배치 파이프라인
      '밖'의 동작이라 batch_id가 없다. batch_id가 있는 파이프라인 노드 함수에는
      반드시 @trace_node를 붙일 것. (batch 밖 동작의 감사 기록 정책은 W2에서 확정)
    """
    # 1) DB 변경
    supplier = await repository.create_supplier(db, supplier_data)

    # 기본 리스크 프로필 함께 초기화
    # 협력사 생성 시점에 빈 프로필을 미리 만들어 두어 조회 시 값이 없는 현상을 방지합니다.
    default_profile = SupplierRiskProfile(
        supplier_id=supplier.supplier_id,
        overall_risk_score=0,
        risk_level="low",
        feoc_status="unknown",
        is_high_risk_flag=False
    )
    db.add(default_profile)

    # 2) 커밋 — 영속화 확정 (repository가 커밋하지 않는다는 전제로 service가 책임진다)
    await db.commit()
    await db.refresh(supplier)  # DB가 채운 기본값(생성 시각·기본 status 등) 반영

    # 3) 커밋 성공 후 이벤트 발행
    sla_due_date = datetime.now(timezone.utc) + timedelta(days=SUPPLIER_SLA_DAYS)
    event = SupplierInvitedEvent(
        supplier_id=supplier.supplier_id,
        email=email,
        sla_due_date=sla_due_date,
    )
    # 계약: publish는 2-인자, payload는 asdict로 직렬화 가능한 dict
    await publish("SupplierInvited", dataclasses.asdict(event))

    return supplier


async def get_supplier(
    db: AsyncSession,
    supplier_id: UUID,
) -> Optional[Supplier]:
    """단건 조회. 비즈니스 로직 없이 repository에 위임."""
    return await repository.get_supplier_by_id(db, supplier_id)


async def list_suppliers(
    db: AsyncSession,
    status: Optional[str] = None,
    tier: Optional[int] = None,
    risk_level: Optional[str] = None,
    feoc_status: Optional[str] = None,
    page: int = 1,
    size: int = 20,
) -> List[Supplier]:
    """
    목록 조회(필터: status / tier / risk_level / feoc_status + 페이지네이션).
    ※ 기본값이 None인 인자의 타입 힌트는 Optional[...]로 적는다(정확성).
    """
    return await repository.get_suppliers(
        db, status, tier, risk_level, feoc_status, page, size
    )
 


def _score_to_risk_level(score: int) -> str:
    """
    overall_risk_score 구간을 risk_level로 변환.
    schema.sql / md B-2 기준: 높을수록 위험.
      70~100 → low / 50~69 → medium / 30~49 → high / 0~29 → critical
    """
    if score >= 70:
        return "low"
    if score >= 50:
        return "medium"
    if score >= 30:
        return "high"
    return "critical"


@trace_node(node_name="upsert_risk_score", node_type="system")
async def upsert_risk_score(
    supplier_id: UUID,
    score: int,
    db: AsyncSession,
    batch_id: str = None,
) -> SupplierRiskProfile:
    """
    Verification/Risk Domain에서 RiskDetected 이벤트 수신 시 호출.
    (인자 시그니처는 backend_md_additions B-2를 그대로 따른다: supplier_id, score, db)

    동작:
      1) supplier_risk_profiles upsert (없으면 생성, 있으면 점수 갱신)
      2) overall_risk_score → risk_level 자동 재계산
      3) suppliers.risk_level / supplier_risk_profiles.risk_level 동기화(비정규화 캐시)
      4) 커밋
      5) 커밋 성공 후 RiskProfileUpdated 발행

    ※ 상태 변경 함수이므로 @trace_node 적용. batch_id가 있으면 해시 체인에 기록된다
      (Risk Domain이 배치 파이프라인 안에서 호출할 때 batch_id를 넘긴다).
    """
    # 0~100 범위로 클램프 (잘못된 입력 방어)
    score = max(0, min(100, score))
    new_level = _score_to_risk_level(score)

    # 1) upsert — 직접 SQL은 repository에 위임
    profile = await repository.upsert_risk_profile(
        db,
        supplier_id=supplier_id,
        overall_risk_score=score,
        risk_level=new_level,
        last_risk_review_at=datetime.now(timezone.utc),
    )

    # 3) suppliers.risk_level 비정규화 캐시 동기화 (목록 필터·맵 노드 컬러가 이걸 읽음)
    await repository.update_supplier_risk_level(db, supplier_id, new_level)

    # 4) 커밋 — 영속화 확정
    await db.commit()
    await db.refresh(profile)

    # 5) 커밋 성공 후 발행 (overall_risk_score 변경 시)
    event = RiskProfileUpdatedEvent(
        supplier_id=supplier_id,
        overall_risk_score=score,
        risk_level=new_level,
    )
    await publish("RiskProfileUpdated", dataclasses.asdict(event))

    return profile


async def get_risk_profile(
    supplier_id: UUID,
    db: AsyncSession,
) -> SupplierRiskProfile | None:
    """
    supplier_risk_profiles 단건 조회.
    (md B-2의 하위 3개 테이블 JOIN은 W4 종합 스코어링에서 확장 — 지금은 메인 프로필만.)
    조회 전용이라 발행/상태변경 없음.
    """
    return await repository.get_risk_profile_by_supplier(db, supplier_id)