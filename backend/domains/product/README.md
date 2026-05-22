# Product Domain — 설계 명세서

> **담당**: 팀원 C (Product Domain)
> **최종 수정**: 2025-05-20
> **참조**: `PROJECT_CORE.md` 4-2절 / `schema.sql` 영역 7

---

## 1. 도메인 책임 범위

Product Domain은 배터리 제품의 등록·BOM 버전 관리·5계층 부품 트리를 담당한다.

모든 공급망 관계(`supply_chain_map`)와 DPP 발행(`dpp_records`)은 이 도메인에서 관리하는 `product_id` / `bom_version_id`를 기준으로 연결된다. 즉, **Product Domain은 시스템 전체 데이터 흐름의 출발점**이다.

### 담당 테이블 (schema.sql 영역 7)

| 테이블 | 역할 요약 |
|---|---|
| `products` | 배터리 제품 마스터. 모든 공급망·DPP의 기준 단위. |
| `bom_versions` | 제품별 BOM 버전 관리. 시점별 BOM 이력 보존. |
| `parts` | 부품 마스터. Pack→광물 5계층 자기참조 트리. |
| `bom_items` | BOM 버전 내 부품 구성 항목. 소요량·원산지·재료비. |
| `part_code_mapping` | 원청 코드 ↔ 협력사 코드 매핑. |
| `manufacturing_process` | 부품별 제조 공정도. 아웃소싱 공정 추적. |

### 담당하지 않는 것 (경계 명시)

- `supply_chain_map` — SupplyChain Domain 담당. Product Domain은 `bom_version_id`를 제공할 뿐이다.
- `batches` — Audit/Operation Domain 담당. 배치 생성 시 `product_id`·`bom_version_id`를 참조하는 쪽이다.
- `dpp_records` — DPP Domain 담당. `product_id` FK만 참조한다.

---

## 2. 5계층 부품 트리 구조

배터리 제품의 부품은 **Pack → Module → Cell → 전구체 → 광물** 5계층으로 구성된다.
`parts.parent_part_id` 자기참조 FK로 트리를 구성하며, `tier_level` 컬럼이 계층을 명시한다.

```
tier_level=1  Pack         (배터리 팩 전체. 루트 노드. parent_part_id = NULL)
    │
    ├── tier_level=2  Module      (셀 묶음 단위)
    │       │
    │       └── tier_level=3  Cell        (전기화학 반응 단위)
    │               │
    │               └── tier_level=4  전구체      (양극재 전단계 소재)
    │                       │
    │                       └── tier_level=5  광물        (리튬·코발트·니켈·망간 등)
```

### 계층별 주요 속성

| tier_level | 계층 이름 | part_code 예시 | hs_code 예시 | 비고 |
|---|---|---|---|---|
| 1 | Pack | `PACK-NCM811-100Ah` | 850760 | 루트. `parent_part_id = NULL` |
| 2 | Module | `MOD-NCM811-16S` | 850760 | Pack 하위 |
| 3 | Cell | `CELL-NCM811-A` | 850760 | Module 하위 |
| 4 | 전구체 | `PRE-NCM811` | 282739 | Cell 하위. 양극재 전단계 |
| 5 | 광물 | `MIN-LI-01` | 260600 | 말단 노드. 자식 없음 |

### 설계 불변 원칙

- `parent_part_id = NULL`인 노드는 반드시 `tier_level = 1`(Pack)이어야 한다.
- 말단 노드(`tier_level = 5`, 광물)는 자식 `parts` row가 존재해서는 안 된다.
- **`hs_code`는 6자리 이상 필수.** 미달 시 `POST /parts` API에서 `422` 반환. FTA 세번변경기준(CTC) 판정의 전제 조건이다.
- `unit_price`는 RVC(Regional Value Content) 부가가치기준 FTA 판정 계산에 사용된다. 광물 계층부터 누적 원가 산정.

---

## 3. 테이블 명세

### 3-1. `products`

```sql
product_id      UUID PK
product_code    VARCHAR(50) UNIQUE NOT NULL   -- 예: 'BAT-NCM811-100Ah'
product_name    VARCHAR(255)
manufacturer_id UUID FK → suppliers(supplier_id)
type            VARCHAR(50)                  -- 각형 / 파우치형 / 원통형
specs           JSONB                        -- {"무게":"650kg","용량":"100Ah","전압":"3.7V"}
created_at      TIMESTAMPTZ
updated_at      TIMESTAMPTZ
```

**비즈니스 규칙**
- `product_code` 중복 시 `409 Conflict` 반환.
- `manufacturer_id`는 `supplier_type = 'manufacturer'`인 협력사만 허용. (애플리케이션 레벨 검증)

### 3-2. `bom_versions`

```sql
bom_version_id UUID PK
product_id     UUID FK → products(product_id) ON DELETE CASCADE
version_number VARCHAR(20) NOT NULL           -- 예: 'v1.0', 'v2.1'
effective_from DATE
effective_to   DATE
status         VARCHAR(20) DEFAULT 'draft'   -- draft / active / deprecated
approved_by    UUID FK → users(user_id)
approved_at    TIMESTAMPTZ
created_at     TIMESTAMPTZ
```

**비즈니스 규칙**
- 한 `product_id`에 `status = 'active'`인 버전은 **동시에 1개만** 존재 가능.
- `active` 전이 시 기존 `active` 버전은 자동으로 `deprecated`로 전이.
- 상태 전이는 반드시 `state_machine.py`의 `transition_bom_status()` 함수를 통해서만. 직접 UPDATE 금지.

### 3-3. `parts`

```sql
part_id        UUID PK
part_code      VARCHAR(50) UNIQUE NOT NULL   -- 원청 기준 코드. 예: 'PACK-NCM811-100Ah'
part_name      VARCHAR(255)
tier_level     INT                           -- 1=Pack / 2=Module / 3=Cell / 4=전구체 / 5=광물
parent_part_id UUID FK → parts(part_id)     -- 자기참조. Pack 루트는 NULL.
hs_code        VARCHAR(15)                  -- 6자리 이상 필수
material_type  VARCHAR(100)
function_purpose TEXT
unit_price     NUMERIC(15,4)               -- RVC 계산용 단가
purchase_unit  VARCHAR(20)
specs          JSONB
created_at     TIMESTAMPTZ
```

**비즈니스 규칙**
- `hs_code` 6자리 미만 입력 시 `422 Unprocessable Entity` 반환. FTA 판정 전제 조건.
- `parent_part_id = NULL`은 `tier_level = 1`(Pack)만 허용.
- 5계층 트리 조회는 재귀 CTE 사용. ORM 직접 조회 금지.

**인덱스** (schema.sql 정의)
```sql
CREATE INDEX idx_parts_parent  ON parts(parent_part_id);  -- 재귀 CTE 성능
CREATE INDEX idx_parts_hs_code ON parts(hs_code);         -- FTA CTC 판정 조회
```

### 3-4. `bom_items`

```sql
bom_item_id            UUID PK
bom_version_id         UUID FK → bom_versions(bom_version_id) ON DELETE CASCADE
part_id                UUID FK → parts(part_id)
required_quantity      NUMERIC(15,4)
required_quantity_unit VARCHAR(20)
percentage             NUMERIC(5,2)
direct_material_cost   NUMERIC(15,4)   -- RVC 역내 부가가치 산정 기준
origin_country         VARCHAR(2)      -- ISO 3166-1 alpha-2. FTA 원산지 판정 입력값.
```

**비즈니스 규칙**
- `origin_country`는 `hs_code`가 존재하는 부품에만 표시. `hs_code` 미기재 부품은 `origin_country = NULL`로 반환.
- `direct_material_cost`는 Compliance Domain의 RVC 계산 시 직접 참조된다.

### 3-5. `part_code_mapping`

```sql
mapping_id          UUID PK
part_id             UUID FK → parts(part_id) ON DELETE CASCADE
supplier_id         UUID FK → suppliers(supplier_id)
supplier_part_code  VARCHAR(50)   -- 협력사 내부 코드. 예: 'POS-CAM-NCM-811-A'
original_part_code  VARCHAR(50)   -- 원청 기준 코드. 예: 'CAM-NCM811'
```

**비즈니스 규칙**
- 협력사가 자체 부품 코드를 사용하더라도 `part_id`로 동일 부품 추적 가능.
- Submission Domain이 협력사 제출 데이터를 수신할 때 이 테이블로 코드 역변환.

### 3-6. `manufacturing_process`

```sql
process_id                UUID PK
part_id                   UUID FK → parts(part_id) ON DELETE CASCADE
sequence_no               INT                -- 공정 순서
process_name              VARCHAR(255)
process_description       TEXT
is_outsourced             BOOLEAN DEFAULT FALSE
outsourced_to_supplier_id UUID FK → suppliers(supplier_id)   -- is_outsourced=TRUE 시 필수
process_image_url         VARCHAR(500)       -- DPP 발행 시 첨부
```

**비즈니스 규칙**
- `is_outsourced = TRUE`이면 `outsourced_to_supplier_id`가 반드시 존재해야 한다.
- CSDDD·LKSG 실사 시 `sequence_no` 순서로 공정 투명성 증빙 자료 제공.

---

## 4. BOM 버전 상태 머신

```
draft ──────────────→ active ──────────────→ deprecated
 │                      │                        │
 │   transition_bom_     │  transition_bom_       │
 │   status("active")    │  status("deprecated")  │
 │                       │                        │
 └── ValueError          └── 기존 active 버전     └── 되돌릴 수 없음
     (잘못된 전이 시)         자동 deprecated 전이       (빈 전이 목록)
```

### 허용 전이 매트릭스

| 현재 상태 | 허용 전이 | 금지 전이 |
|---|---|---|
| `draft` | `active` | `deprecated` |
| `active` | `deprecated` | `draft` |
| `deprecated` | (없음) | 모든 전이 |

### 구현 위치

```
backend/domains/product/state_machine.py
  └── transition_bom_status(bom_version_id, new_status, approved_by, db)
```

- 잘못된 전이 시도 시 `ValueError` 발생.
- 성공 시 `audit_trail`에 자동 기록. (`@trace_node` 데코레이터 필수)
- `draft → active` 전이 시 동일 `product_id`의 기존 `active` 버전을 `deprecated`로 전이하는 로직을 함께 처리.

---

## 5. 5계층 부품 트리 조회 — 재귀 CTE

`GET /products/{id}/bom-tree` 응답의 핵심 쿼리.
ORM 우회, `SQLAlchemy text()` 사용.

```sql
-- domains/product/service.py — PARTS_TREE_QUERY
WITH RECURSIVE part_tree AS (
    -- 앵커: 최상위 Pack (parent_part_id IS NULL, active BOM)
    SELECT
        p.part_id,
        p.part_name,
        p.part_code,
        p.tier_level,
        p.parent_part_id,
        p.hs_code,
        p.unit_price,
        bi.origin_country,
        bi.direct_material_cost,
        0 AS depth
    FROM parts p
    JOIN bom_items bi  ON bi.part_id = p.part_id
    JOIN bom_versions bv ON bv.bom_version_id = bi.bom_version_id
    WHERE bv.product_id = :product_id
      AND bv.status = 'active'
      AND p.parent_part_id IS NULL     -- Pack 루트부터 시작

    UNION ALL

    -- 재귀: 자식 부품 탐색
    SELECT
        p.part_id,
        p.part_name,
        p.part_code,
        p.tier_level,
        p.parent_part_id,
        p.hs_code,
        p.unit_price,
        bi.origin_country,
        bi.direct_material_cost,
        pt.depth + 1
    FROM parts p
    JOIN bom_items bi  ON bi.part_id = p.part_id
    JOIN bom_versions bv ON bv.bom_version_id = bi.bom_version_id
    JOIN part_tree pt  ON p.parent_part_id = pt.part_id
    WHERE bv.product_id = :product_id
)
SELECT * FROM part_tree ORDER BY depth, tier_level;
```

### 응답 JSON 구조 (중첩 트리)

```json
{
  "product_id": "...",
  "product_code": "BAT-NCM811-100Ah",
  "bom_version": "v1.0",
  "parts_tree": [
    {
      "part_id": "...",
      "part_code": "PACK-NCM811-100Ah",
      "part_name": "NCM811 배터리 팩",
      "tier_level": 1,
      "hs_code": "850760",
      "unit_price": 850000.0,
      "origin_country": "KR",
      "depth": 0,
      "children": [
        {
          "part_id": "...",
          "part_code": "MOD-NCM811-16S",
          "part_name": "NCM811 모듈",
          "tier_level": 2,
          "hs_code": "850760",
          "unit_price": 45000.0,
          "origin_country": "KR",
          "depth": 1,
          "children": [
            {
              "tier_level": 3,
              "part_name": "NCM811 셀",
              "hs_code": "850760",
              "origin_country": "KR",
              "children": [
                {
                  "tier_level": 4,
                  "part_name": "NCM811 전구체",
                  "hs_code": "282739",
                  "origin_country": "CN",
                  "children": [
                    {
                      "tier_level": 5,
                      "part_name": "수산화리튬",
                      "hs_code": "282520",
                      "origin_country": null,
                      "children": []
                    }
                  ]
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}
```

**`origin_country` 표시 규칙**: `hs_code`가 없는 부품은 `origin_country = null`로 반환. FTA 판정 대상에서 제외됨을 의미한다.

---

## 6. API 엔드포인트 목록

| Method | Path | 설명 | 주요 검증 |
|---|---|---|---|
| `POST` | `/products` | 제품 등록 | `product_code` 중복 → `409` |
| `GET` | `/products/{id}` | 제품 상세 조회 | — |
| `GET` | `/products/{id}/bom-tree` | 5계층 부품 트리 조회 | active BOM 없으면 `404` |
| `POST` | `/products/{id}/bom-versions` | BOM 버전 생성 | `version_number` 중복 → `409` |
| `PATCH` | `/products/{id}/bom-versions/{vid}/status` | BOM 상태 전이 | 허용 전이 아니면 `422` |
| `POST` | `/parts` | 부품 등록 | `hs_code` 6자리 미만 → `422` |
| `GET` | `/parts/{id}/manufacturing-process` | 제조 공정도 조회 | — |

---

## 7. 타 도메인과의 연결 관계

Product Domain은 이벤트를 **발행만** 한다. 다른 도메인을 직접 import하지 않는다.

```
[Product Domain 발행 이벤트]
  ProductCreated          → SupplyChain Domain 수신 (공급망 맵 초기화 트리거)
  BomVersionActivated     → SupplyChain Domain 수신 (BOM 기준 공급망 재매핑)
  BomVersionDeprecated    → Audit Domain 수신 (이력 기록)
  PartCreated             → (현재 수신자 없음. 확장 시 추가)

[Product Domain 수신 이벤트]
  없음. Product Domain은 이벤트 수신자가 아니다.
  원청사 Admin이 직접 API를 호출하여 제품·BOM·부품을 등록한다.
```

### 타 도메인이 Product Domain 데이터를 참조하는 방식

| 도메인 | 참조 방법 | 참조 대상 |
|---|---|---|
| SupplyChain | `bom_version_id` FK 직접 참조 (같은 DB) | `supply_chain_map.bom_version_id` |
| Compliance | `product_id` 기준 BOM 트리 CTE 조회 | RVC 계산용 `unit_price`, `origin_country` |
| DPP | `product_id` FK 직접 참조 | `dpp_records.product_id` |
| Submission | `part_id` FK 직접 참조 | `part_code_mapping`으로 협력사 코드 역변환 |

---

## 8. 완료 기준 (Done Criteria)

- [ ] `GET /products/{id}/bom-tree` 호출 시 Pack → Module → Cell → 전구체 → 광물 **5계층 중첩 JSON** 반환
- [ ] `hs_code` 미기재 부품의 `origin_country`는 응답에서 `null` 표시
- [ ] `POST /parts` 에서 `hs_code` 6자리 미만 입력 시 `422` 반환
- [ ] `PATCH bom-versions/{vid}/status` 에서 잘못된 전이 시도 시 `422` 반환
- [ ] `draft → active` 전이 시 기존 `active` 버전이 `deprecated`로 자동 전이
- [ ] 모든 상태 전이에 `@trace_node` 데코레이터 적용 → `audit_trail` 자동 기록

---

## 9. 구현 파일 구조 (예정)

```
backend/domains/product/
  ├── README.md               ← 이 문서
  ├── __init__.py
  ├── models.py               ← SQLAlchemy ORM (schema.sql 영역 7 대응)
  ├── schemas.py              ← Pydantic 요청/응답 스키마
  ├── service.py              ← 비즈니스 로직. PARTS_TREE_QUERY 포함.
  ├── state_machine.py        ← BOM 버전 상태 전이 함수
  ├── router.py               ← FastAPI APIRouter
  └── events.py               ← ProductCreated 등 도메인 이벤트 정의
```

---

*이 문서는 `schema.sql` 영역 7과 `PROJECT_CORE.md` 3-1절·4-2절을 단일 진실 공급원으로 삼는다.*
*컬럼명·타입 불일치 발견 시 `schema.sql`을 기준으로 이 문서를 수정한다.*
