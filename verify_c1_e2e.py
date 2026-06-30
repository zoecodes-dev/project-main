"""
scripts/verify_c1_e2e.py — C-1(조항 단위 RAG) End-to-End 검증 스크립트

은지(C) — 2026-06-30

[실행 전제]
  - AWS Bedrock 자격 증명이 로컬 환경(~/.aws/credentials 또는 환경변수)에 설정돼 있어야 한다.
    이 스크립트는 embeddings.py / compliance.py의 로컬 폴백(sha256 가짜 임베딩) 경로를
    "타지 않는 것"을 직접 확인하는 게 검증 목표 중 하나다.
  - docker-compose로 postgres(+pgvector)가 떠 있고, 01_schema.sql이 이미 적용된 상태여야 한다.
  - regulations 마스터 시드(10개 규제 INSERT)가 이미 실행됐어야 한다 (01_schema.sql 하단 INSERT 블록).

[실행 방법]
  프로젝트 루트에서:
    python -m scripts.verify_c1_e2e
  또는:
    python scripts/verify_c1_e2e.py   (단, 이 경우 backend가 PYTHONPATH에 있어야 함)

[이 스크립트가 하는 일 — 4단계]
  1. 사전 확인   : regulations 시드 존재 여부, regulation_clauses 테이블 존재 여부
  2. 조항 시드   : seed_regulation_clauses() 호출 (이미 있으면 스킵 — 멱등)
  3. 실제 임베딩 : reindex_pending_clause_embeddings() 호출
                   → 여기서 "로컬 폴백 경고 로그가 한 줄도 안 찍히는지"가 핵심 관전 포인트.
                     찍히면 AWS 자격 증명이 코드에서 실제로 안 먹히고 있다는 신호.
  4. RAG 검증    : search_regulations()를 5개 규제에 대해 직접 호출해 citation이
                   실제로 반환되는지 + judge_carbon_footprint를 1건 실제로 실행해
                   cited_clauses에 진짜 조번호가 들어가는지 확인

  각 단계는 실패해도 다음 단계로 넘어가되, 마지막에 PASS/FAIL 요약을 출력한다.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import uuid

from sqlalchemy import text

from backend.infrastructure.database import AsyncSessionLocal
from backend.domains.regulation.embeddings import (
    seed_regulation_clauses,
    reindex_pending_clause_embeddings,
    _CLAUSE_SEED_DATA,
)
from backend.agents.compliance import search_regulations, judge_carbon_footprint

# ── 로깅: 폴백 경고를 놓치지 않도록 WARNING 이상은 무조건 콘솔에 ──
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("verify_c1_e2e")

# embeddings.py가 "Bedrock 실패, 로컬 폴백 사용" 경고를 찍을 때 잡아내기 위한 카운터.
# (실제 코드를 건드리지 않고, 로그 핸들러로 가로채는 방식 — 비침투적 검증)
_fallback_hit_count = 0


class _FallbackDetectorHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        global _fallback_hit_count
        msg = record.getMessage()
        if "로컬 폴백 사용" in msg:
            _fallback_hit_count += 1
            print(f"  ⚠️  폴백 감지: {msg}")


def _install_fallback_detector() -> None:
    handler = _FallbackDetectorHandler()
    logging.getLogger("backend.domains.regulation.embeddings").addHandler(handler)


# ---------------------------------------------------------------------------
# 1단계: 사전 확인
# ---------------------------------------------------------------------------

async def step1_preflight() -> bool:
    print("\n" + "=" * 70)
    print("STEP 1 — 사전 확인 (regulations 시드 / regulation_clauses 테이블)")
    print("=" * 70)

    async with AsyncSessionLocal() as db:
        reg_count = (await db.execute(
            text("SELECT COUNT(*) AS c FROM regulations")
        )).fetchone()
        print(f"  regulations 테이블 행 수: {reg_count.c}")
        if reg_count.c == 0:
            print("  ❌ regulations 마스터 시드가 없어요. 01_schema.sql 하단 INSERT 블록을 먼저 적용해주세요.")
            return False

        try:
            clause_count = (await db.execute(
                text("SELECT COUNT(*) AS c FROM regulation_clauses")
            )).fetchone()
            print(f"  regulation_clauses 테이블 행 수(시드 전): {clause_count.c}")
        except Exception as e:
            print(f"  ❌ regulation_clauses 테이블이 없어요(Step 1 DDL 미적용?): {e}")
            return False

    print("  ✅ 사전 확인 통과")
    return True


# ---------------------------------------------------------------------------
# 2단계: 조항 시드
# ---------------------------------------------------------------------------

async def step2_seed_clauses() -> bool:
    print("\n" + "=" * 70)
    print("STEP 2 — 조항 시드 (seed_regulation_clauses)")
    print("=" * 70)

    inserted = await seed_regulation_clauses()
    print(f"  신규 INSERT된 조항 행 수: {inserted} (0이면 이미 시드됨 — 정상)")

    async with AsyncSessionLocal() as db:
        total = (await db.execute(
            text("SELECT COUNT(*) AS c FROM regulation_clauses")
        )).fetchone()
        print(f"  regulation_clauses 전체 행 수: {total.c}")

        expected_min = sum(len(v) for v in _CLAUSE_SEED_DATA.values())
        print(f"  시드 데이터 기준 기대 최소 행 수: {expected_min}")

        if total.c < expected_min:
            print(f"  ❌ 시드된 행({total.c})이 기대치({expected_min})보다 적어요. "
                  f"regulations 마스터에 일부 regulation_code가 없을 수 있어요.")
            return False

    print("  ✅ 조항 시드 통과")
    return True


# ---------------------------------------------------------------------------
# 3단계: 실제 Bedrock 임베딩 (★ 폴백 미발생이 핵심 관전 포인트)
# ---------------------------------------------------------------------------

async def step3_real_embedding() -> bool:
    print("\n" + "=" * 70)
    print("STEP 3 — 실제 Bedrock 임베딩 (reindex_pending_clause_embeddings)")
    print("=" * 70)
    print("  ※ 아래에 '⚠️ 폴백 감지' 로그가 한 줄이라도 찍히면 AWS 자격 증명이")
    print("    code 경로에서 실제로 안 먹히고 있다는 신호예요 (예: IAM 권한 부족,")
    print("    리전 불일치, bedrock:InvokeModel 거부 등).\n")

    global _fallback_hit_count
    _fallback_hit_count = 0
    _install_fallback_detector()

    done = await reindex_pending_clause_embeddings()
    print(f"\n  처리된 조항 임베딩 건수: {done}")
    print(f"  폴백 발생 횟수: {_fallback_hit_count}")

    async with AsyncSessionLocal() as db:
        indexed = (await db.execute(
            text("SELECT COUNT(*) AS c FROM regulation_clauses WHERE embedding_status = 'indexed'")
        )).fetchone()
        pending = (await db.execute(
            text("SELECT COUNT(*) AS c FROM regulation_clauses WHERE embedding_status = 'pending'")
        )).fetchone()
        print(f"  indexed: {indexed.c} / pending(남은 것): {pending.c}")

    if _fallback_hit_count > 0:
        print("  ⚠️  폴백이 감지됐어요 — AWS 자격 증명이 코드 경로에서 실제로 사용되지 않았어요.")
        print("      (AWS_PROFILE / AWS_REGION 환경변수, bedrock_factory.py의 리전 설정을 확인해주세요)")
        return False

    if pending.c > 0:
        print("  ❌ 여전히 pending인 조항이 있어요 — 일부 임베딩 호출이 실패했을 수 있어요.")
        return False

    print("  ✅ 실제 Bedrock 임베딩 통과 (폴백 0건)")
    return True


# ---------------------------------------------------------------------------
# 4단계: RAG 검증 — search_regulations 직접 호출 + judge 1건 실제 실행
# ---------------------------------------------------------------------------

# judge가 실제로 호출하는 (query_hint, regulation_code) 쌍 — compliance.py 그대로 가져옴
_JUDGE_QUERY_HINTS: list[tuple[str, str]] = [
    ("Xinjiang forced labor origin country supply chain prohibition rebuttable presumption", "UFLPA"),
    ("FEOC foreign entity of concern ownership threshold 25 percent battery critical mineral", "IRA"),
    ("carbon footprint declaration lifecycle threshold kgCO2eq battery cell manufacturing", "EU_BATTERY_ART7"),
    ("recycled content minimum threshold cobalt nickel lithium battery Annex XII", "EU_BATTERY"),
]


async def step4_rag_verification() -> bool:
    print("\n" + "=" * 70)
    print("STEP 4 — RAG 검증 (search_regulations 직접 호출 + judge 1건 실행)")
    print("=" * 70)

    all_ok = True

    async with AsyncSessionLocal() as db:
        for query_hint, regulation_code in _JUDGE_QUERY_HINTS:
            print(f"\n  [{regulation_code}] query_hint='{query_hint[:50]}...'")
            results = await search_regulations(query_hint, regulation_code, db, top_k=5)

            if not results:
                print(f"    ❌ 검색 결과 0건 — {regulation_code} 조항 시드/임베딩을 확인해주세요.")
                all_ok = False
                continue

            citations = [r.get("citation") for r in results]
            has_real_citation = any(c is not None for c in citations)

            print(f"    반환 행 수: {len(results)}")
            print(f"    citations: {citations}")
            print(f"    유사도(상위3): {[round(r['similarity'], 4) for r in results[:3]]}")

            if not has_real_citation:
                print(f"    ❌ 모든 citation이 None — 폴백 경로로 빠졌어요 "
                      f"(regulation_clauses에 {regulation_code} 조항이 indexed 상태가 아닐 수 있어요).")
                all_ok = False
            else:
                # 코사인 정렬이 실제로 의미 있는지 — similarity가 내림차순인지 확인
                sims = [r["similarity"] for r in results]
                is_sorted_desc = all(sims[i] >= sims[i+1] for i in range(len(sims) - 1))
                print(f"    유사도 내림차순 정렬 확인: {is_sorted_desc}")
                if not is_sorted_desc:
                    print("    ❌ 유사도가 내림차순이 아니에요 — ORDER BY 절을 확인해주세요.")
                    all_ok = False
                else:
                    print("    ✅ 실제 조항 citation 반환 + 코사인 랭킹 정상")

    # ── judge_carbon_footprint 1건 실제 실행 — batch_id가 실제 데이터에 있어야 동작 ──
    # 실제 batches 테이블에 존재하는 batch_id로 교체해서 실행해주세요.
    # 여기서는 형태만 보여주고, 실제 batch_id가 없으면 스킵(검증 환경 제약 — 가이드 §6).
    print("\n  [judge_carbon_footprint 실제 1건 실행 — batch_id 필요]")
    SAMPLE_BATCH_ID = "550e8400-e29b-41d4-a716-446655440000"  # ← 실제 batches.batch_id (UUID 문자열)로 교체

    if SAMPLE_BATCH_ID:
        async with AsyncSessionLocal() as db:
            context = {"batch_id": SAMPLE_BATCH_ID}  # 실제로는 _build_judge_context(state) 결과 필요
            result = await judge_carbon_footprint(SAMPLE_BATCH_ID, context, db)
            print(f"    verdict: {result.get('verdict')}")
            print(f"    cited_clauses: {result.get('cited_clauses')}")
            real_citations = [
                c for c in result.get("cited_clauses", [])
                if c.get("citation") and not c["citation"].startswith("clause-")
            ]
            if not real_citations:
                print("    ❌ cited_clauses에 실제 조번호가 없어요 — LLM이 빈 근거로 판정했거나 "
                      "여전히 한 줄 설명을 인용 중일 수 있어요.")
                all_ok = False
            else:
                print(f"    ✅ 실제 조번호 인용 확인: {[c['citation'] for c in real_citations]}")
    else:
        print("    ⏭️  SAMPLE_BATCH_ID가 설정 안 됐어요 — 스크립트 상단에서 실제 batch_id로 교체 후 재실행해주세요.")
        print("       (가이드 §6: S3/Bedrock 끝단 실데이터 검증은 로컬 자격 증명 + 실제 배치 데이터가 필요)")

    return all_ok


# ---------------------------------------------------------------------------
# 메인 — 4단계 순차 실행 + 요약
# ---------------------------------------------------------------------------

async def main() -> None:
    results: dict[str, bool] = {}

    results["1. 사전확인"] = await step1_preflight()
    if not results["1. 사전확인"]:
        print("\n사전 확인 실패 — 이후 단계를 건너뜁니다.")
        _print_summary(results)
        sys.exit(1)

    results["2. 조항시드"] = await step2_seed_clauses()
    results["3. 실제임베딩"] = await step3_real_embedding()
    results["4. RAG검증"] = await step4_rag_verification()

    _print_summary(results)
    sys.exit(0 if all(results.values()) else 1)


def _print_summary(results: dict[str, bool]) -> None:
    print("\n" + "=" * 70)
    print("최종 요약")
    print("=" * 70)
    for name, ok in results.items():
        print(f"  {'✅ PASS' if ok else '❌ FAIL'} — {name}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
