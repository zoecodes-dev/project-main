# HITL (Human-in-the-Loop) Domain

KIRA 플랫폼에서 AI(LangGraph 파이프라인)가 독자적으로 판정하기 모호하여 보류(`batch_hitl_wait`)시킨 공급망 배치 데이터를 인간 심사관이 직접 관제하고 최종 승인/반려 결정을 내리는 제어 정문 도메인입니다.

## 1. 심사관 조치 상태 전이 매트릭스 (State Machine)
`hitl_reviews` 테이블의 상태는 엄격하게 제어되며 직접적인 SQL UPDATE는 금지됩니다. 반드시 `HitlStateMachine`을 거쳐야 합니다.

### 상태 (Status) 흐름
- `hitl_pending`: AI 파이프라인이 중단되고 심사 항목이 큐에 적재된 대기 상태
- `hitl_in_review`: 심사관이 데이터를 열람하고 검토 중인 상태
- `hitl_resolved`: 심사관의 최종 결단(승인/반려 등)이 완료된 확정 상태

### 심사 결단 (Resolution) 유형
- `approve` (승인): 문제가 없다고 판단하여 파이프라인 재개를 허용함
- `reject` (반려): 치명적인 위반 또는 서류 미비로 인해 연관된 제출 건(Submission) 전체를 반려함
- `escalate` (이관): 상위 권한자나 다른 부서로 검토를 이관함

## 2. 도메인 간 비동기 통신 (Event-Driven)
타 도메인과의 강결합을 피하기 위해 이벤트(`publish`)를 통한 비동기 파동만을 활용합니다.

| 이벤트명 | 발생 조건 | 수신 도메인 및 액션 |
| :--- | :--- | :--- |
| `hitl.resolved` | 심사관이 `/resolve` (또는 approve/reject) 완료 시 | **LangGraph (지혜)**: 멈춰있던 워크플로우 스레드를 `resume` 하여 다음 노드로 진입시킴 |
| `submission.reject_requested` | 심사관이 명시적으로 `reject` 처리 시 | **Submission (제출)**: 연관된 `data_request_log`의 상태를 `submission_rejected`로 하향 전이 처리함 |

## 3. 감사 로그 추적성 (Provenance)
본 도메인에서 이루어지는 인간의 결단 행위는 시스템의 규제 대응 무결성을 증명하는 핵심 근거입니다.
- `service.py`의 `resolve_batch` 호출 시 `@trace_node(node_type="human")`가 동작하여, 심사관의 행위(`user_id`, `decision_text`)가 `audit_trail` 테이블에 해시 체인으로 이중 래핑 없이 영구 보존됩니다.

## 4. AI 판정 요약 (Haiku 연동 완료)
- **적용 모델**: 경량/고속 모델인 Haiku (`global.anthropic.claude-haiku-4-5-20251001-v1:0` / `lightweight` 프로파일)
- **기능**: `/context` 호출 시 심사관의 빠른 판단을 돕기 위해 복잡한 컴플라이언스/증빙 컨텍스트를 3줄 이내로 요약 반환 (`@trace_tool` 적용).
- **주의**: 요약은 참고용이며, 최종 승인/반려 결정(`resolve`)은 사람(심사관)이 직접 수행합니다.