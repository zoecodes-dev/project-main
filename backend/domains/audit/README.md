# Audit Domain — README

담당: 윤지혜(A) | Audit

---

## 1. 역할

파이프라인 각 단계의 실행을 `@trace_node`/`@trace_tool`로 자동 기록(Provenance)하고,
그 기록을 **조회·검증**한다. 해시 체인으로 위변조를 검증한다. LLM 호출 없음.

---

## 2. 담당 테이블

### `audit_trail` — 단계별 실행 기록 (해시 체인)

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `audit_id` | UUID PK | 기록 고유 ID |
| `batch_id` | UUID FK → batches | 소속 배치 |
| `step_number` | INT | 배치 내 실행 순서 |
| `timestamp` | TIMESTAMPTZ | 기록 시각 (DEFAULT now()) |
| `node_type` | VARCHAR(20) | agent / tool / human |
| `node_name` | VARCHAR(100) | 노드 이름 (예: data_gateway) |
| `model_version` | VARCHAR(50) | LLM 버전 (없으면 NULL) |
| `prompt_version` | VARCHAR(20) | 프롬프트 버전 (없으면 NULL) |
| `duration_ms` | INT | 단계 소요시간(ms) |
| `input_hash` | VARCHAR(64) | 입력값 SHA-256 |
| `output_hash` | VARCHAR(64) | 출력값 SHA-256 → 다음 row의 prev_hash |
| `prev_hash` | VARCHAR(64) | 직전 step의 output_hash. NULL이면 첫 step |
| `decision_text` | TEXT | 노드 판단 내용 |
| `citations` | JSONB | 인용 법조항 목록 |

> 해시 체인: 새 row INSERT 전 동일 batch_id의 MAX(step_number) row의 output_hash를
> 새 row의 prev_hash에 복사. 첫 row는 prev_hash = NULL.

### `gap_analysis_results` — 규제 개정 영향 분석

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `analysis_id` | UUID PK | 분석 결과 ID |
| `regulation_id` | UUID FK | 개정된 규제 |
| `previous_version_id` | UUID FK | 이전 규제 버전 |
| `affected_supplier_ids` | JSONB | 영향받는 협력사 ID 배열 |
| `newly_required_fields` | JSONB | 신규 필수 항목 |
| `gray_zone_items` | JSONB | HITL 검토 필요 회색지대 항목 |
| `reviewed_by` | UUID FK | 검토 사용자 |

---

## 3. 담당 이벤트

> dataclass는 `events/types.py`에 정의됨. **publish 호출은 W2 이후** (W1은 조회·검증 전용, 발행 없음).

| 이벤트 | 발생 시점 | 처리 |
|--------|----------|------|
| `AuditEntryCreated` | `@trace_node`/`@trace_tool` 함수 실행 시 | audit_trail row INSERT |
| `HitlRequested` | confidence_score < 0.85 또는 gray_zone 판정 | interrupt(), hitl_wait 전환 |
| `HitlReviewed` | 사람 검토 완료 | human 타입 row INSERT, 파이프라인 재개 |

---

## 4. 파일 구성

```
domains/audit/
├── models.py       # AuditTrail ORM (schema.sql과 1:1)
├── repository.py   # list_trail_by_batch() / list_full_chain()  — SELECT 전용
├── service.py      # create_audit_entry() 깡통(W2) + get_trail() / verify_chain()
├── router.py       # GET /audit/trail/{batch_id}, /verify
└── README.md
```

---

## 5. API

### `GET /audit/trail/{batch_id}`
audit_trail을 **step_number 순**으로 반환. `node_type`·기간(`start`/`end`) 필터 선택.

```bash
curl -X GET "http://localhost:8000/audit/trail/{batch_id}"
curl -X GET "http://localhost:8000/audit/trail/{batch_id}?node_type=agent&start=2026-05-14T00:00:00Z&end=2026-05-14T23:59:59Z"
```
응답: `AuditTrailRow` 배열. 데이터 없으면 `200 []`.

### `GET /audit/trail/{batch_id}/verify`
해시 체인 무결성 검증.
- `chain_valid` — 해시 무결성만 (첫 step prev_hash=NULL, 이후 prev_hash==직전 output_hash)
- `breaks` — 체인 끊긴 지점 (강한 신호=위변조)
- `warnings` — step_number gap·중복 (약한 신호, chain_valid엔 영향 없음)

```bash
curl -X GET "http://localhost:8000/audit/trail/{batch_id}/verify"
```
```json
{ "batch_id": "...", "total_steps": 7, "chain_valid": true, "breaks": [], "warnings": [] }
```

> 조회·검증 API → 이벤트 발행 없음 → 테이블 변경 없음 (audit_trail read-only).
> row가 생기는 건 노드·툴의 `@trace_node`/`@trace_tool` 자동 INSERT 시점.
> `create_audit_entry`(human 기록용, W2)만 쓰기 함수 → audit_trail INSERT (현재 깡통).

---

## 6. 주차별 범위

| 주차 | 할 일 | 상태 |
|------|-------|------|
| W1 | README + models.py | ✅ |
| W1 | repository / service / router — trail·verify·필터 | ✅ |
| W2 | `create_audit_entry()` 실제 INSERT (decision_text/citations 저장) | ⏳ |
| W2 | 이벤트 publish 연결 | ⏳ |
| W2~ | supervisor `route(state)`, LangGraph StateGraph 조립 | ⏳ |