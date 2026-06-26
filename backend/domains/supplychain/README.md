# SupplyChain Domain (영수 - Geo Audit Lead)

## 1. 개요
본 도메인은 배터리 제품의 Tier 1부터 말단 광산(Tier 5+)까지의 공급망 그래프를 관리하고, PostGIS 기반의 지리적 검증(Geo Audit)을 수행함. N차 공급망의 재귀적 탐색과 고위험 지역 판정이 핵심 책임임.

## 2. 주요 책임
- **공급망 그래프 관리**: 협력사 간 parent-child 관계 및 공급 비율(Supply Ratio) 관리.
- **N차 추적성 보장**: Recursive CTE를 활용한 상/하향식 공급망 트리 탐색.
- **Geo Audit 수행**: 공장/광산 좌표의 진위성 검증 및 고위험 지역(신장, EUDR 산림 훼손지 등) 근접도 판정.
- **대체 공급망 추천**: 특정 노드 리스크 발생 시 동일 부품의 대체 공급 경로 탐색.

## 3. 관리 테이블 및 뷰
- `supply_chain_map`: 협력사 간 공급 관계 및 부품 매핑.
- `supply_ratio`: 공장별 분할 납품 비율 관리.
- `v_supply_chain_node_status` (View): 공급망 허브 UI 렌더링을 위한 상태 통합 뷰.

## 4. 핵심 로직: N차 공급망 재귀 조회 (Recursive CTE)
`supply_chain_map`의 자기참조 구조를 활용하여 특정 `product_id`에 연결된 전체 계층을 탐색함.
- **입력**: `product_id`, `bom_version_id`
- **출력**: 계층별(depth), 차수별(tier) 협력사 및 공장 위치 정보 정보가 포함된 트리 구조 JSON.

## 5. Geo Audit 검증 항목
- **신장 위구르 자치구 근접성**: `ST_DWithin` 함수를 사용하여 경계 내부 또는 50km 이내 여부 판정.
- **국가 정합성**: 신고된 국가(`country`)와 좌표(`location`)의 실제 일치 여부 검증.
- **EUDR 산림 훼손**: `ST_Within`을 사용하여 산림 훼손 폴리곤 내부 포함 여부 판정 (시연용 CTE `ST_MakeEnvelope` 가상 바운딩 박스 연동).

#### 🛡️ 모의 Sad Path 검출 로그 (W3 화요일)
- **시나리오**: 베트남(VN)으로 신고된 위장 조립 공장이 실제로는 중국 광둥성 인근 좌표를 제출한 상황 모의.
- **결과**: `check_coordinate_authenticity` 쿼리 실행 결과, `ST_Within` 판정에서 `country_match: False` 검출 완료.
- **후속 작용**: 시스템이 즉시 `GeoRiskDetected(risk_type="country_mismatch")` 이벤트를 발행하여 감사 로그 기록 및 리스크 +30점 유발 검증 성공.

#### 🛡️ 종단 시연 검증 로그 (W4 목요일 - ③ Mercedes GLC Sad Path)
- **시나리오**: GLC 배치의 리튬 출처 광산이 신장(86.0, 41.0) 및 인도네시아 보르네오 EUDR 훼손지(114.0, 0.0)에 위치한 상황 모의.
- **결과**: `ST_DWithin`(신장) 및 `ST_Within`(EUDR) 쿼리가 총 3건의 위반(기존 시드 1건 + 신규 2건)을 정확히 포획.
- **예외 경계 마킹(Trace Warn)**: `audit_trail` 기록 중 시연용 임시 문자열(`test-glc-batch`)로 인한 UUID 파싱 경고(`DataError`)가 발생했으나, 메인 파이프라인(이벤트 발행 → risk_worker 점수 누적 → HITL `batch_hitl_wait` 전이)은 락 없이 완벽하게 종단 도달 완료.

## 6. 발행 이벤트 (events/types.py 정의 준수)
| 이벤트명 | 발생 시점 | 수신 도메인 |
| :--- | :--- | :--- |
| `GeoRiskDetected` | 고위험 지역 판정 또는 좌표 불일치 발견 시 | Audit, Risk |

### 7. 큐 적재 흐름 요약
- 국가 불일치(`country_mismatch`) 등 리스크 감지 시 `publish("GeoRiskDetected", asdict(event))`를 통해 이벤트를 즉시 발행한다.
- (W3 변경) 후속 비동기 처리는 차윤(Risk) 도메인의 `risk_worker`가 위반(violation) 항목으로 통합 처리하므로, 기존의 `geo_risk_worker`는 삭제됨.

## 8. 제약 사항
- 도메인 외부(`audit`, `supplier` 등) 모델 직접 import 금지.
- 모든 상태 변경 및 주요 쿼리 실행 시 `@trace_node`, `@trace_tool` 적용 필수.
- PostGIS 공간 함수 사용 시 반드시 `SRID 4326`(WGS84) 기준 준수.

## 9. W3 구현 진행 현황 (Geo Audit 노드 그래프 결합)
- [x] Day 1: 버그 2개 수정 (`geo_analysis` → `stage_geo` / `check_coordinate_authenticity` 깡통 호출 우회 해결)
- [x] Day 2: 좌표-국가 불일치 검사 PostGIS 동작 확인 완료
- [x] Day 3: `geo_audit` 노드 LangGraph 파이프라인 결합 및 `GeoRiskDetected` 발행 통합 적용 완료
- [x] Day 4: 튜터형 학습 완료 (PostGIS 공간 쿼리, 재귀 CTE, 이벤트/큐 분산 처리, 멱등성 등 5대 핵심 아키텍처 원리 정립)

## 10. W4 구현 진행 현황
- [x] [BackEnd] 4_1. N차 공급망 재귀 CTE 트리 및 PostGIS 공간 리스크 조회 API 구현
- [x] [BackEnd] 4_2. EUDR 산림 훼손 검사 안착 및 Sad 시나리오(GLC) 종단 시연 검증
- [x] 4_3. 튜터형 학습 및 PostGIS·재귀 CTE 공간 시계열 아키텍처 완전 정복
- [x] [BackEnd] 4_4. HITL 검토 지리 Context 조회 유틸리티 및 좌표 회색지대 판별 로직 구현
- [x] [BackEnd] 4_5. 회사 경계 기반 협력사 통지 및 공급원 변경 자진신고 시스템 구현
- [x] [BackEnd] 4_6. 지리 리스크 및 회사 경계 아키텍처 튜터형 학습 및 최종 정합성 검증

## 11. API 엔드포인트 (W4 조회 라우터 확충)
조회 전용 인터페이스로, 어떠한 이벤트(publish) 발행이나 강제 상태 전이도 발생시키지 않습니다.
| Method | Path | 파라미터 | 설명 | 응답 데이터 |
| :--- | :--- | :--- | :--- | :--- |
| `GET` | `/supply-chain/tree` | `product_id` 또는 `bom_version_id` | N차 공급망 재귀 CTE 트리 조회 | 평면 리스트 (hop_level, part, supplier, link_status 포함) |
| `GET` | `/supply-chain/alternatives` | `part_id` | 특정 부품의 대체 공급사 풀 조회 | 대체 협력사 목록 |
| `GET` | `/supply-chain/geo-risks` | 없음 | 지정학 공간 리스크(신장, 위장공장) 노출 목록 | xinjiang_adjacent, country_mismatch 목록 |

## 12. 정형화된 factory_gps DTO 출력 레이아웃 (HITL 연동)
HITL(Human-In-The-Loop) 검토 화면에서 지도에 핀을 꽂고 회색지대(Gray Zone)를 즉시 시각화할 수 있도록, PostGIS의 복잡한 GeoJSON 문자열을 파싱하여 정형화된 위경도 배열과 플래그를 포함하는 DTO로 서빙합니다.
- **호출 메서드**: `SupplyChainService.get_hitl_geo_context(db)`

## 13. 자진신고(Self-Declaration) 기반 재검증 이벤트 토폴로지
시스템이 단순 사후 적발(Sad Path)에 머무르는 것을 방지하기 위해, 협력사가 공급원 변경을 자진 신고하면 상위 가치사슬 전체를 다시 깨워 검증하는 비동기 이벤트 토폴로지가 설계되어 있습니다.
- **트리거 및 경계 검증**: 협력사가 공급원 변경 신고 시 `evaluate_cross_entity_boundary`를 통해 법인 횡단 여부 판별.
- **이벤트 전파 (Bus Topology)**: `requires_full_revalidation: True` 플래그를 담아 `supplier.source_change_declared` 이벤트 발행.

## 14. 공급망 맵 API (스펙 §10.2 — 신규)
프론트 맵 화면(`lib/supply-chain-mock.ts` 대체)의 실데이터 소스. 기존 `/supply-chain/*` 라우터는 유지하고 스펙 shape로 **신규 경로**를 추가함. 두 엔드포인트 모두 `Depends(get_current_user)` + `products.tenant_id` 경로 격리.

| # | Method | Path | 설명 | 응답 |
| :--- | :--- | :--- | :--- | :--- |
| 10.2a | `GET` | `/products/{productId}/supply-chain-map` | 제품 공급망 맵 조회. 필터: `bom_version_id`/`period_from`/`period_to`/`factory_id`/`po_number` | `{ supplyChainMap[], supplyChainRatios[], suppliers[], supplierFactories[] }` |
| 10.2b | `POST` | `/supply-chain/maps/{mapId}/confirm` | 맵 노드 확정. `link_status` → `supplychain_confirmed` | `{ mapId, status:"confirmed" }` |

- **10.2a 응답 계약**(snake_case → 프론트 snakeToCamel):
  - `supply_chain_map[]`: `map_id, part_id, supplier_id, factory_id, tier_level, link_status` (`map_id`는 10.2b confirm 호출용)
  - `supply_chain_ratios[]`: `part_id, supplier_id, ratio_percent`
  - `suppliers[]` = `SupplierBrief` 형태, `supplier_factories[]` = `SupplierFactory` 형태(`latitude`/`longitude` 분리)
- `linkStatus` enum(§A-4): `supplychain_declared | supplychain_confirmed`.
- **10.2b**: 요청 `{ confirmed: true }`(false면 400). 타 테넌트/미존재면 404. 응답 `status`는 link_status enum 원본이 아니라 계약 고정값 `"confirmed"`.

### 14.1 설계 결정 / 의도적 제외 (2026-06-26)
- **X-Total-Count 미적용 (의도적 제외, N/A)**: 델타 D공통체크의 "supply-chain-map의 배열들도 X-Total-Count" 문구는 비페이징 aggregate 응답에 일괄 규칙이 기계적으로 딸려 들어간 케이스로 판단해 **미적용**. 근거: (1) 맵 화면은 페이지를 나누지 않고 통째로 렌더 → 페이징 자체가 없음, (2) 헤더는 한 칸인데 응답엔 배열 4종(suppliers/factories/map/ratios)이라 "무엇의 개수"인지 모호. 추후 화면에 총개수 노출이 필요하면 헤더가 아니라 응답 본문에 `counts:{...}` 묶음으로 추가하는 방향(헤더 재사용 금지).
- **geo-risk(신장/국가불일치/EUDR) ↔ 맵 분리 유지**: 3종 적발은 별도 `GET /supply-chain/geo-risks`로 유지하고 10.2a 응답에는 미포함. 지도 위 위험표시는 프론트가 `factory_id` 기준 overlay로 결합(기능 결합도 ↓, 관심사 분리).
- **suppliers[] 내부 필드 `tenant_id` 제거**: 프론트 불필요 필드라 10.2a `suppliers[]` SELECT에서 drop. suppliers는 이미 tenant 격리된 `supply_chain_map` 노드로 한정되므로 노출 제거가 격리에 영향 없음.
