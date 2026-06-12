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

## 5. 공통 인가 연동과 부인 방지 (Day 1 완료)
규제 감사(Audit) 요건에 따라, 모든 승인/반려 결정은 익명으로 처리될 수 없습니다. 
`router.py`에서 인프라 계층의 `get_current_user` 의존성(`Depends`)을 주입받아, 실제 인증된 사용자의 `user_id`를 서비스 계층으로 넘겨 `hitl_reviews.decided_by`에 영구 기록합니다. (가짜 `dummy_user_id` 완전 폐기)

## 6. 도메인 격리 준수 (Rule 6 사수)
심사 컨텍스트(`get_review_context`) 구성 시 타 도메인의 ORM 모델을 직접 Import하지 않고 아래의 DTO 조회 헬퍼 함수만을 경유하여 안전하게 데이터를 수집합니다.
- 컴플라이언스 이력: `Verification.get_compliance_history_dto`
- 공장 GPS 및 마스터: `SupplyChain.get_supplier_master_and_gps_dto`
- 증빙 파일 URL: `Submission.get_evidence_urls_dto`
  - **보안/성능 방어**: S3 객체 키를 프론트엔드가 바로 열람할 수 있도록 `asyncio.to_thread`를 활용하여 Non-blocking으로 만료 시간(Expiration)이 포함된 Presigned URL을 동적 발급합니다.
  - **작업 대기 방어**: 타 도메인(geo 등)의 API가 아직 미구현 상태이더라도 프론트엔드 화면이 뻗지 않도록 `hasattr` 기반의 방어 로직(Mock 데이터 반환)을 적용하여 병렬 개발 안정성을 확보했습니다.

## 7. 비동기 워커를 통한 Graph 재개 (Day 2 완료)
LangGraph 파이프라인을 다시 깨우는 작업은 리소스를 소모하므로 웹 요청 사이클(Service)에서 직접 실행하지 않습니다. 
상태 전이 완료 후 `hitl_queue`에 작업을 `enqueue`하며, 백그라운드 워커(`workers/hitl_worker.py`)가 이를 넘겨받아 비동기적으로 `resume_graph`를 호출하여 파이프라인을 부드럽게 재개합니다.