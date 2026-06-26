"""
domains/supplier/masterform_prefill.py  (담당: 팀원 B · 은진 / KIRA W5 AP)

마스터폼 'AI 자동 채움(prefill)'의 도메인 지식 SSOT.

AP(AI 파싱 강화)의 목표: 협력사가 양식을 직접 못 채워도, 보완 문서(PDF·이미지)를
업로드하면 AI(data_gateway.parse_document)가 필드를 추출하고 → 그 결과가 마스터폼
필드로 자동 채워진다. 협력사는 추출 결과만 확인·정정한다.

이 모듈은 두 가지를 한곳에 모은다(추출과 변환이 같은 필드 정의를 보게):
  1) FIELD_CATALOG — 문서에서 AI가 추출할 '마스터폼 스칼라 필드'의 정의(SSOT).
     data_gateway가 이 카탈로그로 추출 프롬프트를 만들고(=알려진 키로만 추출),
     이 모듈이 같은 카탈로그로 flat 추출결과를 마스터폼 섹션 구조로 되돌린다.
  2) to_master_form_prefill — flat parsed_fields → 섹션별 nested prefill + 신뢰도
     낮은(<임계치) 필드 목록(협력사 확인 요청 대상).

스코프: '한 문서에서 단일값으로 신뢰성 있게 뽑히는' 스칼라 필드만 자동 채움 대상이다.
다건/구조화 섹션(공장 목록·연락처·인증서·인권/실사/교육·소재별 재활용 함량·탄소
선언 다건)은 AI 단일 추출로 안전하게 못 채우므로 협력사가 직접 입력한다.
recycling_efficiency·recycled_materials(소재별 회수율/함량 dict)도 단일 스칼라가
아니라 구조화 값이라 카탈로그에서 제외(협력사가 직접 입력).
"""
from typing import Any, Dict, List, Optional, Tuple

# 신뢰도 임계치 — data_gateway.CONFIDENCE_THRESHOLD와 동일 기준(0.85).
# 이 미만 필드는 prefill에 채우되 '확인 요청' 목록에 함께 실어 협력사 검토를 유도한다.
PREFILL_CONFIDENCE_THRESHOLD = 0.85


# ── 카탈로그 항목: flat_field → (마스터폼 섹션, 파이썬 타입, 한글 라벨) ──────────
# flat_field는 섹션을 가로질러 유일하다(추출결과 parsed_fields가 평면 dict이므로).
# section 값은 MasterFormRequest의 최상위 키와 일치한다(company/manufacturing/...).
_FIELD = Tuple[str, str, str]   # (section, type, label)

FIELD_CATALOG: Dict[str, _FIELD] = {
    # 섹션 0 — 회사 (suppliers)
    "company_name":        ("company", "str", "회사 정식 상호"),
    "company_name_en":     ("company", "str", "영문 상호"),
    "ceo_name":            ("company", "str", "대표자명"),
    "business_reg_no":     ("company", "str", "사업자등록번호"),
    "corporate_reg_no":    ("company", "str", "법인등록번호"),
    "duns_number":         ("company", "str", "DUNS 번호"),
    "tax_number":          ("company", "str", "납세자번호(VAT 등)"),
    "website":             ("company", "str", "웹사이트"),
    "established_year":    ("company", "int", "설립연도"),
    "employee_count":      ("company", "int", "임직원 수"),
    "provider_type":       ("company", "str", "공급자 유형(manufacturer/recycler/trader/miner)"),

    # 섹션 1 — 탄소발자국 (supplier_manufacturer_details)
    "manufacturing_process": ("manufacturing", "str",   "제조 공정 설명"),
    "energy_source":         ("manufacturing", "str",   "에너지원"),
    "capacity":              ("manufacturing", "str",   "생산 능력"),
    "carbon_intensity":      ("manufacturing", "float", "탄소집약도(kgCO2eq/kg)"),

    # 섹션 2 — 재활용 (supplier_recycler_details · recycling_efficiency 제외=D 대기)
    "recycling_certification": ("recycling", "str",   "재활용 인증"),
    "input_source":            ("recycling", "str",   "투입 원료 출처"),
    "recycled_content_ratio":  ("recycling", "float", "재활용 함량 비율(%)"),

    # 섹션 3 — 원산지 (supplier_miner_details 스칼라 · GPS/증명서는 직접입력)
    "mine_name":         ("origin", "str",   "광산명"),
    "mining_method":     ("origin", "str",   "채굴 방식"),
    "extraction_volume": ("origin", "float", "채굴량"),

    # 섹션 4 — 지분·FEOC (supplier_trader_details + risk_profile FEOC)
    "trading_license":         ("ownership", "str",   "거래 라이선스"),
    "broker_certification":    ("ownership", "str",   "브로커 인증"),
    "disclosure_completeness": ("ownership", "float", "공개 완성도(%)"),
    "feoc_direct_ownership":   ("ownership", "float", "FEOC 직접 지분(%)"),
    "feoc_indirect_ownership": ("ownership", "float", "FEOC 간접 지분(%)"),
}


def catalog_prompt_lines() -> str:
    """
    추출 프롬프트에 끼울 '대상 필드 목록' 문자열을 만든다(섹션별 그룹핑·한글 라벨 포함).
    data_gateway가 이 목록을 system 프롬프트에 넣어 '알려진 키로만' 추출하게 한다.
    """
    by_section: Dict[str, List[str]] = {}
    for field, (section, ftype, label) in FIELD_CATALOG.items():
        by_section.setdefault(section, []).append(f'    "{field}" ({ftype}) — {label}')
    blocks = []
    for section, lines in by_section.items():
        blocks.append(f"  [{section}]\n" + "\n".join(lines))
    return "\n".join(blocks)


def _coerce(value: Any, ftype: str) -> Optional[Any]:
    """
    추출값(문자열일 수 있음)을 카탈로그 타입으로 안전 변환. 실패하면 None.
    None을 반환하면 호출부가 '읽지 못한 값'으로 간주해 prefill에 넣지 않는다(추측 금지).
    """
    if value is None:
        return None
    if ftype == "str":
        s = str(value).strip()
        return s or None
    # float/int: 숫자/단위 섞인 문자열("36.5 kgCO2eq/kg")에서 앞쪽 숫자만 취한다.
    if isinstance(value, (int, float)):
        num: Any = value
    else:
        import re
        m = re.search(r"-?\d+(?:\.\d+)?", str(value))
        if not m:
            return None
        num = m.group(0)
    try:
        return int(float(num)) if ftype == "int" else float(num)
    except (TypeError, ValueError):
        return None


def to_master_form_prefill(
    parsed_fields: Dict[str, Any],
    confidence_map: Dict[str, Any],
    threshold: float = PREFILL_CONFIDENCE_THRESHOLD,
) -> Dict[str, Any]:
    """
    flat 추출결과(parsed_fields/confidence_map) → 마스터폼 섹션 구조 prefill.

    반환:
      {
        "prefill": { "company": {...}, "manufacturing": {...}, ... },  # 채워진 섹션만
        "low_confidence_fields": [
            {"section","field","label","value","confidence"}, ...      # < threshold
        ],
      }

    카탈로그에 없는 추출 키는 무시한다(마스터폼에 자리가 없는 잡필드 방지).
    타입 변환 실패값도 넣지 않는다(추측 저장 금지). 신뢰도 낮은 값은 채우되
    low_confidence_fields에 실어 협력사 확인을 유도한다.
    """
    prefill: Dict[str, Dict[str, Any]] = {}
    low_confidence: List[Dict[str, Any]] = []

    for field, (section, ftype, label) in FIELD_CATALOG.items():
        if field not in parsed_fields:
            continue
        coerced = _coerce(parsed_fields[field], ftype)
        if coerced is None:
            continue
        prefill.setdefault(section, {})[field] = coerced

        try:
            conf = float(confidence_map.get(field, 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        if conf < threshold:
            low_confidence.append({
                "section": section,
                "field": field,
                "label": label,
                "value": coerced,
                "confidence": conf,
            })

    return {"prefill": prefill, "low_confidence_fields": low_confidence}
