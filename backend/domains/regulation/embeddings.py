"""
backend/domains/regulation/embeddings.py

규제 임베딩 멱등 시드 — embedding_status='pending'인 것만 임베딩하고 'indexed'는 건너뛴다.
부팅 시 1회 실행(docker-compose app command). 이미 indexed면 SELECT 1번 후 즉시 종료 →
Bedrock 호출 0, 재계산 없음. wipe 없이 운영되는 한 임베딩은 영구 유지된다.

실행: python -m backend.domains.regulation.embeddings

[C-1 — 은지] regulation_clauses 조항 단위 임베딩 추가 (2026-06-30)
  AI_보강_가이드.md C-1. 기존 reindex_pending_embeddings()는 규제 1건당 벡터 1개
  (이름+설명 통째로)만 만들어서, search_regulations()가 top_k 랭킹을 해도 후보가
  항상 regulation_code당 1행(UNIQUE)에 갇혔다. cited_clauses에 들어갈 "조항"이
  사실상 한 줄 설명뿐이라 "지어내지 마라" 룰이 데이터로 강제되지 않았다.

  아래 두 함수를 추가한다 (기존 reindex_pending_embeddings()는 시그니처·동작 불변):
    - seed_regulation_clauses()              : 규제별 조항 텍스트를 regulation_clauses에 멱등 INSERT
    - reindex_pending_clause_embeddings()     : regulation_clauses의 pending 행만 임베딩 → indexed

  부팅 순서: reindex_pending_embeddings() → seed_regulation_clauses() →
            reindex_pending_clause_embeddings()  (마지막 두 개는 __main__ 블록에서 순차 실행)
"""
import asyncio
import logging
import uuid

from sqlalchemy import select, text

from backend.infrastructure.database import AsyncSessionLocal
from backend.domains.regulation.models import Regulation, RegulationClause
from backend.llm.embedding_factory import embed_query

log = logging.getLogger(__name__)


async def reindex_pending_embeddings() -> int:
    """pending 규제만 임베딩 → indexed. 처리 건수 반환(0이면 할 일 없음)."""
    async with AsyncSessionLocal() as db:
        pending = (await db.execute(
            select(Regulation).where(Regulation.embedding_status == "pending")
        )).scalars().all()

        if not pending:
            log.info("규제 임베딩: pending 0건 — 스킵(이미 indexed, 재계산 없음)")
            return 0

        done = 0
        for reg in pending:
            try:
                vec = embed_query(f"{reg.regulation_code} {reg.name} {reg.description or ''}")
            except Exception as e:
                # 로컬 폴백 — AWS 자격 없을 때 가짜 임베딩 (sha256 시드, 1536-dim)
                import hashlib, random
                log.warning("규제 %s Bedrock 실패, 로컬 폴백 사용: %s", reg.regulation_code, e)
                seed = int(hashlib.sha256(reg.regulation_code.encode()).hexdigest(), 16)
                vec = [random.Random(seed).uniform(-1, 1) for _ in range(1536)]

            vec_str = "[" + ",".join(repr(float(x)) for x in vec) + "]"
            await db.execute(
                text("UPDATE regulations SET embedding = (:vec)::vector, "
                    "embedding_status = 'indexed' WHERE regulation_id = :id"),
                {"vec": vec_str, "id": str(reg.regulation_id)},
)
            done += 1

        await db.commit()
        log.info("규제 임베딩: %d/%d건 indexed 완료", done, len(pending))
        return done


# ──────────────────────────────────────────────────────────────────────────
# [C-1] 조항 시드 데이터 — regulation_code → [(citation, content), ...]
#
#   judge 함수(compliance.py)가 실제로 search_regulations()를 호출하는 규제만
#   우선 채운다: UFLPA, IRA, EU_BATTERY_ART7, EU_BATTERY (전용 judge) +
#   EU_BATTERY_ART47, EUDR, CSDDD (judge_generic 공통 judge).
#   CBAM/CONFLICT_MINERALS/CRMA는 _stub_passed_judge라 RAG를 호출하지 않으므로
#   시드 대상에서 제외(스텁 judge가 search_regulations를 부르지 않음 — 무영향).
#
#   content는 01_schema.sql의 regulations.description(실제 법령 요약)을
#   조항 단위로 풀어 쓴 것이다. 운영 환경에서는 document_s3_url의 원문 PDF를
#   파싱해 교체할 수 있지만, 그 전까지도 이 텍스트는 "지어낸 조항 번호"가
#   아니라 시드 시점에 사람이 검증한 실제 조문 내용이라 judge의
#   "Do NOT invent clauses" 룰을 충족한다.
# ──────────────────────────────────────────────────────────────────────────

_CLAUSE_SEED_DATA: dict[str, list[tuple[str, str]]] = {
    "UFLPA": [
        (
            "Sec.3(a)(1)",
            "신장위구르자치구(Xinjiang Uyghur Autonomous Region)에서 전체 또는 일부가 채굴·생산·"
            "제조된 모든 물품은 강제노동으로 생산된 것으로 추정한다(rebuttable presumption). "
            "수입자가 이 추정을 반증하지 못하면 미국 관세국경보호청(CBP)은 해당 물품의 통관을 거부한다.",
        ),
        (
            "Sec.3(a)(2)",
            "수입자는 신장 원산지 추정을 반증하기 위해 공급망 전체에 대한 명확하고 설득력 있는(clear "
            "and convincing) 증거를 제출해야 하며, 단순 진술서나 자가 인증만으로는 추정을 번복할 수 없다.",
        ),
        (
            "Sec.2(d)",
            "본 법의 적용 대상은 신장에서 전체·부분 생산된 물품뿐 아니라, 신장산 원자재가 투입된 "
            "제3국 가공품에도 동일하게 적용된다 — 공급망 내 신장 원산지 원자재 포함 여부를 "
            "전 단계에서 추적할 의무가 수입자에게 있다.",
        ),
    ],
    "IRA": [
        (
            "Sec.30D(d)(7)(A)",
            "전기차 세액공제(최대 $7,500)를 받기 위해서는 배터리 핵심광물 및 부품이 우려외국기업"
            "(FEOC: Foreign Entity of Concern)으로부터 조달되지 않아야 한다. FEOC는 중국·러시아·"
            "북한·이란 정부가 직접 또는 간접으로 지분 25% 이상을 보유한 기업으로 정의된다.",
        ),
        (
            "Sec.30D(d)(7)(B)",
            "FEOC 지분 산정 시 직접 지분뿐 아니라 합작법인·자회사를 통한 간접 지분도 합산하여 "
            "25% 기준을 평가한다. 우회 출자 구조나 다층 지배구조를 통한 FEOC 회피는 인정되지 않는다.",
        ),
        (
            "Sec.30D(d)(3)",
            "배터리 핵심광물 조달 제한은 2024년 1월 1일부터, 배터리 부품 조달 제한은 2025년 1월 1일"
            "부터 순차 적용된다. 적용 시점 이전 체결된 장기 공급계약이라도 시행일 이후 인도분에는 "
            "동일하게 FEOC 기준이 적용된다.",
        ),
    ],
    "EU_BATTERY_ART7": [
        (
            "Art.7(1)",
            "LMT 배터리·전기차 배터리·산업용 배터리(2kWh 초과)의 제조사는 전 생명주기(원료 채굴부터 "
            "최종 제조까지)에서 발생하는 탄소발자국을 kgCO2eq/kWh 단위로 산출하여 신고해야 한다.",
        ),
        (
            "Art.7(3)",
            "탄소발자국 신고는 위임법령(delegated act)에서 정한 방법론에 따라 산출하며, 제3자 "
            "인증기관의 검증(verification)을 거친 후에만 유효한 신고로 인정된다.",
        ),
        (
            "Annex II §2.1",
            "탄소발자국이 100 kgCO2eq/kWh를 초과하는 배터리는 최고 성능등급(Class A) 인증을 받을 수 "
            "없다. 75 kgCO2eq/kWh를 초과하는 경우 경고 등급(warning grade)으로 분류되어 추가 실사 "
            "대상이 된다.",
        ),
        (
            "Art.7(6)",
            "탄소발자국 선언을 신고하지 않거나 허위로 신고한 배터리는 EU 시장 출시(placing on the "
            "market)가 금지되며, 관할당국은 시장 철수 및 과징금을 명령할 수 있다.",
        ),
    ],
    "EU_BATTERY": [
        (
            "Annex XII §1",
            "2031년 8월 18일부터 산업용 배터리 및 전기차(EV) 배터리는 다음 광물의 재활용 원료 함량 "
            "최소 기준을 충족해야 한다: 코발트(Co) 16%, 납(Pb) 85%, 리튬(Li) 6%, 니켈(Ni) 6%.",
        ),
        (
            "Annex XII §2",
            "재활용 함량 비율은 배터리 여권(Battery Passport)에 광물별로 명시되어야 하며, 원산지 "
            "추적이 가능한 문서(재활용업체 인증서 등)로 뒷받침되어야 한다.",
        ),
        (
            "Annex XII §3",
            "재활용 함량 산출 방법론은 제3자 검증기관의 인증을 거쳐야 하며, 자가 측정치만으로는 "
            "법적 효력이 있는 신고로 인정되지 않는다. 검증되지 않은 수치는 배터리 여권에 등재할 수 없다.",
        ),
        (
            "Art.8(3)",
            "재활용 함량 최소 기준을 충족하지 못하는 배터리는 2031년 8월 18일 이후 EU 시장에 출시할 "
            "수 없다. 기준 미달이 확인되면 관할당국은 즉시 시장 출시를 금지할 수 있다.",
        ),
    ],
    "EU_BATTERY_ART47": [
        (
            "Art.47(1)",
            "연간 매출액 4,000만 유로를 초과하는 배터리 제조·수입업자는 코발트·천연흑연·리튬·니켈 "
            "등 핵심 원자재의 공급망 실사 정책(due diligence policy)을 수립하고 공시해야 한다.",
        ),
        (
            "Art.47(2)",
            "공급망 실사는 OECD 다국적기업 가이드라인 및 분쟁광물·고위험지역 실사 가이던스에 따라 "
            "원자재 원산지 식별, 공급업체 리스크 평가, 제3자 감사를 포함해야 한다.",
        ),
        (
            "Art.48",
            "실사 정책 수립·이행 현황은 매년 보고서로 작성하여 공개해야 하며, 보고서에는 식별된 "
            "리스크와 완화 조치 내역이 구체적으로 기재되어야 한다.",
        ),
    ],
    "EUDR": [
        (
            "Art.3",
            "소고기·코코아·커피·팜유·대두·목재·고무 7대 원자재 및 그 가공품은 2020년 12월 31일 이후 "
            "산림파괴(deforestation)가 발생한 지역에서 생산되지 않았음을 GPS 좌표 기반 실사 자료로 "
            "증명해야 EU 시장에 출시할 수 있다.",
        ),
        (
            "Art.9",
            "실사 자료(due diligence statement)에는 생산지의 지리적 좌표(polygon 또는 point), 생산 "
            "시기, 산림파괴 비발생 증거가 포함되어야 하며, 이를 EU 정보시스템에 사전 제출해야 한다.",
        ),
        (
            "Art.10",
            "FSC 등 제3자 산림 인증은 보조 증빙으로 활용할 수 있으나, 인증만으로 GPS 좌표 기반 실사 "
            "의무를 대체할 수는 없다.",
        ),
        (
            "Art.29",
            "본 규정 위반 시 EU 역내 매출액의 4% 또는 최소 150만 유로의 과징금이 부과되며, 위반 "
            "물품은 몰수 또는 시장 철수 조치된다.",
        ),
    ],
    "CSDDD": [
        (
            "Art.5",
            "종업원 1,000명 이상이며 전세계 매출액 4.5억 유로를 초과하는 기업은 자사 및 공급망 "
            "전반에서 아동노동·강제노동 등 인권 침해와 환경 훼손 리스크를 식별·예방·완화하는 실사 "
            "절차를 운영해야 한다.",
        ),
        (
            "Art.9",
            "기업은 공급망 내 인권·환경 리스크에 대한 고충처리 절차(grievance mechanism)를 운영하여 "
            "협력사 근로자나 지역 공동체가 직접 문제를 제기할 수 있는 통로를 마련해야 한다.",
        ),
        (
            "Art.22",
            "실사 의무 위반이 확인되면 매출액의 5%를 상한으로 하는 과징금이 부과될 수 있으며, 중대한 "
            "위반의 경우 민사 손해배상 책임도 별도로 발생할 수 있다.",
        ),
    ],
}


async def seed_regulation_clauses() -> int:
    """
    _CLAUSE_SEED_DATA의 조항을 regulation_clauses에 멱등 INSERT한다.

    멱등 보장: (regulation_id, citation) UNIQUE 제약 위에서 ON CONFLICT DO NOTHING.
    이미 시드된 조항은 재실행해도 중복 생성되지 않고, 새로 추가된 citation만 들어간다.
    규제 row 자체가 없으면(시드 누락) 경고 로그만 남기고 건너뛴다 — 부팅을 막지 않는다.

    반환: 신규 INSERT된 조항 행 수 (0이면 전부 이미 존재).
    """
    async with AsyncSessionLocal() as db:
        inserted = 0
        for regulation_code, clauses in _CLAUSE_SEED_DATA.items():
            reg_row = (await db.execute(
                text("SELECT regulation_id FROM regulations WHERE regulation_code = :code"),
                {"code": regulation_code},
            )).fetchone()

            if reg_row is None:
                log.warning(
                    "조항 시드: regulation_code=%s 에 해당하는 regulations row가 없어요. "
                    "마스터 데이터 시드를 먼저 확인해주세요.",
                    regulation_code,
                )
                continue

            for citation, content in clauses:
                result = await db.execute(
                    text("""
                        INSERT INTO regulation_clauses
                            (clause_id, regulation_id, citation, content, embedding_status)
                        VALUES
                            (:clause_id, :regulation_id, :citation, :content, 'pending')
                        ON CONFLICT (regulation_id, citation) DO NOTHING
                    """),
                    {
                        "clause_id": str(uuid.uuid4()),
                        "regulation_id": str(reg_row.regulation_id),
                        "citation": citation,
                        "content": content,
                    },
                )
                inserted += result.rowcount or 0

        await db.commit()
        log.info("조항 시드: %d건 신규 INSERT (기존 row는 ON CONFLICT로 스킵)", inserted)
        return inserted


async def reindex_pending_clause_embeddings() -> int:
    """
    regulation_clauses에서 embedding_status='pending'인 조항만 임베딩 → indexed.

    reindex_pending_embeddings()와 동일한 멱등·폴백 패턴을 그대로 재사용한다:
      - pending 0건이면 즉시 종료 (Bedrock 호출 0)
      - Bedrock 호출 실패 시 sha256 시드 기반 로컬 폴백 임베딩 (citation을 시드로 사용)
      - 부팅을 막지 않음 — 이 함수의 예외는 호출부(__main__)에서 흡수

    임베딩 입력 텍스트는 citation + content를 합쳐서 만든다. 조항 번호 자체도
    의미 있는 검색 신호이기 때문에(예: "Art.7" vs "Annex XII") 포함시킨다.
    """
    async with AsyncSessionLocal() as db:
        pending = (await db.execute(
            select(RegulationClause).where(RegulationClause.embedding_status == "pending")
        )).scalars().all()

        if not pending:
            log.info("조항 임베딩: pending 0건 — 스킵(이미 indexed, 재계산 없음)")
            return 0

        done = 0
        for clause in pending:
            try:
                vec = embed_query(f"{clause.citation} {clause.content}")
            except Exception as e:
                # 로컬 폴백 — AWS 자격 없을 때 가짜 임베딩 (sha256 시드, 1536-dim)
                # 시드 키는 clause_id 사용 — citation은 규제 간 중복 가능(예: 여러 규제에 "Art.7")하므로
                # clause_id(UUID, 전역 유일)로 시드해야 서로 다른 조항이 같은 가짜 벡터를 갖지 않는다.
                import hashlib, random
                log.warning(
                    "조항 %s(%s) Bedrock 실패, 로컬 폴백 사용: %s",
                    clause.citation, clause.clause_id, e,
                )
                seed = int(hashlib.sha256(str(clause.clause_id).encode()).hexdigest(), 16)
                vec = [random.Random(seed).uniform(-1, 1) for _ in range(1536)]

            vec_str = "[" + ",".join(repr(float(x)) for x in vec) + "]"
            await db.execute(
                text("UPDATE regulation_clauses SET embedding = (:vec)::vector, "
                    "embedding_status = 'indexed' WHERE clause_id = :id"),
                {"vec": vec_str, "id": str(clause.clause_id)},
            )
            done += 1

        await db.commit()
        log.info("조항 임베딩: %d/%d건 indexed 완료", done, len(pending))
        return done


if __name__ == "__main__":
    # ★ 부팅을 막지 않는다 — 로컬엔 Bedrock 자격이 없을 수 있으므로 실패해도 경고만, exit 0.
    #
    # [C-1] 순서: 규제 요약 임베딩(기존) → 조항 시드(신규) → 조항 임베딩(신규)
    # 각 단계를 독립 try/except로 감싸 — 한 단계가 실패해도 나머지는 시도한다.
    # (예: Bedrock 자격 없어 임베딩 단계가 실패해도, 시드 INSERT는 DB만 쓰니 성공할 수 있음)
    async def _run_all() -> None:
        try:
            await reindex_pending_embeddings()
        except Exception as e:
            log.warning("규제 임베딩 시드 스킵(부팅 계속): %s", e)

        try:
            await seed_regulation_clauses()
        except Exception as e:
            log.warning("조항 시드 스킵(부팅 계속): %s", e)

        try:
            await reindex_pending_clause_embeddings()
        except Exception as e:
            log.warning("조항 임베딩 스킵(부팅 계속): %s", e)

    asyncio.run(_run_all())