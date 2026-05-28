# Product Domain — 설계 명세서

> **담당**: 팀원 C (Product Domain)
> **최종 수정**: 2026-05-28 (W2 결정 #1, #2 반영)
> **참조**: `PROJECT_CORE.md` 4-2절 / `schema.sql` 영역 7 / `DECISION_LOG.md` 결정 #1, #2

---

## 1. 도메인 책임 범위

Product Domain은 배터리 제품의 **외부 원천 동기화·BOM 버전 관리·5계층 부품 트리**를 담당한다.

모든 공급망 관계(`supply_chain_map`)와 DPP 발행(`dpp_records`)은 이 도메인에서 관리하는 `product_id` / `bom_version_id`를 기준으로 연결된다. **Product Domain은 시스템 전체 데이터 흐름의 출발점**이다.

> **[결정 #1]** 이 시스템은 제품을 직접 생성하지 않는다. 원청사의 ERP/MES/PLM이 원천이며, 이 시스템은 동기화된 복사본을 보유한다(read-mostly). `POST /products`는 등록 폼이 아니라 동기화 트리거다.

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
- **`hs_code`는 6자리 이상 필수.** FTA 세번변경기준(CTC) 판정의 전제 조건.
- `unit_price`는 RVC(Regional Value Content) 부가가치기준 FTA 판정 계산에 사용된다.

---

## 3. 테이블 명세

### 3-1. `products`

```sql
product_id      UUID PK
product_code    VARCHAR(50) UNIQUE NOT NULL
product_name    VARCHAR(255)
manufacturer_id UUID FK → suppliers(supplier_id)
type            VARCHAR(50)
specs           JSONB
created_at      TIMESTAMPTZ    -- 이 시스템에 처음 동기화된 시각 (원천 생성 시각 아님)
updated_at      TIMESTAMPTZ
-- [결정 #1] 아래 3개 컬럼 추가 예정 (B schema migration 대기)
source_system   VARCHAR(50)    -- 'SEED' / 'ERP' / 'MES' / 'PLM'
external_id     VARCHAR(100)   -- 원천 시스템 PK
synced_at       TIMESTAMPTZ    -- 마지막 동기화 시각
```

**비즈니스 규칙**
- `product_code` 중복 시 UPSERT(동기화 정보 갱신). 직접 INSERT 경로에서는 `409 Conflict`.
- `source_system` 허용값 외 입력 시 `400 Bad Request`.

### 3-2. `bom_versions`

```sql
bom_version_id UUID PK
product_id     UUID FK → products(product_id) ON DELETE CASCADE
version_number VARCHAR(20) NOT NULL
effective_from DATE
effective_to   DATE
status         VARCHAR(20) DEFAULT 'draft'   -- draft / active / deprecated
approved_by    UUID FK → users(user_id)
approved_at    TIMESTAMPTZ
created_at     TIMESTAMPTZ
-- [결정 #1] 아래 3개 컬럼 추가 예정 (B schema migration 대기)
source_system  VARCHAR(50)
external_id    VARCHAR(100)
synced_at      TIMESTAMPTZ
```

**비즈니스 규칙**
- 한 `product_id`에 `status = 'active'`인 버전은 **동시에 1개만** 존재 가능.
- `active` 전이 시 기존 `active` 버전은 자동으로 `deprecated`로 전이.
- 상태 전이는 반드시 `state_machine.py` 경유. 직접 UPDATE 금지.

### 3-3. `parts`

```sql
part_id        UUID PK
part_code      VARCHAR(50) UNIQUE NOT NULL
part_name      VARCHAR(255)
tier_level     INT                           -- 1=Pack / 2=Module / 3=Cell / 4=전구체 / 5=광물
parent_part_id UUID FK → parts(part_id)     -- 자기참조. Pack 루트는 NULL.
hs_code        VARCHAR(15)                  -- 6자리 이상 필수
material_type  VARCHAR(100)
function_purpose TEXT
unit_price     NUMERIC(15,4)
purchase_unit  VARCHAR(20)
specs          JSONB
created_at     TIMESTAMPTZ
```

**비즈니스 규칙**
- `hs_code` 6자리 미만 → `422`. W3 parts ingest 함수 생성 시점에 검증 추가 예정.
- 5계층 트리 조회는 재귀 CTE 사용. ORM 직접 재귀 순회 금지.

### 3-4. `bom_items`

```sql
bom_item_id            UUID PK
bom_version_id         UUID FK → bom_versions ON DELETE CASCADE
part_id                UUID FK → parts
required_quantity      NUMERIC(15,4)
required_quantity_unit VARCHAR(20)
percentage             NUMERIC(5,2)
direct_material_cost   NUMERIC(15,4)
origin_country         VARCHAR(2)           -- ISO 3166-1 alpha-2
```

### 3-5. `part_code_mapping`

```sql
mapping_id          UUID PK
part_id             UUID FK → parts ON DELETE CASCADE
supplier_id         UUID FK → suppliers
supplier_part_code  VARCHAR(50)
original_part_code  VARCHAR(50)
```

### 3-6. `manufacturing_process`

```sql
process_id                UUID PK
part_id                   UUID FK → parts ON DELETE CASCADE
sequence_no               INT
process_name              VARCHAR(255)
process_description       TEXT
is_outsourced             BOOLEAN DEFAULT FALSE
outsourced_to_supplier_id UUID FK → suppliers   -- is_outsourced=TRUE 시 필수
process_image_url         VARCHAR(500)
```

---

## 4. BOM 버전 상태 머신

```
draft ──────────────→ active ──────────────→ deprecated
                         │
                         └── 기존 active 버전 자동 deprecated 전이
                             (PROJECT_CORE.md 3-1 불변 규칙)
```

### 허용 전이 매트릭스

| 현재 상태 | 허용 전이 | 금지 전이 |
|---|---|---|
| `draft` | `active`, `deprecated` | — |
| `active` | `deprecated` | `draft` |
| `deprecated` | (없음, 터미널) | 모든 전이 |

### 구현 위치

```
backend/domains/product/state_machine.py
  ├── activate_bom_version(db, bom_version_id)   → @trace_node
  └── deprecate_bom_version(db, bom_version_id)  → @trace_node
```

- 허용되지 않는 전이 → `422 Unprocessable Entity`.
- `draft → active` 전이 시 동일 `product_id`의 기존 `active` 버전을 먼저 `deprecated`로 전이.
- 커밋은 `service.py`에서 담당. `state_machine.py`는 `flush()`까지만.

---

## 5. 5계층 부품 트리 조회 — 재귀 CTE

`GET /products/{id}/bom` 응답의 핵심 쿼리. ORM 우회, `SQLAlchemy text()` 사용.
`depth < 5` 조건으로 순환 참조 방어.

```sql
WITH RECURSIVE bom_tree AS (
    SELECT p.part_id, p.part_code, ..., 0 AS depth
    FROM parts p
    JOIN bom_items bi ON bi.part_id = p.part_id
                     AND bi.bom_version_id = :bom_version_id
    WHERE p.parent_part_id IS NULL          -- 루트(Pack)부터

    UNION ALL

    SELECT p.part_id, p.part_code, ..., bt.depth + 1
    FROM parts p
    JOIN bom_items bi ON bi.part_id = p.part_id
                     AND bi.bom_version_id = :bom_version_id
    JOIN bom_tree bt  ON p.parent_part_id = bt.part_id
    WHERE bt.depth < 5                      -- 5계층 상한
)
SELECT * FROM bom_tree ORDER BY depth, tier_level, part_code;
```

### `only_confirmed` 파라미터 (결정 #2)

`GET /products/{id}/bom?only_confirmed=true` (기본값)
- `true` → `supply_chain_map.link_status = 'confirmed'` 노드만 포함 (운영 화면용)
- `false` → `pending` 포함 전체 트리 (공급망 맵 전체 뷰용)

> ⚠️ `link_status` 필터는 B의 schema migration 완료 후 `repository.py` `TODO` 주석 해제로 활성화.

---

## 6. API 엔드포인트 목록

| Method | Path | 설명 | 응답 | 주요 예외 |
|---|---|---|---|---|
| `POST` | `/products` | 외부 원천 동기화 트리거 (결정 #1) | `202` | `source_system` 허용값 외 → `400` |
| `GET` | `/products` | 제품 목록 (synced_at 내림차순) | `200` | — |
| `GET` | `/products/{id}` | 제품 단건 조회 | `200` | 없는 ID → `404` |
| `GET` | `/products/{id}/bom` | 5계층 BOM 트리 (`only_confirmed` 파라미터) | `200` | 제품 없음 → `404` / active BOM 없음 → `404` |
| `POST` | `/products/bom-versions/{id}/activate` | BOM 버전 활성화 | `200` | 없는 ID → `404` / 허용 전이 외 → `422` |
| `POST` | `/products/bom-versions/{id}/deprecate` | BOM 버전 deprecated 전이 | `200` | 없는 ID → `404` / 허용 전이 외 → `422` |

---

## 7. 타 도메인과의 연결 관계

Product Domain은 이벤트를 **발행만** 한다. 다른 도메인을 직접 import하지 않는다.

```
[발행 이벤트 — 결정 #1]
  BOMImported      → SupplyChain Domain (BOM 기반 공급망 구성 트리거)
  LotImported      → DPP Domain (Readiness 트리거) — W3에서 lot_id 채움
  ProductImported  → SupplyChain Domain (공급망 맵 초기화 트리거)
                     Compliance Domain (규제 검증 시작 트리거)

[수신 이벤트]
  없음. Product Domain은 이벤트 수신자가 아니다.
```

---

## 8. 완료 기준 (Done Criteria)

- [x] `GET /products/{id}/bom` 5계층 중첩 JSON 반환
- [x] active BOM 없음 → `404` / 제품 없음 → `404` 원인별 분기
- [x] `draft → active` 전이 시 기존 `active` 버전 자동 `deprecated`
- [x] 허용되지 않는 BOM 전이 → `422`
- [x] 모든 상태 전이에 `@trace_node` 적용
- [x] `fetch_from_source()` `@trace_tool` 적용
- [x] `only_confirmed` 파라미터 수신 (필터 활성화는 B migration 대기)
- [ ] `hs_code` 6자리 미만 `422` — W3 parts ingest 함수 생성 시 추가 예정

---

## 9. 구현 파일 구조

```
backend/domains/product/
  ├── README.md               ← 이 문서
  ├── __init__.py
  ├── models.py               ✅ W2 완료 (결정 #1 컬럼 추가)
  ├── repository.py           ✅ W2 완료 (fetch_from_source 신설, only_confirmed 추가)
  ├── state_machine.py        ✅ W2 완료 (신규 — BOM 상태 전이 전담)
  ├── service.py              ✅ W2 완료 (import_products 교체, 이벤트 3종 변경)
  └── router.py               ✅ W2 완료 (202 트리거, only_confirmed 파라미터)
```

> `crud.py`는 `repository.py`로 완전 대체됐으므로 PR 머지 전 삭제 필요.

---

## 10. 자가검증 — curl 시나리오 (W2 화·수)

### POST /products — 동기화 트리거

```bash
curl -X POST http://localhost:8000/api/v1/products \
  -H "Content-Type: application/json" \
  -d '{"source_system": "SEED"}'
```

성공 (202):
```json
{
  "synced_count": 3,
  "source_system": "SEED",
  "products": [
    {
      "product_id": "d1eebc99-...",
      "product_code": "BAT-NCM811-100Ah",
      "product_name": "NCM811 High Capacity Battery",
      "source_system": "SEED",
      "synced_at": "2026-05-28T09:00:00+00:00"
    }
  ]
}
```

흐름: `POST /products` → `BOMImported` + `LotImported` + `ProductImported` 순서 발행 → `products` 테이블 UPSERT

---

### GET /products — 목록

```bash
curl "http://localhost:8000/api/v1/products?limit=20&offset=0"
```

성공 (200):
```json
[
  {
    "product_id": "d1eebc99-...",
    "product_code": "BAT-NCM811-100Ah",
    "product_name": "NCM811 High Capacity Battery",
    "source_system": "SEED",
    "synced_at": "2026-05-28T09:00:00+00:00"
  }
]
```

흐름: `GET /products` → 이벤트 없음 → `products` SELECT (synced_at 내림차순)

---

### GET /products/{id} — 단건

```bash
curl http://localhost:8000/api/v1/products/d1eebc99-6666-4ef8-bb6d-6bb9bd380a77
```

성공 (200):
```json
{
  "product_id": "d1eebc99-...",
  "product_code": "BAT-NCM811-100Ah",
  "source_system": "SEED",
  "synced_at": "2026-05-28T09:00:00+00:00",
  "created_at": "2026-05-28T09:00:00+00:00",
  "updated_at": "2026-05-28T09:00:00+00:00"
}
```

없는 ID (404): `{"detail": "제품을 찾을 수 없습니다."}`

흐름: `GET /products/{id}` → 이벤트 없음 → `products` SELECT

---

### GET /products/{id}/bom — BOM 트리

```bash
# only_confirmed=true (기본값)
curl "http://localhost:8000/api/v1/products/d1eebc99-.../bom?only_confirmed=true"
```

성공 (200):
```json
{
  "product_id": "d1eebc99-...",
  "product_code": "BAT-NCM811-100Ah",
  "bom_version": "v1.0",
  "bom_status": "active",
  "only_confirmed": true,
  "tree": {
    "part_code": "PACK-NCM811-100Ah",
    "tier_level": 1,
    "hs_code": "850760",
    "origin_country": "KR",
    "depth": 0,
    "children": [
      {
        "part_code": "MOD-NCM811-16S",
        "tier_level": 2,
        "children": ["..."]
      }
    ]
  }
}
```

제품 없음 (404): `{"detail": "제품을 찾을 수 없습니다."}`
active BOM 없음 (404): `{"detail": "해당 제품에 active BOM 버전이 존재하지 않습니다."}`
BOM items 비어 있음 (200): `{"tree": null, "warning": "BOM 항목이 없습니다..."}`

흐름: `GET /products/{id}/bom` → 이벤트 없음 → `products` + `bom_versions` + `parts` + `bom_items` 재귀 CTE SELECT

---

### POST /bom-versions/{id}/activate — BOM 활성화

```bash
curl -X POST http://localhost:8000/api/v1/products/bom-versions/aabbcc-.../activate
```

성공 (200):
```json
{
  "bom_version_id": "aabbcc-...",
  "product_id": "d1eebc99-...",
  "version_number": "v2.0",
  "status": "active"
}
```

없는 ID (404): `{"detail": "BOM 버전을 찾을 수 없습니다: aabbcc-..."}`
잘못된 전이 (422): `{"detail": "허용되지 않는 BOM 버전 상태 전이: 'deprecated' → 'active'..."}`

흐름: `POST /bom-versions/{id}/activate` → 이벤트 없음 → 기존 active `deprecated` 전이 후 대상 `active` 전이

---

## 11. 자가검증 — 규제 시드 (W2 목)

### 적재 실행

```bash
psql -U $POSTGRES_USER -d $POSTGRES_DB -f docker/03_seed_regulations.sql
psql -U $POSTGRES_USER -d $POSTGRES_DB -f docker/04_seed_regulations_index.sql
```

### 적재 확인 쿼리

```sql
SELECT regulation_code, region, effective_from, embedding_status
FROM regulations
ORDER BY region, regulation_code;
```

예상 결과 (11 rows):
```
 regulation_code   | region | effective_from | embedding_status
-------------------+--------+----------------+------------------
 CBAM              | EU     | 2026-01-01     | pending
 CONFLICT_MINERALS | EU     | 2021-01-01     | pending
 CRMA              | EU     | 2024-05-23     | pending
 CSDDD             | EU     | 2024-07-25     | pending
 EU_BATTERY        | EU     | 2023-08-17     | pending
 EU_BATTERY_ART7   | EU     | 2024-07-18     | pending
 EU_BATTERY_ART47  | EU     | 2023-08-17     | pending
 EUDR              | EU     | 2023-06-29     | pending
 EUDR_FSC          | EU     | 2023-06-29     | pending
 IRA               | US     | 2024-01-01     | pending
 UFLPA             | US     | 2022-06-21     | pending
(11 rows)
```

흐름: 시드 적재 → 이벤트 없음 → `regulations` 테이블 11개 row INSERT

---

## 12. 블로커 (B 완료 대기)

코드는 준비됐으나 아래 항목은 B(인프라 담당) 처리 완료 후 자동 해소된다.

| 항목 | 파일 | 상태 |
|---|---|---|
| `products` 컬럼 3종 추가 | `models.py`, `repository.py` | ⏳ B schema migration 대기 |
| `bom_versions` 컬럼 3종 추가 | `models.py` | ⏳ B schema migration 대기 |
| `supply_chain_map.link_status` 추가 | `repository.py` TODO 주석 | ⏳ B schema migration 대기 |
| `events/types.py` 이름 변경 3종 | `service.py` import | ⏳ B 처리 대기 |

---

*이 문서는 `schema.sql` 영역 7과 `PROJECT_CORE.md` 3-1절·4-2절·`DECISION_LOG.md` 결정 #1·#2를 단일 진실 공급원으로 삼는다.*
*컬럼명·타입 불일치 발견 시 `schema.sql`을 기준으로 이 문서를 수정한다.*
