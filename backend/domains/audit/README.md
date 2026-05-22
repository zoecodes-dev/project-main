# Audit Domain — README

담당: 팀원 A (지혜) | Pipeline Coordinator

---

## 1. 담당 역할 요약

파이프라인의 모든 단계에서 **무슨 일이 일어났는지 자동으로 기록**하고,
신뢰도가 낮거나 판단이 모호한 경우 **사람 검토(HITL)로 파이프라인을 정지**하는 역할.

LLM 호출 없음. 조건 분기와 기록만.

---

## 2. 담당 테이블

### `audit_trail`
파이프라인 각 단계의 실행 결과를 row로 기록. 해시 체인으로 무결성 보장.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `audit_id` | UUID PK | 감사 기록 고유 식별자 |
| `batch_id` | UUID FK → batches | 어떤 배치의 기록인지 |
| `step_number` | INT | 배치 내 실행 순서 |
| `node_type` | VARCHAR | agent / tool / human |
| `node_name` | VARCHAR | 실행된 노드 이름 (예: data_gateway) |
| `model_version` | VARCHAR | 사용된 LLM 버전 (없으면 NULL) |
| `input_hash` | VARCHAR(64) | 입력값의 SHA-256 해시 |
| `output_hash` | VARCHAR(64) | 출력값의 SHA-256 해시 → 다음 row의 prev_hash |
| `prev_hash` | VARCHAR(64) | 직전 step의 output_hash. NULL이면 첫 번째 step |
| `decision_text` | TEXT | 해당 노드의 판단 내용 |
| `citations` | JSONB | Compliance Agent 인용 법조항 목록 |

> 해시 체인 규칙: 새 row INSERT 전 동일 batch_id의 MAX(step_number) row 조회 →
> 그 output_hash를 새 row의 prev_hash에 복사. 첫 row는 prev_hash = NULL.

### `gap_analysis_results`
규제 개정 시 영향받는 협력사와 신규 필수 항목을 분석한 결과 저장.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `analysis_id` | UUID PK | 분석 결과 고유 식별자 |
| `regulation_id` | UUID FK | 개정된 규제 ID |
| `previous_version_id` | UUID FK | 이전 규제 버전 ID |
| `affected_supplier_ids` | JSONB | 영향받는 협력사 ID 배열 |
| `newly_required_fields` | JSONB | 신규 필수 항목 목록 |
| `gray_zone_items` | JSONB | HITL 검토 필요 회색지대 항목 |
| `reviewed_by` | UUID FK | 검토한 사용자 ID |

---

## 3. 담당 이벤트

| 이벤트 | 발생 시점 | 처리 내용 |
|--------|----------|----------|
| `AuditEntryCreated` | `@trace_node` 데코레이터가 붙은 함수 실행 시 자동 발생 | audit_trail에 row INSERT |
| `HitlRequested` | confidence_score < 0.85 또는 gray_zone 판정 시 | 파이프라인 interrupt(), hitl_wait 상태로 전환 |
| `HitlReviewed` | 사람 검토 완료 시 | audit_trail에 human 타입 row INSERT, 파이프라인 재개 |

---

## 4. 핵심 함수 목록

```
domains/audit/
├── models.py         # AuditTrail ORM (Day 2)
├── service.py        # create_audit_entry() (Day 3 깡통)
├── router.py         # GET /audit/trail/{batch_id}
│                     # GET /audit/gap-analysis/{regulation_id}
└── README.md         # 이 파일

agents/
└── supervisor.py     # route(state) -> str (Day 3 깡통)
```

### `create_audit_entry()` — Day 3 구현 예정
```python
@trace_node("audit_entry_create", "agent")
async def create_audit_entry(
    db: AsyncSession,
    batch_id: UUID,
    step_number: int,
    node_type: str,       # agent / tool / human
    node_name: str,
    decision_text: str,
    citations: list[str] | None = None,
    model_version: str | None = None,
) -> AuditTrail:
    ...
```

### `route(state) -> str` — Day 3 구현 예정
```python
def route(state: BatchState) -> str:
    if state["confidence_score"] < 0.85:
        return "hitl_interrupt"
    if state["current_stage"] == "queued":
        return "data_gateway"
    if state["current_stage"] == "extraction":
        return "verification"
    if state["current_stage"] == "verification":
        return "geo_audit"
    if state["current_stage"] == "geo_analysis":
        return "compliance"
    if state["current_stage"] == "compliance":
        return "readiness"
    return "completed"
```

---

## 5. 완료 기준

- `@trace_node` 데코레이터가 붙은 함수 호출 시 `audit_trail`에 row 자동 생성
- `GET /audit/trail/{batch_id}` 응답의 `chain_valid: true` 반환
- `route(state)` 함수가 각 stage 조건에 맞는 노드 이름 반환

---

## 6. 이번 주 범위 (W1)

| Day | 할 일 |
|-----|-------|
| Day 1 (오늘) | 이 README 작성 ✅ |
| Day 2 | `domains/audit/models.py` — AuditTrail ORM 작성 |
| Day 3 | `route(state) -> str` 깡통 함수 + `create_audit_entry()` 깡통 함수 |

> LangGraph StateGraph 조립은 W2. 이번 주는 ORM + 깡통 함수만.
