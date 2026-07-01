# Verification Domain

> **스코프 축소 안내**: FEOC(IRA 지분율 심사) 기능은 스코프에서 제외되어 제거되었다.
> 이 도메인은 현재 **문서 무결성 검증**과 **컴플라이언스 이력 조회 DTO**만 담당한다.
> (배치 파이프라인의 `stage_verification` 단계도 함께 폐지 — LangGraph 노드로 결선된 적 없음.)

## 책임
- 문서 무결성 검증(`verify_document_integrity_rule`): 협력사 확정값(confirmed_fields)과 업로드 증빙 추출값을 대조해 불일치 시 `compliance_reject` + `needs_human_review` 처리 (수치 허용오차 ±5%).
- 컴플라이언스 이력 조회 DTO(`get_compliance_history_dto`): HITL 등 타 도메인이 배치별 판정 이력을 조회할 때 사용하는 읽기 전용 헬퍼.

## 관련 테이블 (schema.sql 참조)
- compliance_results (조회/기록)

## 비고
- 별도 발행 이벤트 없음(구 Verification* 이벤트는 제거됨).
- `router.py`는 현재 엔드포인트 없이 prefix만 등록된 상태.
