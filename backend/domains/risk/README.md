# Risk Domain

KIRA 플랫폼에서 협력사 및 배치(Batch) 단위의 **규제 위반 리스크 점수를 가점식으로 산출하고 에스컬레이션(HITL)** 을 판단하는 도메인입니다.

## 1. 리스크 점수 산출 로직 (Risk Scoring)
여러 도메인(Verification, Geo, Compliance)이 적재한 위반 사항(violations)을 기반으로 가점식 점수를 계산합니다.
* `compliance_violation` / `compliance_reject`: **+50점**
* `GeoRiskDetected` (지리적 위험 검출): **+30점**
* `compliance_warning` (회색지대): **+15점**

누적 점수가 **70점 이상(Critical)** 이 될 경우, 즉각적으로 시스템에 `RiskEscalated` 이벤트를 발행하여 파이프라인 중단 및 사람의 개입(HITL)을 요청합니다.

## 2. 에이전트 노드 연동 (`risk_scoring` 노드)
`agents/automation.py`의 `risk_scoring` 노드를 통해 파이프라인의 `stage_risk` 단계에서 비즈니스 로직이 호출됩니다. 
타 도메인 코드 직접 참조 없이 오직 이벤트와 상태 객체(`BatchState`)만을 사용하여 느슨하게 결합(Loose Coupling)되어 있습니다.

## 3. 감사 로그(Audit Trail) 해시 체인 결과
파이프라인 노드 실행 시 얇게 감싸진 래퍼 위에 부착된 `@trace_node(node_type="agent")` 데코레이터를 통해, DB의 `audit_trail` 테이블에 무결성 해시 체인이 정상적으로 기록됨을 E2E 스크립트로 검증 완료하였습니다.

```json
// 통합 테스트(verify_graph) 해시 체인 연결 예시
{
  "step_number": 2,
  "node_type": "agent",
  "node_name": "risk_scoring",
  "input_hash": "f47ac10b58cc4372c...",
  "output_hash": "c56b219a12ee3154b...",
  "prev_hash": "a1b2c3d4e5f607182..." // 이전 단계(verification)의 output_hash와 완벽히 일치
}
```
