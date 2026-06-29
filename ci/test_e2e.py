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

from BACK.backend.infrastructure.security import create_access_token

BASE_URL = os.getenv("BASE_URL", "http://localhost")
TIMEOUT = 15.0

# 시드(02_seed_data.sql)의 원청 관리자 — suppliers를 소유한 테넌트.
# 테넌트 격리(§0.2)로 /suppliers 등 보호 라우트는 Bearer 토큰이 필요해졌다.
# SECRET_KEY는 서버(docker)와 동일한 .env를 공유하므로 여기서 발급한 토큰을 서버가 그대로 검증한다.
SEED_USER_ID = "11111111-0000-4000-8000-000000000001"
SEED_TENANT_ID = "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
SEED_ROLE = "admin"


@pytest.fixture(scope="session")
def auth_token():
    """시드 원청 유저로 JWT 발급(tenant_id 클레임 포함). get_current_user가 읽는 sub/role/tenant_id."""
    return create_access_token(
        {"sub": SEED_USER_ID, "role": SEED_ROLE, "tenant_id": SEED_TENANT_ID}
    )


@pytest.fixture(scope="session")
def client(auth_token):
    # 모든 요청에 Authorization 헤더를 기본 첨부 → /suppliers·master-form·factories 등 보호 라우트 통과.
    headers = {"Authorization": f"Bearer {auth_token}"}
    with httpx.Client(
        base_url=BASE_URL, timeout=TIMEOUT, follow_redirects=True, headers=headers
    ) as c:
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
        "company": {"company_name": "E2E 재활용", "provider_type": "recycler"},
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
# 기능: R10 data_gateway supplier_ids 산출 가드 (2026-06-24)
# ============================================================
# 4대 데모 시나리오 제품 (02_seed_data.sql §products). data_gateway는
# get_n_tier_supply_chain(product_id)의 child_supplier_id로 supplier_ids를 만들고,
# verification·risk가 이를 필수 입력으로 받는다 → 트리가 비면 전 판정이 깨진다.
# /supply-chain/tree 가 같은 재귀 쿼리를 쓰므로, 이 엔드포인트의 child_supplier_id
# 집합이 곧 data_gateway가 수집할 supplier_ids다. (누가 시드/hop을 건드려 트리를
# 끊으면 이 테스트가 즉시 잡는다 — R10 회귀 가드.)
_SCENARIO_PRODUCT_IDS = {
    "① iX3 (Happy)":  "d1111111-0000-4000-8000-000000000001",
    "② i4 (Gray)":    "d2222222-0000-4000-8000-000000000002",
    "③ GLC (Sad)":    "d3333333-0000-4000-8000-000000000003",
    "④ EQS (Happy)":  "d4444444-0000-4000-8000-000000000004",
}


@pytest.mark.parametrize("label,product_id", list(_SCENARIO_PRODUCT_IDS.items()))
def test_r10_supply_tree_yields_supplier_ids(client, label, product_id):
    """
    R10 DoD: 4시나리오 모두 supplier_ids(=트리 child_supplier_id 집합)가 비어있지 않다.
    트리가 비면 verification(FEOC ANY(:sids) 빈집합 → 헛통과)·risk(supplier_ids[0] →
    IndexError)가 깨지므로, 빈 트리를 배포 게이트에서 차단한다.
    """
    resp = client.get("/supply-chain/tree", params={"product_id": product_id})
    assert resp.status_code == 200, f"{label} tree {resp.status_code}: {resp.text}"
    nodes = resp.json()
    assert isinstance(nodes, list) and nodes, f"{label}: 공급망 트리가 비었음 — 시드/hop 연속성 확인"

    supplier_ids = {n["child_supplier_id"] for n in nodes if n.get("child_supplier_id")}
    assert supplier_ids, f"{label}: child_supplier_id가 전부 비었음 — data_gateway supplier_ids 공집합"


# ════════════════════════════════════════════════════════════
# 기능: 담당 D delta 추가확인 조치 (2026-06-26)
#   10.2a suppliers[] tenant_id 제거 + provider_type 정합 / 5.5 capaId 404 / 5.4 소유권 선검사
# ════════════════════════════════════════════════════════════
def test_supply_chain_map_provider_type_no_tenant(client):
    """
    10.2a: GET /products/{id}/supply-chain-map.
      - suppliers[] 노드에 내부 필드 tenant_id 가 없어야 한다(응답 정리).
      - provider_type 키가 있어야 한다(supplier_type→provider_type 리네임 정합 — 안 됐으면 쿼리 500).
    시드 GLC(d3333333…, seed 테넌트 소유, supply_chain_map 7건)로 검증.
    """
    pid = "d3333333-0000-4000-8000-000000000003"
    resp = client.get(f"/products/{pid}/supply-chain-map")
    assert resp.status_code == 200, f"map {resp.status_code}: {resp.text}"
    suppliers = resp.json()["suppliers"]
    assert suppliers, "맵 suppliers[]가 비었음 — 시드/테넌트 소유 확인"
    for s in suppliers:
        assert "tenant_id" not in s, f"내부 tenant_id가 응답에 노출됨: {list(s.keys())}"
        assert "provider_type" in s, f"provider_type 누락(리네임 정합 실패): {list(s.keys())}"


def test_due_diligence_capa_not_found_404(client, a_supplier_id):
    """
    5.5: 존재하지 않는 capaId 상태 갱신 → 404.
    (수정 전: corrective_actions 에 매칭 0건이어도 UPDATE가 행을 반환해 조용한 200 no-op)
    seed 협력사로 실사를 생성(테넌트 소유)해 audit 존재는 보장하고, capaId만 없는 상황을 만든다.
    """
    cr = client.post(
        "/due-diligence",
        json={"supplier_id": a_supplier_id, "name": "E2E 실사", "scope": "E2E 검증"},
    )
    assert cr.status_code == 201, f"create {cr.status_code}: {cr.text}"
    audit_id = cr.json()["audit_id"]
    pr = client.patch(
        f"/due-diligence/{audit_id}/capa/no-such-capa-id", json={"status": "완료"}
    )
    assert pr.status_code == 404, f"없는 capaId는 404여야 함, got {pr.status_code}: {pr.text}"


def test_due_diligence_report_ownership_before_s3(client):
    """
    5.4: 존재하지 않는 auditId 로 보고서 업로드 → 소유권 선검사로 404.
    (수정 전: S3 업로드를 먼저 시도해 NoCredentialsError 500. 수정 후: 검사 먼저 → 404)
    happy-path(실제 S3 저장)는 로컬 S3 자격증명 부재로 보류 — 본 테스트는 '검사 순서'만 보장한다.
    """
    import uuid

    bogus = str(uuid.uuid4())
    files = {"file": ("r.pdf", b"%PDF-1.4 test", "application/pdf")}
    resp = client.patch(
        f"/due-diligence/{bogus}/report",
        files=files,
        data={"result": "pass", "score": "90"},
    )
    assert resp.status_code == 404, f"소유권 선검사로 404여야 함(S3 이전), got {resp.status_code}: {resp.text}"


# ════════════════════════════════════════════════════════════
# 새 기능을 만들면 아래에 e2e 함수를 한 개 추가하세요 (누적 스위트가 매일 재검증).
#   def test_<기능>_<날짜>(client, a_supplier_id): ...
# ════════════════════════════════════════════════════════════
