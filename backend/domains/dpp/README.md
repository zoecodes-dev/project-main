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

## 4. 추가 API 엔드포인트 스펙 (조회용)
프론트엔드 DPP 이력 대시보드를 위한 조회 API 세트입니다.
* **`GET /dpp/records`**: 발행이 완료된 전체 DPP 이력(목록)을 반환합니다. 
  - 쿼리 파라미터 `?customer_id=` 를 통해 특정 고객사 마스터별 스캔 및 추출이 가능합니다.
* **`GET /dpp/records/{dpp_id}`**: 80여 개의 필드가 담긴 DPP Payload 상세 데이터를 반환합니다.
  - **인터페이스 동기화**: 응답 객체의 `payload.product_info` 내에 은지님이 개편한 고객사 식별자(`customer_id`), 고객사명(`customer_name`), 배터리 모델명(`model_name`), 단위 암페어 용량(`amperage_ah`) 데이터가 정밀하게 포함되어 반환됩니다.
*(모든 조회 API는 `@trace_tool` 데코레이터를 통해 행위가 추적됩니다.)*

## 5. 대외 전송 양식 자동 생성 (Delivery Preview)
- 발급이 완료된 DPP를 바이어(고객사)에게 메일이나 메시지로 전달하기 위해, 사람이 직접 확인하고 발송할 수 있는 미리보기 템플릿(양식)을 API로 제공합니다.
- **엔드포인트**: `GET /dpp/{dpp_id}/delivery-form`
- **도메인 격리 준수**: `customers` 정보 조회를 위해 타 도메인 모델을 직접 Import하지 않고, 기존에 작성된 Raw SQL 헬퍼(`get_score_raw_data`)를 재사용하여 고객사 명칭과 QR 코드 주소를 안전하게 주입합니다.

## 6. 대외 발송 이력 추적 (Delivery History)
- 수동 또는 반자동으로 대외 발송이 완료된 후, 해당 발송 이력(수신처, 발송자, 제목, 본문, 발송일시)을 영구 기록하여 규제 당국의 발송 증빙 요구에 대응합니다.
- **엔드포인트**: `POST /dpp/{dpp_id}/deliver`
- 전송 이력은 `dpp_delivery_history` 테이블에 안전하게 적재되며, 존재하지 않는 사용자의 토큰으로 요청 시 참조 무결성 위반을 방어하도록 설계되어 있습니다.
