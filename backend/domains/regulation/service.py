"""
backend/domains/regulation/service.py  (담당: 팀원 C — 은지)

★ [Wave 0 — 계약 우선(contract-first) 스텁]
   D(영수)의 '맵 gap 계산 API'(C2) 작업을 언블락하기 위해
   인터페이스 시그니처와 더미 반환값을 먼저 공개한다.

   현재 상태: DB 연동 없이 하드코딩된 더미 데이터 반환.
   화요일(Wave 1) B1 작업에서 실제 DB 조회 로직으로 교체 예정.

레이어 규칙 (PROJECT_CORE 5-1):
  router → service → repository → models  (단방향)
  - 이 파일은 service 계층이다.
  - DB 직접 접근은 repository에 위임한다 (현재는 스텁이므로 repository 미사용).
  - 타 도메인(supplier, product 등)을 직접 import하지 않는다.

[이 파일이 제공하는 계약(Contract)]
  ┌─────────────────────────────────────────────────────────┐
  │  get_applicable_regulations(product_id)                 │
  │    → list[dict]  # 규제 정보 리스트                     │
  │                                                         │
  │  get_required_fields(regulation_id)                     │
  │    → list[dict]  # 규제가 요구하는 필수 필드 리스트     │
  └─────────────────────────────────────────────────────────┘

[반환 dict 스키마 — 호출자가 의존하는 계약]
  get_applicable_regulations() 반환 항목:
    {
        "regulation_id":   str,   # 규제 고유 ID (PK 역할)
        "regulation_code": str,   # 규제 코드 (예: "EU_BATTERY")
        "name":            str,   # 규제 명칭
        "description":     str,   # 규제 설명
        "destination":     str,   # 적용 시장 (예: "EU", "US", "BOTH")
    }

  get_required_fields() 반환 항목:
    {
        "field_id":                  str,        # 필드 고유 ID
        "regulation_id":             str,        # 어느 규제에 속하는지
        "field_name":                str,        # 필드 식별자 (snake_case)
        "field_label":               str,        # 사람이 읽는 필드명 (한글)
        "field_type":                str,        # 데이터 타입 ("number", "string", "jsonb" 등)
        "is_mandatory":              bool,       # 필수 여부
        "provider_type_applicable":  list[str],  # 어느 공급사 유형에 해당하는지
    }

[B1 작업 완료 후 교체 계획]
  화요일(Wave 1) B1에서 regulation 도메인 DB 구조가 확정되면:
  1. repository.py 를 신규 작성해 실제 SQL 조회 추가
  2. 이 service 함수들에서 더미 데이터 제거 후 repository 호출로 교체
  3. AsyncSession 파라미터 추가 (DB 연동 시 필요)
  호출부(D의 C2 코드)는 함수 시그니처가 동일하므로 수정 불필요.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. 더미 데이터 상수
#    Wave 0 스텁 단계에서 반환할 하드코딩 데이터.
#    B1(화요일) 완료 후 이 상수들은 삭제하고 DB 조회로 교체한다.
#
#    [설계 의도]
#    - regulation_id는 실제 DB PK 대신 임시 문자열 사용.
#      B1 완료 후 UUID로 교체 예정.
#    - destination은 compliance.py의 REGULATION_BY_DESTINATION 키와 일치시킨다.
# ---------------------------------------------------------------------------

# ── 규제 목록 더미 데이터 ──
# compliance.py REGULATION_BY_DESTINATION의 EU 규제 8종 중 핵심 4종을 우선 표현.
# B1 완료 후 전체 규제를 DB에서 조회하도록 교체한다.
_DUMMY_REGULATIONS: list[dict[str, Any]] = [
    {
        "regulation_id":   "reg-eu-battery-001",
        "regulation_code": "EU_BATTERY",
        "name":            "EU 배터리법 (재활용 함량)",
        "description":     (
            "EU 배터리법 2023/1542 Annex XII — "
            "코발트·니켈·리튬·납의 재활용 함량 최소 기준을 규정한다."
        ),
        "destination":     "EU",
    },
    {
        "regulation_id":   "reg-eu-battery-art7-001",
        "regulation_code": "EU_BATTERY_ART7",
        "name":            "EU 배터리법 Art.7 (탄소발자국 선언)",
        "description":     (
            "EU 배터리법 2023/1542 Art.7 / Annex II — "
            "배터리 제조 전주기 탄소발자국 선언 의무 및 임계치를 규정한다. "
            "2025년 2월 시행 기준: 위반 100 kgCO2eq/kWh 초과, 경고 75 초과."
        ),
        "destination":     "EU",
    },
    {
        "regulation_id":   "reg-eudr-001",
        "regulation_code": "EUDR",
        "name":            "EU 삼림벌채 규정 (EUDR)",
        "description":     (
            "EU 산림벌채규정 — "
            "원자재 원산지의 GPS 좌표 및 FSC 인증을 통한 삼림벌채 위험 검증을 요구한다."
        ),
        "destination":     "EU",
    },
    {
        "regulation_id":   "reg-uflpa-001",
        "regulation_code": "UFLPA",
        "name":            "위구르 강제노동방지법 (UFLPA)",
        "description":     (
            "미국 UFLPA — "
            "신장(Xinjiang) 원산지 원자재가 포함된 경우 "
            "반박 가능 추정(rebuttable presumption) 원칙을 적용한다."
        ),
        "destination":     "US",
    },
    {
        "regulation_id":   "reg-ira-001",
        "regulation_code": "IRA",
        "name":            "인플레이션감축법 FEOC 조항 (IRA)",
        "description":     (
            "미국 IRA — "
            "우려국 외국 기업(FEOC) 직·간접 지분 25% 이상 시 세액공제 불인정."
        ),
        "destination":     "US",
    },
]

# ── 규제별 필수 필드 더미 데이터 ──
# regulation_id를 키로, 해당 규제가 요구하는 필드 목록을 값으로 관리.
# C1(D 담당) 완료 후 regulation_required_fields 테이블에서 조회하도록 교체한다.
#
# field_name 컨벤션:
#   - compliance.py _build_judge_context()의 키 이름과 일치시킨다.
#   - 재활용 소재 키는 소문자 원소기호(co, ni, li, pb) — events/types.py SSOT.
_DUMMY_REQUIRED_FIELDS: dict[str, list[dict[str, Any]]] = {
    # EU_BATTERY (Annex XII — 재활용 함량)
    "reg-eu-battery-001": [
        {
            "field_id":                 "fld-eubt-001",
            "regulation_id":            "reg-eu-battery-001",
            "field_name":               "recycled_content_ratio",
            "field_label":              "재활용 함량 비율 (%)",
            "field_type":               "number",
            "is_mandatory":             True,
            "provider_type_applicable": ["recycler", "manufacturer"],
        },
        {
            "field_id":                 "fld-eubt-002",
            "regulation_id":            "reg-eu-battery-001",
            "field_name":               "recycled_materials",
            "field_label":              "광물별 재활용 함량 (co/ni/li/pb %)",
            "field_type":               "jsonb",
            "is_mandatory":             True,
            "provider_type_applicable": ["recycler"],
        },
    ],
    # EU_BATTERY_ART7 (Art.7 / Annex II — 탄소발자국)
    "reg-eu-battery-art7-001": [
        {
            "field_id":                 "fld-art7-001",
            "regulation_id":            "reg-eu-battery-art7-001",
            "field_name":               "carbon_intensity",
            "field_label":              "탄소집약도 (kgCO2eq/kWh)",
            "field_type":               "number",
            "is_mandatory":             True,
            "provider_type_applicable": ["manufacturer"],
        },
        {
            "field_id":                 "fld-art7-002",
            "regulation_id":            "reg-eu-battery-art7-001",
            "field_name":               "factory_carbon_declarations",
            "field_label":              "공장별 탄소발자국 선언 (1차 데이터)",
            "field_type":               "jsonb",
            "is_mandatory":             True,
            "provider_type_applicable": ["manufacturer"],
        },
    ],
    # EUDR (삼림벌채 — GPS + FSC)
    "reg-eudr-001": [
        {
            "field_id":                 "fld-eudr-001",
            "regulation_id":            "reg-eudr-001",
            "field_name":               "mine_coordinates",
            "field_label":              "원산지 GPS 좌표 (lng, lat)",
            "field_type":               "string",
            "is_mandatory":             True,
            "provider_type_applicable": ["miner"],
        },
        {
            "field_id":                 "fld-eudr-002",
            "regulation_id":            "reg-eudr-001",
            "field_name":               "origin_country",
            "field_label":              "원산지 국가 코드 (ISO 3166-1 alpha-2)",
            "field_type":               "string",
            "is_mandatory":             True,
            "provider_type_applicable": ["miner", "trader"],
        },
    ],
    # UFLPA (원산지 + 강제노동 위험 플래그)
    "reg-uflpa-001": [
        {
            "field_id":                 "fld-uflpa-001",
            "regulation_id":            "reg-uflpa-001",
            "field_name":               "origin_country",
            "field_label":              "원산지 국가 코드",
            "field_type":               "string",
            "is_mandatory":             True,
            "provider_type_applicable": ["miner", "trader"],
        },
        {
            "field_id":                 "fld-uflpa-002",
            "regulation_id":            "reg-uflpa-001",
            "field_name":               "geo_risk_flags",
            "field_label":              "지역 위험 플래그 (예: xinjiang)",
            "field_type":               "jsonb",
            "is_mandatory":             False,
            "provider_type_applicable": ["miner"],
        },
    ],
    # IRA (FEOC 지분)
    "reg-ira-001": [
        {
            "field_id":                 "fld-ira-001",
            "regulation_id":            "reg-ira-001",
            "field_name":               "feoc_direct_ownership",
            "field_label":              "FEOC 직접 지분율 (%)",
            "field_type":               "number",
            "is_mandatory":             True,
            "provider_type_applicable": ["trader", "manufacturer"],
        },
        {
            "field_id":                 "fld-ira-002",
            "regulation_id":            "reg-ira-001",
            "field_name":               "feoc_indirect_ownership",
            "field_label":              "FEOC 간접 지분율 (%)",
            "field_type":               "number",
            "is_mandatory":             False,
            "provider_type_applicable": ["trader", "manufacturer"],
        },
    ],
}


# ---------------------------------------------------------------------------
# 2. 공개 인터페이스 함수 — 이 두 함수가 Wave 0에서 공개하는 계약이다.
# ---------------------------------------------------------------------------

async def get_applicable_regulations(product_id: str) -> list[dict[str, Any]]:
    """
    [Wave 0 스텁] 주어진 제품에 적용되는 규제 목록을 반환한다.

    현재(스텁): product_id 무시 — EU 핵심 규제 5종 + US 규제 2종 고정 반환.
    목표(B1 완료 후): product_id → batches.destination 조회 →
                      compliance.py REGULATION_BY_DESTINATION 매핑 →
                      regulations 테이블에서 실제 행 조회.

    파라미터:
        product_id (str): 제품 UUID 문자열.
                          스텁 단계에서는 사용하지 않지만,
                          호출부가 이 시그니처에 맞춰 코드를 작성해야
                          B1 교체 시 수정이 불필요하다.

    반환:
        list[dict]: 규제 정보 딕셔너리 리스트.
                    각 항목의 스키마는 모듈 상단 docstring 참조.

    사용 예시 (D 팀원 — C2 맵 gap 계산):
        from backend.domains.regulation.service import get_applicable_regulations

        regulations = await get_applicable_regulations(product_id=str(product_id))
        for reg in regulations:
            print(reg["regulation_code"], reg["destination"])
    """
    # [STUB] 더미 데이터 반환. B1 완료 후 아래 블록을 repository 호출로 교체한다.
    logger.debug(
        "[STUB] get_applicable_regulations called with product_id=%s. "
        "더미 데이터를 반환합니다. B1 완료 후 DB 조회로 교체 필요.",
        product_id,
    )
    return _DUMMY_REGULATIONS


async def get_required_fields(regulation_id: str) -> list[dict[str, Any]]:
    """
    [Wave 0 스텁] 주어진 규제가 요구하는 필수 필드 목록을 반환한다.

    현재(스텁): _DUMMY_REQUIRED_FIELDS에서 regulation_id로 조회해 반환.
               알 수 없는 regulation_id면 빈 리스트를 반환한다.
    목표(C1 완료 후): regulation_required_fields 테이블에서 실제 행 조회.
                      C1은 D(영수) 담당 — DDL + 시드 완료 후 여기 교체.

    파라미터:
        regulation_id (str): 규제 고유 ID.
                             get_applicable_regulations() 반환값의
                             "regulation_id" 필드값을 그대로 넘기면 된다.

    반환:
        list[dict]: 필수 필드 딕셔너리 리스트.
                    알 수 없는 regulation_id → 빈 리스트 [] 반환 (예외 아님).
                    각 항목의 스키마는 모듈 상단 docstring 참조.

    사용 예시 (D 팀원 — C2 맵 gap 계산):
        from backend.domains.regulation.service import (
            get_applicable_regulations,
            get_required_fields,
        )

        # 1단계: 제품에 적용되는 규제 목록 조회
        regulations = await get_applicable_regulations(product_id=str(product_id))

        # 2단계: 각 규제가 요구하는 필드 목록 조회
        for reg in regulations:
            required_fields = await get_required_fields(
                regulation_id=reg["regulation_id"]
            )
            for field in required_fields:
                print(field["field_name"], field["is_mandatory"])
    """
    # [STUB] 더미 데이터에서 조회. C1 완료 후 repository 호출로 교체한다.
    fields = _DUMMY_REQUIRED_FIELDS.get(regulation_id, [])

    if not fields:
        logger.warning(
            "[STUB] get_required_fields: regulation_id=%s 에 매핑된 필드가 없습니다. "
            "C1(D 담당) 완료 후 DB에서 조회하도록 교체 필요.",
            regulation_id,
        )

    return fields
