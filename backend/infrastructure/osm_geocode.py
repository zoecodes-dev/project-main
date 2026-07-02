"""
infrastructure/osm_geocode.py — OpenStreetMap/Nominatim 지오코딩 프리미티브.

■ 왜 OSM인가 (AWS geo-places 대안)
  - AWS geo-places는 중국(신장·카슈가르 등)을 좌표로 못 잡는다(실측 None). 바이두/가오더는
    중국 실명인증 벽으로 한국서 계정 개설 불가. OSM/Nominatim은 무료·계정 불필요·WGS-84·
    중국 커버(카슈가르 검증)를 동시에 만족하는 현실안.
  - 지명/주소 → 좌표(WGS-84) + 구조화된 행정계층(예: 喀什市→喀什地区→新疆维吾尔自治区)을 반환.

■ 사용정책 주의 (공개 Nominatim)
  - User-Agent 헤더 필수, 초당 1건. 운영/대량은 self-host Nominatim 또는 무료티어
    제공자(LocationIQ/Geoapify/MapTiler)로 전환. 데모/저볼륨은 공개 엔드포인트로 충분.

■ 동기 HTTP는 asyncio.to_thread로 감싼다(이벤트 루프 비차단 — storage.py 패턴).
■ 실패 시 None (흐름 비차단).
"""
from __future__ import annotations

import asyncio
import json
import math
import urllib.parse
import urllib.request

# 공개 Nominatim. 운영 전환 시 self-host/무료티어 URL로 교체.
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
# 역지오코딩(좌표→행정구역/국가) — 픽커 핀 확정 후 폼 자동입력용.
NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
# ★Nominatim 정책상 식별 가능한 User-Agent 필수★ (연락처는 배포 시 실제 값으로 교체)
_USER_AGENT = "KIRA-supplychain-compliance/1.0 (contact: ops@kira.example)"
_TIMEOUT = 8

# 좌표 대조 기본 임계(km). 신장 50km DWithin 관례와 맞춤 — 필요 시 호출부에서 override.
GEO_MISMATCH_THRESHOLD_KM = 50.0


def _parse_item(it: dict) -> dict:
    """Nominatim 결과 1건 → 표준 dict. (단일/다중 조회 공용)"""
    display = it.get("display_name", "") or ""
    addr = it.get("address", {}) or {}
    # 행정구역 — 나라마다 키가 다름(중국=state 新疆 / 한국·독일·일본=city / 인니=region / DRC=state).
    # per-country 매핑 대신 '거친→세밀' 순서 폴백으로 어느 나라든 비지 않게 한다(실측 6개국 커버).
    # ※ 자동입력 편의값(수정가능)일 뿐, 컴플라이언스 판정(is_xinjiang)은 display_name 전체를 봐서 무관.
    admin = (addr.get("state") or addr.get("region") or addr.get("province")
             or addr.get("county") or addr.get("city") or addr.get("municipality")
             or addr.get("town") or addr.get("village") or "")
    hay = f"{display} {admin}"
    return {
        "lat": float(it["lat"]),
        "lon": float(it["lon"]),
        "display_name": display,
        "admin": admin,
        "country_code": (addr.get("country_code") or "").upper(),
        # 신장 여부 — 해석된 행정계층에 新疆/Xinjiang이 있으면 True (좌표 판정과 별개의 강한 신호)
        "is_xinjiang": ("新疆" in hay) or ("Xinjiang" in hay) or ("شىنجاڭ" in hay),
    }


def _search(query: str, country_code: str | None = None, limit: int = 1) -> list[dict]:
    """Nominatim 검색 → 후보 리스트(상위 limit개). 동기 호출(호출부에서 to_thread)."""
    q = {
        "q": query,
        "format": "jsonv2",
        "limit": limit,
        "addressdetails": 1,
    }
    # ★국가 앵커링★ — 없으면 자유입력이 엉뚱한 나라로 오매칭('Hanoi Vietnam'→헬싱키 사례).
    # ISO alpha-2를 넘기면 그 나라로 결과를 한정한다(Nominatim countrycodes, 소문자).
    if country_code:
        q["countrycodes"] = country_code.strip().lower()
    params = urllib.parse.urlencode(q)
    req = urllib.request.Request(
        f"{NOMINATIM_URL}?{params}",
        headers={"User-Agent": _USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return [_parse_item(it) for it in (data or [])]


def _call(query: str, country_code: str | None = None) -> dict | None:
    """단일 최상위 후보(기존 geocode_osm 용). 후보 없으면 None."""
    items = _search(query, country_code, limit=1)
    return items[0] if items else None


async def geocode_osm(query: str | None, country_code: str | None = None) -> dict | None:
    """
    지명/주소 → {lat, lon, display_name, admin, country_code, is_xinjiang} | None.
    country_code(ISO alpha-2, 예 'CN'/'VN')를 주면 그 나라로 결과를 한정해 오매칭을 막는다.
    실패(빈 입력·매칭 실패·네트워크·타임아웃) → None. 흐름을 막지 않는다.
    """
    if not query or not query.strip():
        return None
    try:
        return await asyncio.to_thread(_call, query.strip(), country_code)
    except Exception:
        return None


async def geocode_candidates(
    query: str | None, country_code: str | None = None, limit: int = 5
) -> list[dict]:
    """
    지명/주소 → 후보 리스트 [{lat, lon, display_name, admin, country_code, is_xinjiang}, ...].

    동명 지명 해소용. 픽커(프론트)가 이 목록을 지도에 띄워 사용자가 맞는 곳을 고르게 한다.
    (예: "Franklin" US → TX/PA/IL/FL/MO 여러 카운티가 후보로 나옴)
    - country_code(alpha-2)를 주면 그 나라로 한정, 없으면 전세계 동명 후보를 반환.
    - 각 후보에 admin(행정구역)·country_code·is_xinjiang을 붙여 사용자가 구분/판정 가능.
    - limit은 1~10으로 클램프. 실패·빈 입력 → 빈 리스트.
    """
    if not query or not query.strip():
        return []
    try:
        return await asyncio.to_thread(_search, query.strip(), country_code, max(1, min(limit, 10)))
    except Exception:
        return []


def _reverse(lat: float, lon: float) -> dict | None:
    """좌표 → 표준 dict(_parse_item 재사용). 실패/에러 → None."""
    params = urllib.parse.urlencode({
        "lat": lat, "lon": lon, "format": "jsonv2", "addressdetails": 1,
    })
    req = urllib.request.Request(
        f"{NOMINATIM_REVERSE_URL}?{params}",
        headers={"User-Agent": _USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    # 역지오코딩 실패 시 Nominatim은 {"error": ...}를 준다.
    if not data or "error" in data:
        return None
    return _parse_item(data)


async def reverse_geocode_osm(lat: float | None, lon: float | None) -> dict | None:
    """
    좌표 → {lat, lon, display_name, admin, country_code, is_xinjiang} | None.

    픽커에서 사용자가 확정한 핀의 국가·행정구역을 역추출해 폼 자동입력(country/region/address)에 쓴다.
    핀이 authoritative — 반환 country_code/admin으로 기존 입력값을 갱신하면 된다.
    실패·좌표 없음 → None(흐름 비차단).
    """
    if lat is None or lon is None:
        return None
    try:
        return await asyncio.to_thread(_reverse, float(lat), float(lon))
    except Exception:
        return None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """두 WGS-84 좌표 간 대권거리(km). 신고좌표 ↔ 증빙주소좌표 대조 임계 판정용."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
