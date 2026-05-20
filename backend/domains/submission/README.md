# submission Domain

## 1. Submission 핵심 상태 머신 (State Machine)

데이터 요청 및 협력사 제출 프로세스는 `data_request_log.submission_status` 컬럼의 상태 전이를 기반으로 관리됩니다. 모든 상태 변경은 임의의 SQL UPDATE 문을 금지하며, 반드시 `state_machine.py` 내 정의된 함수를 거쳐야 합니다.

### 1-1. Mermaid 상태 다이어그램

```mermaid
stateDiagram-v2
    [*] --> pending : 최초 생성 (draft)
    pending --> requested : 원청사 요청 발송 (SubmissionRequested 이벤트 발행)
    requested --> in_progress : 협력사 포털 로그인 / 작성 시작
    in_progress --> submitted : 협력사 데이터 입력 및 서명 완료 (SubmissionCompleted 이벤트 발행) 
    submitted --> review : 원청사/시스템 내부 검토 및 AI 검증 파이프라인 진입
    
    review --> approved : 검증 통과 및 최종 승인 (SubmissionApproved 이벤트 발행)
    review --> rejected : 반려 처리 (SubmissionRejected 이벤트 발행)
    rejected --> in_progress : 협력사 수정 및 재제출 대기
    
    approved --> violation : 사후 감사/분석을 통해 규제 위반 사항 발견 시
    violation --> [*]
    approved --> archived : 최종 보관 처리
    archived --> [*]