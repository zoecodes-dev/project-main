# DPP (Digital Product Passport) Domain

KIRA 플랫폼에서 배터리 제품의 **디지털 제품 여권(DPP) 발행 및 Readiness(준비도) 평가**를 담당하는 핵심 도메인입니다. 공식 규제 문서 관점에서 매우 엄격한 데이터 무결성과 이중 가드 구조를 가집니다.

## 1. 8대 체크리스트 명세 (Readiness 평가)
DPP 발행을 위해서는 공급망 전체에 걸쳐 아래 8가지 조건이 **모두 충족(True)** 되어야 합니다. 단 하나라도 위반 시 점수는 1.0 미만이 되며 발행이 차단됩니다.

1. `all_tiers_completeness`: 모든 공급망 Tier의 데이터 완성도(`completion_rate`)가 80% 이상일 것.
2. `no_violations`: 승인 거절(`rejected`) 또는 컴플라이언스 위반(`violation`) 건이 0건일 것.
3. `origin_certs_valid`: 수집된 원산지포괄확인서 중 만료(`expired`)된 서류가 0건일 것.
4. `certifications_valid`: 필수 인증서(ISO 등) 중 만료된 서류가 0건일 것.
5. `training_completed`: 필수 의무 교육 미이수(`overdue`) 건이 0건일 것.
6. `no_open_human_rights`: 미해결 상태(`open`)인 인권 이슈가 0건일 것.
7. `no_open_accidents`: 현재 조사 중(`investigating`)인 중대 산업 재해가 0건일 것.
8. `trader_disclosure_ok`: 트레이더(중개상)의 상위 공급망 정보 공개율(`disclosure_completeness`)이 75% 이상일 것.

## 2. 불변 가드 (Immutable 이중 방어)
DPP는 법적 효력을 가지는 공식 문서이므로, 한 번 발행된(`dpp_issued`) 기록은 어떠한 경우에도 수정될 수 없어야 합니다.
* **앱 레벨 (1차 가드):** `immutable_guard.py`의 `assert_not_issued()`가 UPDATE 시도를 사전에 차단하여 `ImmutableRecordError` 예외를 발생시킴.
* **DB 레벨 (2차 가드):** `01_schema.sql`에 정의된 `prevent_issued_dpp_update` 트리거가 직접적인 쿼리 조작까지 물리적으로 방어.

## 3. API 응답 예시 (Readiness 시나리오)

### ❌ 실패 시나리오 (준비도 미달)
```json
{
  "product_id": "11111111-1111-1111-1111-111111111111",
  "readiness_score": 0.88,
  "breakdown": {
    "all_tiers_completeness": true,
    "no_violations": true,
    "origin_certs_valid": false, 
    "certifications_valid": true,
    "training_completed": true,
    "no_open_human_rights": true,
    "no_open_accidents": true,
    "trader_disclosure_ok": true
  }
}
```
*(원산지 증명서 만료로 인해 1.0 달성 실패)*

### ✅ 성공 시나리오 (발행 가능)
```json
{
  "product_id": "11111111-1111-1111-1111-111111111111",
  "readiness_score": 1.0,
  "breakdown": {
    "all_tiers_completeness": true,
    "no_violations": true,
    "origin_certs_valid": true,
    "certifications_valid": true,
    "training_completed": true,
    "no_open_human_rights": true,
    "no_open_accidents": true,
    "trader_disclosure_ok": true
  }
}
```
