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

■ ★중국·신장은 OSM(Nominatim) fallback★ (2026-07-02 배선)
  - geo-places는 중국(특히 신장)을 좌표로 못 잡고 None을 반환한다(실측). 그때
    osm_geocode로 fallback해 실좌표를 얻는다. OSM은 신장을 정확히 잡음(Kashgar 등 실측).
  - OSM 반환은 {"lat","lon"} 라벨 dict → (lat, lng)로 그대로 매핑한다. AWS처럼
    pos[0]/pos[1]을 뒤집으면 안 됨(이미 라벨돼 있음 — 이중 뒤집기 = 좌표 오염).
  - 성(省) 이름만 받으면 OSM은 성 중심점(region 수준 근사)을 준다. 신장 경계 판정엔
    충분하나 '공장 실측 위치'는 아니므로 정밀 좌표로 취급하지 말 것.

■ IntendedUse="Storage" 필수
  - 좌표를 DB에 저장하므로 약관상 Storage 모드 명시 필요. SingleUse면 위반.

■ 실패 시 None 반환 — 에러로 막지 않는다.
  - 주소 모호·매칭 실패·예외 → None. 호출자는 location을 NULL로 두면 된다.
  - 좌표가 없다고 마스터폼 제출 자체가 실패하면 안 된다(부분 입력 허용).
"""
from __future__ import annotations

import asyncio

import boto3

from backend.infrastructure.osm_geocode import geocode_osm

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

    # ★국가 앵커 필수★ — region 수준 지오코딩은 country 없이는 신뢰 불가.
    #   필터 없는 AWS는 "Kashgar"→런던, OSM은 "Hanoi"→헬싱키 식 오답을 준다(2026-07-02 실측).
    #   앵커가 없으면 엉뚱한 좌표를 저장하느니 NULL(None)로 두는 게 안전.
    country_norm = country.strip().upper() if country else None
    if not country_norm:
        return None

    # AWS는 alpha-3 IncludeCountries 필터가 있을 때만 시도한다(필터 없이는 오답 위험).
    #   alpha-2가 매핑에 없으면 AWS는 건너뛰고 바로 OSM으로 간다(OSM은 alpha-2로 앵커).
    alpha3 = _ALPHA2_TO_ALPHA3.get(country_norm)
    aws_result = None
    if alpha3:
        def _call() -> tuple[float, float] | None:
            resp = _geo_client.geocode(
                QueryText=query_text,
                IntendedUse="Storage",       # ★DB 저장 용도 — 필수★
                MaxResults=1,
                Filter={"IncludeCountries": [alpha3]},
            )
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
            aws_result = await asyncio.to_thread(_call)
        except Exception:
            # 매칭 실패·throttle·네트워크 등 모든 예외 → None으로 간주(흐름 차단 금지).
            aws_result = None
    if aws_result is not None:
        return aws_result

    # ── AWS 미스(geo-places가 못 잡는 중국·신장 등) → OSM(Nominatim) fallback ──
    #   query_text는 이미 영문화된 region(신장→Xinjiang 등)이라 OSM 매칭에 유리.
    #   country_norm(alpha-2)로 결과를 해당 국가로 한정(geocode_osm이 소문자 countrycodes 처리).
    #   OSM 반환 {"lat","lon"}을 (lat, lng)로 그대로 매핑 — 추가 뒤집기 금지.
    try:
        osm = await geocode_osm(query_text, country_norm)
    except Exception:
        osm = None
    if osm is not None:
        return (osm["lat"], osm["lon"])
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
