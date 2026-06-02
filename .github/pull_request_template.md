## 오늘 6월 2일입니까??

## 📌 작업 내용
- Gray Zone 및 저신뢰도 상황에서 파이프라인을 일시정지하는 interrupt 노드 구현
- `hitl_interrupt_node`, `supplier_reverify_node` 추가
- HITL 승인 후 기존 stage를 유지하며 `batch_processing`으로 재개하는 흐름 구현

## 🔍 변경 사항
- `backend/agents/graph.py`
  - `interrupt()` 기반 HITL 및 협력사 재확인 대기 노드 추가
  - 개발 검증용 `InMemorySaver` 체크포인터 연결
- `backend/domains/audit/repository.py`
  - `hitl_reviews` 검토 건 INSERT 흐름 추가
  - resume 시 중복 검토 건 생성을 방지하도록 기존 pending row 재사용
- `backend/domains/audit/state_machine.py`
  - `batch_hitl_wait`, `batch_processing` 상태 전이 함수 추가

## ✅ 셀프 체크리스트
- [ ] PR 대상 브랜치가 `develop`으로 되어 있나요?
- [x] 불필요한 주석이나 print문은 삭제했나요?
- [x] 로컬에서 테스트는 돌려보셨나요?

### 테스트 결과
```text
interrupt_triggered = True
batch_status = batch_hitl_wait
current_stage = stage_verification
hitl_reviews_count = 1
resumed_batch_status = batch_processing
resumed_current_stage = stage_verification
```

- Close #
