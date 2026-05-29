# Verification Domain

## 책임
- 제출된 데이터의 법적/규제적 규칙 검증 (FEOC 지분율 심사, OCR, 좌표 무결성 등)
- 결정론적 룰 엔진 평가 및 비동기 검증 파이프라인 제어

## 담당 이벤트 (events/types.py 참조)
- VerificationStartedEvent
- VerificationFailedEvent
- VerificationCompletedEvent

## 관련 테이블 (schema.sql 참조)
- compliance_results
- data_request_log (submission_status 연계)

## 현재 구현 상태 (W3)
- **FEOC 지분율 심사 룰 엔진 (`verify_feoc_rule`) 실동작 구현 완료 (Decision #4 반영)**:
  - 직접 지분 25% 이상 시 즉시 위반(violation) 처리
  - 간접/합산 지분 25% 이상 시 위반 판정 및 `gray_zone` 플래그 활성화 (HITL 사람 검토 큐 연동용)
  - 위반 시 `VERIFICATION_QUEUE` 비동기 위임 (`job_id` 기반 멱등성 보장 적용)
  - `VerificationStarted`, `VerificationFailed`, `VerificationCompleted` 도메인 이벤트 규격에 맞춘 발행 연동
  - `@trace_tool` 데코레이터를 적용하여 AI/시스템의 검증 판단 내역이 `audit_trail`에 자동 기록되도록 구성 완료
