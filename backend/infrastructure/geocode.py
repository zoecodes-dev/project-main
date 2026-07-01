"""
infrastructure/geocode.py  (담당: 영수 D)

주소 → 좌표(lat, lng) 변환 헬퍼. AWS Location Service Places v2(geo-places).

■ 왜 geo-places v2(keyless)인가
  - v1(SearchPlaceIndexForText)은 deprecated 예정 + Place Index 리소스를 미리
    만들어야 함. v2는 keyless — 리소스 생성 불필요, 클라이언트 만들고 바로 호출.
  - 새로 관리할 AWS 리소스도, API 키/시크릿도 없음. 인증은 EC2 IAM Role 자동.

■ ★리전 주의★ — 도쿄(ap-northeast-1). 서울 아님!
  - geo-places는 서울(ap-northeast-2)에 엔드포인트가 없다. 서울로 호출하면
    'Could not connect to the endpoint URL' 에러. (2026-06-30 검증 완료)
  - S3·Bedrock은 서울인데 geocode만 도쿄다. 절대 서울로 바꾸지 말 것.

■ ★한글 주소는 매칭 안 됨★ — 영문 행정구역명을 던져야 한다. (검증 완료)
  - geo-places는 한국 도로명/번지를 못 읽고, 한글 주소는 빈 결과를 반환한다.
  - 우리 용도(신장 경계 판정)는 행정구역(구/시/도) 정밀도면 충분하므로,
    region(행정구역)만 영문화해서 'region_en, country' 형태로 던진다.

■ IntendedUse="Storage" 필수
  - 좌표를 DB에 저장하므로 약관상 Storage 모드 명시 필요. SingleUse면 위반.

■ 실패 시 None 반환 — 에러로 막지 않는다.
  - 주소 모호·매칭 실패·예외 → None. 호출자는 location을 NULL로 두면 된다.
  - 좌표가 없다고 마스터폼 제출 자체가 실패하면 안 된다(부분 입력 허용).
"""
from __future__ import annotations

import asyncio

import boto3

# ─────────────────────────────────────────────────────────
# ★리전 도쿄 고정★ — geo-places는 서울 미지원. 위 주석 참조.
# ─────────────────────────────────────────────────────────
GEO_REGION = "ap-northeast-1"

# boto3 client는 스레드 안전 — 모듈 레벨 1회 생성. 자격증명은 IAM Role 자동.
_geo_client = boto3.client("geo-places", region_name=GEO_REGION)


# ─────────────────────────────────────────────────────────
# region 정적 매핑 (한글/현지어 → 영문 행정구역명)
# 데모·자주 나오는 행정구역만. 없으면 Bedrock Haiku로 fallback.
# 키는 입력 그대로(공백·표기 흔들림 대비해 .strip()으로 비교).
# ※ 데모 시나리오의 실제 region이 확정되면 여기에 추가할 것.
# ─────────────────────────────────────────────────────────
_REGION_MAP: dict[str, str] = {
    # 중국 — 신장(UFLPA 핵심) 및 주요 지역
    "신장": "Xinjiang",
    "신장위구르자치구": "Xinjiang",
    "신장 위구르 자치구": "Xinjiang",
    "우루무치": "Urumqi",
    "카슈가르": "Kashgar",
    "상하이": "Shanghai",
    "광둥": "Guangdong",
    # 콩고민주공화국 — 분쟁광물(DRC)
    "카탕가": "Katanga",
    "콜웨지": "Kolwezi",
    # 한국 주요 행정구역 (필요 시 계속 추가)
    "서울": "Seoul",
    "강남구": "Gangnam-gu",
    "경기도": "Gyeonggi-do",
    "충청북도": "Chungcheongbuk-do",
    "전라남도": "Jeollanam-do",
    "울산": "Ulsan",
    "부산": "Busan",
}


def _is_ascii(s: str) -> bool:
    """이미 영문(ASCII)이면 변환 불필요."""
    return s.isascii()


async def _translate_region(region: str) -> str:
    """
    한글/현지어 region → 영문 행정구역명.
    1) 이미 영문이면 그대로  2) 정적 매핑 hit이면 매핑값
    3) miss면 Bedrock Haiku로 1줄 번역 (공통 팩토리 재사용).
    번역 실패 시 원본을 그대로 반환(최소한 영문이면 통과할 수도 있으니).
    """
    r = region.strip()
    if _is_ascii(r):
        return r
    if r in _REGION_MAP:
        return _REGION_MAP[r]

    # ── Bedrock Haiku fallback (매핑에 없는 임의 region만 도달) ──
    try:
        from backend.llm.bedrock_factory import get_llm, Model

        llm = get_llm(Model.HAIKU_45, temperature=0.0, max_tokens=64)
        prompt = (
            "Translate this administrative region name to its standard English "
            "romanization used by international maps. Return ONLY the English name, "
            "no explanation, no punctuation.\n\n"
            f"Region: {r}"
        )
        resp = await llm.ainvoke(prompt)
        out = (resp.content if isinstance(resp.content, str) else str(resp.content)).strip()
        return out or r
    except Exception:
        # 번역 실패해도 흐름을 막지 않는다 — 원본 반환.
        return r


async def geocode_address(
    address: str | None,
    country: str | None = None,
    region: str | None = None,
) -> tuple[float, float] | None:
    """
    주소 → (lat, lng). 실패하면 None (에러를 던지지 않는다).

    geo-places는 한국·중국 도로명을 못 읽으므로, 행정구역(region) 수준으로 던진다.
    - region을 영문화하고, country(ISO alpha-2)로 결과를 해당 국가로 한정한다.
    - query = "region_en, country" (region 없으면 address 원문을 fallback으로).

    Returns:
        (latitude, longitude) | None
    """
    # 쿼리 텍스트 구성: region(영문화) 우선, 없으면 address 원문.
    query_parts: list[str] = []
    if region:
        query_parts.append(await _translate_region(region))
    elif address:
        query_parts.append(address)  # region이 없을 때만 원문 주소 (영문일 가능성에 기대)

    if not query_parts:
        return None

    query_text = ", ".join(query_parts)

    # country 필터 — ISO alpha-2(KR/CN 등)를 받지만 geo-places는 alpha-3을 쓴다.
    # 매핑 후 IncludeCountries 필터로 결과를 해당 국가로 한정.
    geo_filter = None
    if country:
        cc = _ALPHA2_TO_ALPHA3.get(country.strip().upper())
        if cc:
            geo_filter = {"IncludeCountries": [cc]}

    def _call() -> tuple[float, float] | None:
        kwargs = {
            "QueryText": query_text,
            "IntendedUse": "Storage",   # ★DB 저장 용도 — 필수★
            "MaxResults": 1,
        }
        if geo_filter:
            kwargs["Filter"] = geo_filter
        resp = _geo_client.geocode(**kwargs)
        items = resp.get("ResultItems") or []
        if not items:
            return None
        # Position은 [경도(lng), 위도(lat)] 순서 (GeoJSON 관례). ★뒤집힘 주의★
        pos = items[0].get("Position")
        if not pos or len(pos) != 2:
            return None
        lng, lat = pos[0], pos[1]
        return (lat, lng)

    try:
        return await asyncio.to_thread(_call)
    except Exception:
        # 매칭 실패·throttle·네트워크 등 모든 예외 → None. 흐름 차단 금지.
        return None


# ISO 3166-1 alpha-2 → alpha-3 (geo-places의 IncludeCountries는 alpha-3).
# 우리 데이터에 나오는 국가만. 없으면 country 필터를 생략한다.
_ALPHA2_TO_ALPHA3: dict[str, str] = {
    "KR": "KOR",
    "CN": "CHN",
    "JP": "JPN",
    "US": "USA",
    "DE": "DEU",
    "VN": "VNM",
    "ID": "IDN",
    "CD": "COD",  # 콩고민주공화국 (DRC)
    "CL": "CHL",
    "AU": "AUS",
}
