"""
ci/test_e2e.py  (W5 — 기능 시스템 테스트: '누적형' e2e 스위트)

[이 파일의 정체성 — "그날 뭐 만들었는지 모르면 기능 테스트가 어렵다"의 해법]
시스템 테스트는 매일 새로 짜는 게 아니다. '기능을 만들 때 그 기능의 e2e 함수 한 개를
여기 추가'하고, 하루 끝에 '전체를 다시 돌린다'. 그러면 오늘 만든 것뿐 아니라 지난 모든
기능이 매일 재검증된다(회귀 방지). "오늘 뭐 만들었나"는 git diff가 이미 기록하므로,
verify.ps1이 오늘 추가된 라우트를 체크리스트로 띄운다 — 그중 여기 커버 안 된 게
보이면 함수 하나를 더하면 된다.

test_smoke.py 와의 차이:
  - smoke  : 엔드포인트가 '살아있나'(생존). 라우터 누락 회귀 방지.
  - e2e    : 기능이 '실제로 동작하나'(행위). write→read 왕복으로 결과까지 확인.

실행: BASE_URL=http://localhost pytest ci/test_e2e.py -v
의존: docker compose 스택 기동 + 시드 데이터(02_seed_data.sql) 로드(down -v && up --build).
"""
import os

import httpx
import pytest

BASE_URL = os.getenv("BASE_URL", "http://localhost")
TIMEOUT = 15.0


@pytest.fixture(scope="session")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT, follow_redirects=True) as c:
        yield c


@pytest.fixture(scope="session")
def a_supplier_id(client):
    """
    시드된 협력사 하나를 잡아 대상 supplier_id로 쓴다(테넌트 생성 의존 회피).
    프레시 스택(down -v && up --build)이면 02_seed_data.sql의 협력사가 있어야 한다.
    """
    resp = client.get("/suppliers", params={"size": 1})
    assert resp.status_code == 200, f"/suppliers {resp.status_code} — 시드/라우터 확인"
    items = resp.json()
    assert items, "시드 협력사가 없음 — `docker compose down -v && up --build`로 시드 로드 필요"
    return items[0]["supplier_id"]


# ============================================================
# 기능: 마스터폼 분배 저장 (MF · 2026-06-23) — 섹션 0~2 + atomic 오케스트레이션
# ============================================================
def test_masterform_atomic_distribution(client, a_supplier_id):
    """
    POST /master-form 한 번에 섹션 0(회사·공장·PIC) + 1(탄소·factory_carbon_declarations)
    + 2(재활용)을 보내면, 각 도메인 테이블에 단일 트랜잭션으로 atomic 분배 저장되고
    저장된 섹션 키가 응답에 반영된다. 이어서 /factories 로 공장이 실제로 저장됐는지
    (POINT 좌표 보존 포함) 왕복 확인한다.
    """
    payload = {
        "company": {"company_name": "E2E 재활용", "supplier_type": "recycler"},
        "factories": [{
            "factory_name": "E2E 처리장",
            "country": "KR",
            "coordinates": {"latitude": 36.019, "longitude": 129.343},
        }],
        "contacts": [{"name": "담당자", "email": "pic@e2e.test", "is_primary": True}],
        "manufacturing": {
            "carbon_intensity": 12.5,
            "factory_declarations": [
                {"factory_index": 0, "carbon_intensity": 12.5, "declared_at": "2026-01-01"},
            ],
        },
        "recycling": {"recycled_content_ratio": 30.0, "recycling_certification": "ISO 9001"},
    }
    resp = client.post(f"/suppliers/{a_supplier_id}/master-form", json=payload)
    assert resp.status_code == 200, f"master-form {resp.status_code}: {resp.text}"

    saved = resp.json()["sections_saved"]
    for section in ("company", "factories", "contacts", "manufacturing", "recycling"):
        assert section in saved, f"섹션 '{section}' 미저장: {saved}"

    # 공장이 실제로 분배 저장됐는지 + 좌표(lat/lng) 보존 확인 (write→read 왕복)
    fres = client.get(f"/suppliers/{a_supplier_id}/factories")
    assert fres.status_code == 200
    factories = fres.json()["factories"]
    e2e = [f for f in factories if f["factory_name"] == "E2E 처리장"]
    assert e2e, f"공장 분배 저장 실패: {[f['factory_name'] for f in factories]}"
    assert abs((e2e[0].get("latitude") or 0) - 36.019) < 0.01, "좌표 lat 보존 실패"


def test_masterform_prefill_path(client, a_supplier_id):
    """
    AP: GET /master-form/prefill 이 200으로 prefill 구조를 반환한다.
    추출결과가 없으면 document_count=0, 빈 prefill(업로드 전 정상 상태) — 경로 생존 검증.
    """
    resp = client.get(f"/suppliers/{a_supplier_id}/master-form/prefill")
    assert resp.status_code == 200, f"prefill {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "prefill" in body and "low_confidence_fields" in body
    assert body["document_count"] >= 0


# ============================================================
# 기능: E2 협력사 7탭 축소 (2026-06-24) — 데모서 핵심 3탭만 노출
# ============================================================
def test_e2_supplier_tab_reduction(client, a_supplier_id):
    """
    E2: 협력사 상세 모달을 데모에서 핵심 3탭(detail/factories/risk)만 노출하고
    esg/training/reliability는 숨긴다(SUPPLIER_DEMO_MODE=True 기준).

    검증:
      1) GET /suppliers/_meta/tabs 가 노출/숨김 SSOT를 반환한다.
      2) 노출 탭(detail/factories/risk-profile)은 정상 응답(200, 또는 데이터 없으면 404).
      3) 숨긴 탭(esg/training/reliability)은 데모에서 404로 가려진다.
    데모 모드를 끈 환경(SUPPLIER_DEMO_MODE=false)이면 숨김 탭도 살아나므로,
    메타가 알려주는 visible/hidden 목록을 기준으로 단언한다(환경에 무관하게 일관).
    """
    meta = client.get("/suppliers/_meta/tabs")
    assert meta.status_code == 200, f"_meta/tabs {meta.status_code}: {meta.text}"
    body = meta.json()
    visible, hidden = set(body["visible"]), set(body["hidden"])
    # 핵심 3탭은 항상 노출, 둘은 상호배타.
    assert {"detail", "factories", "risk"} <= visible
    assert not (visible & hidden)

    # 숨긴 탭은 404로 가려진다(존재하지 않는 것처럼).
    tab_to_path = {
        "esg": f"/suppliers/{a_supplier_id}/esg",
        "training": f"/suppliers/{a_supplier_id}/training",
        "reliability": f"/suppliers/{a_supplier_id}/reliability",
    }
    for tab, path in tab_to_path.items():
        resp = client.get(path)
        if tab in hidden:
            assert resp.status_code == 404, f"{tab} 숨김인데 {resp.status_code} — 가드 누락"
        else:
            assert resp.status_code == 200, f"{tab} 노출인데 {resp.status_code}: {resp.text}"

    # 노출 탭은 라우터가 살아있어야 한다(데이터 유무와 무관하게 405/404 아닌 200 기대).
    assert client.get(f"/suppliers/{a_supplier_id}/factories").status_code == 200


# ════════════════════════════════════════════════════════════
# 새 기능을 만들면 아래에 e2e 함수를 한 개 추가하세요 (누적 스위트가 매일 재검증).
#   def test_<기능>_<날짜>(client, a_supplier_id): ...
# ════════════════════════════════════════════════════════════
