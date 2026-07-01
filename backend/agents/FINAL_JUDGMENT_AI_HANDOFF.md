# 종합판단 AI 노드 구현 핸드오프

> 상태: **미구현 (설계만)**. FEOC·DPP 발행 스코프 축소로 배치 파이프라인이
> `stage_queued → stage_extraction → stage_geo → stage_compliance → stage_risk`
> 5단계로 축소됐고, 현재 **risk 다음에 바로 `completed`** 로 끝난다(종합 판정 없음).
> 이 문서는 `stage_risk` 다음에 붙일 **배치 단위 종합판단 AI 노드**를 팀원이 구현하도록 정리한 것이다.

---

## 1. 목적

compliance 노드는 **규제별 낱개 verdict**만 낸다(UFLPA=pass, EU_BATTERY_ART7=warning …).
배치 전체를 아우르는 **단일 종합 판정**이 없어서, 지금은 risk 점수만 매기고 끝난다.
종합판단 노드는 그 낱개 결과 + geo + risk를 **하나의 배치 판정**으로 합성한다.

```
risk_scoring (stage_risk) → 종합판단(AI, 신규) → completed
```

**보너스**: 이 종합 판정이 대시보드 "AI 인사이트"의 **백엔드 정본**이 된다.
지금은 프론트가 `getRegulationResults()`(규제별 결과)를 긁어 집계하는데, 이 노드가 생기면
백엔드가 배치별 종합 판정을 내려주고 프론트는 그걸 그대로 표시하면 된다.

---

## 2. 입력 (이미 존재하는 데이터)

노드는 새 데이터를 만들 필요 없이 배치에 이미 쌓인 결과를 종합한다.

| 소스 | 내용 | 조회 방법 |
|---|---|---|
| `compliance_results` (batch_id) | 규제별 verdict + reasoning_text + cited_clauses + confidence | `verification/service.get_compliance_history_dto` 재사용 가능 |
| `geo_audit_results` (batch_id) | risk_detected, risk_flags (신장/EUDR 등) | 단건 SELECT |
| `supplier_risk_profiles` | 공급망 공급사 최고 위험 점수/등급 | `batches/repository.get_batch_detail`의 risk 집계 쿼리 참고 |
| BatchState | current_stage, confidence_score, error_reason(HITL 사유) | 그래프 state |

---

## 3. 출력 (제안 스키마)

AI가 JSON-only로 반환 → 신규 테이블 `batch_final_judgment`(배치당 1건, `batch_id UNIQUE`)에 저장 제안.

```json
{
  "overall_verdict": "pass | conditional | fail",   // 배치 종합 판정
  "executive_summary": "한 문단 자연어 요약(경영 보고용)",
  "key_risks": ["가장 시급한 리스크 3~5개(자연어)"],
  "recommended_action": "다음 조치 권고",
  "confidence": 0.0                                  // 0~1
}
```

스키마 추가는 SSOT 규칙대로 **`docker/01_schema.sql` 직접 수정**(alembic 없음).
로컬은 `down -v && up --build` 재생성, 운영/공유 EC2는 수동 `ALTER`.

---

## 4. 구현 단계 (체크리스트)

1. **스키마**: `docker/01_schema.sql`에 `batch_final_judgment` 테이블 추가
   (`judgment_id`, `batch_id UUID UNIQUE REFERENCES batches ON DELETE CASCADE`,
   `overall_verdict`, `executive_summary TEXT`, `key_risks JSONB`, `recommended_action TEXT`,
   `confidence NUMERIC(5,4)`, `created_at`). + 인덱스.
2. **state**: `backend/agents/state.py`
   - `current_stage` Literal에 `"stage_judgment"` 추가
   - 결과 필드 `final_judgment: Optional[dict]` 추가
   - `docker/01_schema.sql`의 `batches.chk_batch_stage` CHECK에도 `'stage_judgment'` 추가(양쪽 1:1 유지)
3. **AI 판정 함수**: `backend/agents/`에 `final_judgment.py` 신설
   - `compliance.py`의 `_call_llm_for_verdict` 패턴 재사용(Bedrock 경유, JSON-only 구조화 프롬프트).
     **LLM tool-use 안 씀** — 프롬프트에 위 입력을 요약해 넣고 §3 JSON을 강제.
   - RAG 불필요(이미 판정된 결과의 합성이라 규제 조항 재검색 안 함).
4. **노드 함수**: `final_judgment_node(state: BatchState) -> BatchState`
   - AsyncSessionLocal로 §2 입력 조회 → AI 호출 → `batch_final_judgment` UPSERT
   - `current_stage="stage_judgment"`, `final_judgment=<dict>` 반환
   - 커밋 경계 준수: 노드 내부에서 DB 변경 후 커밋, 이벤트 발행은 커밋 성공 후
5. **그래프 결선**: `backend/agents/graph.py`
   - `builder.add_node("final_judgment", traced_graph_node("final_judgment", final_judgment_node))`
   - supervisor 라우팅에 `final_judgment` 목적지 추가, `final_judgment → supervisor(→completed)` 엣지
6. **supervisor**: `backend/agents/supervisor.py`
   - `if current_stage == "stage_risk": return "final_judgment"` (현재는 `return "completed"`)
   - `if current_stage == "stage_judgment": return "completed"`
7. **조회 API**: `batches/repository.get_batch_detail` 응답에 `final_judgment` 추가(배치 상세에 노출)
8. **(선택) 대시보드 연계**: 프론트 대시보드 "AI 인사이트"를 규제별 집계 대신 이 종합 판정으로 전환.
   관련 위치: `dpp-dashboard/app/dashboard/page.tsx`의 `getRegulationResults` 집계 로직.

---

## 5. 지켜야 할 규칙 (ProjectFile/CLAUDE.md)

- **AI 경계**: 현재 AI 호출은 `data_gateway`·`compliance` 2곳뿐. 이 노드가 3번째 AI가 된다
  (결정론 노드 verification/geo/risk와 구분해 문서화할 것).
- **레이어 단방향** / **커밋은 노드(서비스 계층)에서 일원화, repository는 flush만**.
- **이벤트**: 필요 시 `events/types.py`에 계약 추가 후 커밋 성공 후 `publish()`.
- **스키마 SSOT** = `docker/01_schema.sql` 직접 수정, ORM은 최종 스키마와 1:1.

---

## 6. 참고 코드

- AI 호출 패턴: `backend/agents/compliance.py::_call_llm_for_verdict`
- 노드/그래프 패턴: `backend/agents/graph.py`, `supervisor.py`, `automation.py::run_risk_scoring`
- 배치 결과 집계 쿼리: `backend/domains/batches/repository.py::get_batch_detail`
- 컴플라이언스 이력 DTO: `backend/domains/verification/service.py::get_compliance_history_dto`
