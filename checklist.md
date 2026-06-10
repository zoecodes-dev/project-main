# Day4 graph invoke 검증 체크리스트

- [x] `jihye260610` 브랜치와 작업트리 상태 확인.
- [x] app 컨테이너를 최신 로컬 코드로 rebuild.
- [x] `backend.agents.graph` import 확인.
- [x] 첫 `graph.ainvoke` 실패 지점 확인.
- [x] `data_gateway` 반환 형태 불일치 수정.
- [x] `data_gateway`가 다음 노드에 필요한 `supplier_id`를 전달하도록 수정.
- [x] state-only graph 노드가 audit_trail을 남기도록 wrapper 복원.
- [x] DPP issuance flush에 필요한 FK 대상 모델 등록.
- [x] HITL 승인 후 supervisor로 돌아가 다음 단계로 이어지도록 수정.
- [x] Happy Path 재실행 및 audit_trail 확인.
- [x] resume 동작 확인.
- [ ] 검증 결과 기준으로 push 및 PR 준비.
