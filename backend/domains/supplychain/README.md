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

### 동작 흐름 및 응답 예시
1. `products` 및 `bom_versions`를 기준으로 최상위 루트 노드(Pack)를 앵커로 설정.
2. `supply_chain_map`의 `parent_supplier_id` ↔ `child_supplier_id` 관계를 따라 재귀적으로 하위 협력사를 탐색.
3. 각 노드에 `supplier_factories`의 위치 좌표 및 규제 적용 정보를 조인하여 반환.

**[응답 예시: GET /supply-chain/{product_id}/tree]**
```json
{
  "product_id": "550e8400-e29b-41d4-a716-446655440000",
  "bom_version": "v1.0",
  "supply_tree": [
    {
      "map_id": "...",
      "part_id": "...",
      "supplier_name": "한양셀 제조(주)",
      "tier": 1,
      "depth": 0,
      "children": [
        {
          "map_id": "...",
          "part_id": "...",
          "supplier_name": "포스코퓨처엠",
          "tier": 2,
          "depth": 1,
          "factory_location": {
            "country": "KR",
            "coordinates": {"type": "Point", "coordinates": [129.36, 36.03]}
          },
          "children": []
        }
      ]
    }
  ]
}
```

## 5. Geo Audit 검증 항목
- **신장 위구르 자치구 근접성**: `ST_DWithin` 함수를 사용하여 경계 내부 또는 50km 이내 여부 판정.
- **국가 정합성**: 신고된 국가(`country`)와 좌표(`location`)의 실제 일치 여부 검증.
- **EUDR 산림 훼손**: 고위험 지역 좌표 대조 및 위성 데이터 분석 에이전트 연동.

## 6. 발행 이벤트 (events/types.py 정의 준수)
| 이벤트명 | 발생 시점 | 수신 도메인 |
| :--- | :--- | :--- |
| `GeoRiskDetected` | 고위험 지역 판정 또는 좌표 불일치 발견 시 | Audit, Risk |

## 7. 제약 사항
- 도메인 외부(`audit`, `supplier` 등) 모델 직접 import 금지.
- 모든 상태 변경 및 주요 쿼리 실행 시 `@trace_node`, `@trace_tool` 적용 필수.
- PostGIS 공간 함수 사용 시 반드시 `SRID 4326`(WGS84) 기준 준수.