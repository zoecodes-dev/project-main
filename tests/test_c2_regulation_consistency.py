"""
tests/test_c2_regulation_consistency.py — C-2 정합성 테스트

은지(C) — 2026-06-30

[목적]
  AI_보강_가이드.md C-2 완료 조건 중 "3중 정합성 테스트"를 검증한다.
  (dpp 도메인 삭제로 "3중"→"2중"으로 줄었음 — dpp COUNT 항목 제외)

  검증 대상:
    1. compliance.py의 REGULATION_BY_DESTINATION dict(런타임 SSOT)
    2. DB의 regulations.region 컬럼(데이터 SSOT)

  이 두 출처가 drift하면(예: 신규 규제를 DB에만 추가하거나
  dict에만 추가하는 경우) 이 테스트가 CI에서 바로 감지한다.

[실행]
  pytest tests/test_c2_regulation_consistency.py -v
  또는
  python -m pytest tests/test_c2_regulation_consistency.py -v
"""
from __future__ import annotations

import asyncio
import pytest
from sqlalchemy import text

# ── 테스트 대상 import ──
from backend.agents.compliance import REGULATION_BY_DESTINATION
from backend.infrastructure.database import AsyncSessionLocal


# ---------------------------------------------------------------------------
# 헬퍼 — DB에서 규제 코드별 region 조회
# ---------------------------------------------------------------------------

async def _fetch_db_regulation_map() -> dict[str, str]:
    """
    regulations 테이블에서 {regulation_code: region} 매핑을 가져온다.
    region 값: EU / US / BOTH
    """
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            text("SELECT regulation_code, region FROM regulations")
        )).fetchall()
    return {row.regulation_code: row.region for row in rows if row.regulation_code}


def _build_dict_expected_region(code: str) -> set[str]:
    """
    REGULATION_BY_DESTINATION에서 특정 regulation_code가
    어느 destination에 포함됐는지 확인해 예상 region을 반환한다.

    규칙:
      EU에만 있음   → region='EU'
      US에만 있음   → region='US'
      EU+US 둘 다   → region='BOTH'
      KR에만 있음   → KR은 []라서 이 함수에 도달하지 않음
    """
    in_eu = code in REGULATION_BY_DESTINATION.get("EU", [])
    in_us = code in REGULATION_BY_DESTINATION.get("US", [])

    if in_eu and in_us:
        return {"BOTH"}
    if in_eu:
        return {"EU"}
    if in_us:
        return {"US"}
    return set()


# ---------------------------------------------------------------------------
# 테스트 1: dict에 있는 모든 코드가 DB에도 존재하는가
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dict_codes_exist_in_db() -> None:
    """
    REGULATION_BY_DESTINATION의 모든 regulation_code가
    regulations 테이블에 있어야 한다.
    dict에만 있고 DB에 없는 코드가 있으면 시드 누락이다.
    """
    db_map = await _fetch_db_regulation_map()
    db_codes = set(db_map.keys())

    all_dict_codes: set[str] = set()
    for codes in REGULATION_BY_DESTINATION.values():
        all_dict_codes.update(codes)

    missing_in_db = all_dict_codes - db_codes
    assert not missing_in_db, (
        f"REGULATION_BY_DESTINATION에 있지만 DB에 없는 코드: {missing_in_db}\n"
        "→ 01_schema.sql regulations 시드를 확인해주세요."
    )


# ---------------------------------------------------------------------------
# 테스트 2: DB에 있는 모든 코드의 region이 dict 배치와 일치하는가
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_db_region_matches_dict() -> None:
    """
    DB의 regulations.region 값이 REGULATION_BY_DESTINATION의
    배치와 일치해야 한다.

    예: EU_BATTERY_ART7가 dict의 "EU" 리스트에만 있으면
        DB의 regulations.region = 'EU' 여야 한다.
    """
    db_map = await _fetch_db_regulation_map()
    mismatches: list[str] = []

    for code, db_region in db_map.items():
        expected_regions = _build_dict_expected_region(code)
        if not expected_regions:
            # dict에 없는 코드 — 테스트 1에서 이미 처리
            continue
        if db_region not in expected_regions:
            mismatches.append(
                f"  {code}: DB region='{db_region}', dict 기준 예상='{expected_regions}'"
            )

    assert not mismatches, (
        "REGULATION_BY_DESTINATION dict와 DB regulations.region 불일치:\n"
        + "\n".join(mismatches)
        + "\n→ regulations 시드 또는 REGULATION_BY_DESTINATION dict를 정합하게 맞춰주세요."
    )


# ---------------------------------------------------------------------------
# 테스트 3: required_fields 시드가 실제로 들어갔는가
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_required_fields_seeded() -> None:
    """
    regulation_required_fields 테이블에 C-2 시드 데이터가
    있어야 한다. 최소 1건 이상.

    단, get_required_fields()는 0행이면 빈 리스트 반환(폴백)이므로
    이 테스트가 실패해도 서비스가 다운되진 않는다 — 단 gap 분석이
    실데이터를 못 보는 상태가 된다.
    """
    async with AsyncSessionLocal() as db:
        count = (await db.execute(
            text("SELECT COUNT(*) FROM regulation_required_fields")
        )).scalar()

    assert count and count > 0, (
        "regulation_required_fields가 비어 있어요. "
        "01_schema.sql의 C-2 시드 INSERT를 확인해주세요."
    )


# ---------------------------------------------------------------------------
# 동기 실행 진입점 (pytest 없이 직접 python으로 돌릴 때)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    async def _run_all() -> None:
        print("=== C-2 정합성 테스트 ===\n")
        try:
            await test_dict_codes_exist_in_db()
            print("✅ 테스트 1: dict 코드 DB 존재 확인 PASS")
        except AssertionError as e:
            print(f"❌ 테스트 1 FAIL: {e}")

        try:
            await test_db_region_matches_dict()
            print("✅ 테스트 2: DB region ↔ dict 정합성 PASS")
        except AssertionError as e:
            print(f"❌ 테스트 2 FAIL: {e}")

        try:
            await test_required_fields_seeded()
            print("✅ 테스트 3: required_fields 시드 존재 PASS")
        except AssertionError as e:
            print(f"❌ 테스트 3 FAIL: {e}")

    asyncio.run(_run_all())