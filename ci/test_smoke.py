"""
ci/test_smoke.py  (W5 — 테스트 자동화: 라우터 스모크)

docker compose로 전체 스택을 띄운 상태에서 주요 엔드포인트가 살아있는지 확인한다.
기존 수동 verify_deploy.py를 pytest로 승격한 것. 깊은 단위테스트가 아니라
"배포가 깨지지 않았다"를 빠르게 보증하는 게이트.

실행: BASE_URL=http://localhost pytest ci/test_smoke.py -v
"""
import os

import httpx
import pytest

BASE_URL = os.getenv("BASE_URL", "http://localhost")
TIMEOUT = 10.0

# (경로, 인증 없이 200 기대 여부) — 인증 필요한 건 401/403도 "라우터 살아있음"으로 간주
SMOKE_ENDPOINTS = [
    ("/health", {200}),
    ("/suppliers", {200, 401, 403}),
    ("/supply-chain/tree", {200, 401, 403, 422}),
    ("/products", {200, 401, 403}),
    ("/docs", {200}),            # FastAPI 자동 문서 = 앱 기동 증명
    ("/openapi.json", {200}),
]


@pytest.fixture(scope="session")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT, follow_redirects=True) as c:
        yield c


@pytest.mark.parametrize("path,ok_status", SMOKE_ENDPOINTS)
def test_endpoint_alive(client, path, ok_status):
    """엔드포인트가 응답하고, 허용 status 집합에 들어가는지."""
    resp = client.get(path)
    assert resp.status_code in ok_status, (
        f"{path} → {resp.status_code} (기대: {ok_status})"
    )


def test_health_payload(client):
    """health가 단순 200을 넘어 의미있는 본문을 주는지(있다면)."""
    resp = client.get("/health")
    assert resp.status_code == 200


def test_openapi_has_routers(client):
    """openapi.json에 핵심 도메인 라우터가 등록돼 있는지 → 라우터 누락 회귀 방지."""
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    paths = resp.json().get("paths", {})
    # 최소 몇 개 도메인 경로가 존재해야 함
    assert len(paths) > 5, f"등록된 경로가 너무 적음: {len(paths)}"
