# Product Domain — 설계 명세서

> **담당**: 팀원 C (Product Domain)
> **최종 수정**: 2026-06-09 (W4 Day2 — Ingest 확장: 고객사 UPSERT + CustomerImported 이벤트 신설)
> **참조**: `PROJECT_CORE.md` 4-2절 / `schema.sql` 영역 7 / `DECISION_LOG.md` 결정 #1, #2, W4

---

## 1. 도메인 책임 범위

Product Domain은 배터리 제품의 **외부 원천 동기화·BOM 버전 관리·5계층 부품 트리**를 담당한다.

모든 공급망 관계(`supply_chain_map`)와 DPP 발행(`dpp_records`)은 이 도메인에서 관리하는 `product_id` / `bom_version_id`를 기준으로 연결된다. **Product Domain은 시스템 전체 데이터 흐름의 출발점**이다.

> **[결정 #1]** 이 시스템은 제품을 직접 생성하지 않는다. 원청사의 ERP/MES/PLM이 원천이며, 이 시스템은 동기화된 복사본을 보유한다(read-mostly). `POST /products`는 등록 폼이 아니라 동기화 트리거다.

### 담당 테이블 (schema.sql 영역 7)

| 테이블 | 역할 요약 |
|---|---|
| `customers` | 고객사(BMW·Mercedes 등) 마스터. ERP Ingest 패턴. `products`의 상위 기준점. |
| `products` | 배터리 제품 마스터. 고객사·모델·암페어 조합별 별도 row. 모든 공급망·DPP의 기준 단위. |
| `bom_versions` | 제품별 BOM 버전 관리. 생산 기간(`production_from/to`) 기준 이력 보존. |
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

### 3-1. `customers` (W4 신설)

```sql
customer_id    UUID PK
customer_code  VARCHAR(50) UNIQUE NOT NULL
customer_name  VARCHAR(255) NOT NULL
country        VARCHAR(2)
source_system  VARCHAR(100) DEFAULT 'ERP_PLM'  -- Ingest 패턴
external_id    VARCHAR(255)                     -- 원천 시스템 PK
synced_at      TIMESTAMPTZ                      -- 마지막 동기화 시각
created_at     TIMESTAMPTZ
```

**비즈니스 규칙**
- 고객사는 ERP에서 Ingest. KIRA에서 직접 생성하지 않는다 (결정 #1 동일 패턴).
- `customer_code` 충돌 시 UPSERT. 중복 row 생성 안 됨.
- 여러 제품이 같은 `customer_id`를 공유한다 (BMW iX3·i4 모두 BMW FK 참조).

### 3-2. `products`

```sql
product_id      UUID PK
product_code    VARCHAR(50) UNIQUE NOT NULL
product_name    VARCHAR(255)
customer_id     UUID FK → customers(customer_id)   -- [W4 추가]
model_name      VARCHAR(100)                        -- [W4 추가] 예: 'iX3', 'i4', 'GLC'
amperage_ah     NUMERIC(10,2)                       -- [W4 추가] 예: 108.00, 81.00
manufacturer_id UUID FK → suppliers(supplier_id)
type            VARCHAR(50)
specs           JSONB
created_at      TIMESTAMPTZ    -- 이 시스템에 처음 동기화된 시각 (원천 생성 시각 아님)
updated_at      TIMESTAMPTZ
source_system   VARCHAR(100)   -- 'SEED' / 'ERP' / 'MES' / 'PLM'
external_id     VARCHAR(255)   -- 원천 시스템 PK
synced_at       TIMESTAMPTZ    -- 마지막 동기화 시각
```

**비즈니스 규칙**
- 같은 고객사라도 모델·암페어가 다르면 별도 product row. BOM·협력사·DPP가 달라지기 때문.
- `product_code` 중복 시 UPSERT(동기화 정보 갱신). 직접 INSERT 경로에서는 `409 Conflict`.
- `source_system` 허용값 외 입력 시 `400 Bad Request`.

### 3-3. `bom_versions`

```sql
bom_version_id UUID PK
product_id     UUID FK → products(product_id) ON DELETE CASCADE
version_number VARCHAR(20) NOT NULL
production_from DATE    -- [W4 개명] 구 effective_from. 이 BOM으로 생산 시작한 날짜.
production_to   DATE    -- [W4 개명] 구 effective_to.   NULL이면 현재도 생산 중.
status         VARCHAR(20) DEFAULT 'draft'   -- draft / active / deprecated
approved_by    UUID FK → users(user_id)
approved_at    TIMESTAMPTZ
created_at     TIMESTAMPTZ
source_system  VARCHAR(100)
external_id    VARCHAR(255)
synced_at      TIMESTAMPTZ
```

> ⚠️ `effective_from` / `effective_to` 는 `regulations` 테이블의 **규제 발효일** 컬럼 이름이에요.
> `bom_versions`의 **생산 기간** 컬럼과 혼동하지 않도록 W4에서 `production_from/to`로 개명했어요.

**비즈니스 규칙**
- 한 `product_id`에 `status = 'active'`인 버전은 **동시에 1개만** 존재 가능.
- `active` 전이 시 기존 `active` 버전은 자동으로 `deprecated`로 전이.
- 상태 전이는 반드시 `state_machine.py` 경유. 직접 UPDATE 금지.
- `as_of` 날짜로 버전 조회 시: `production_from <= as_of <= COALESCE(production_to, now())`.

### 3-4. `parts`

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

### 3-5. `bom_items`

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

### 3-6. `part_code_mapping`

```sql
mapping_id          UUID PK
part_id             UUID FK → parts ON DELETE CASCADE
supplier_id         UUID FK → suppliers
supplier_part_code  VARCHAR(50)
original_part_code  VARCHAR(50)
```

### 3-7. `manufacturing_process`

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
[발행 이벤트 — W4 Day2 기준]
  CustomerImported → SupplyChain Domain, Compliance Domain (신규 고객사 등장 신호)
                     ※ is_new=True인 신규 고객사만 발행. 기존 갱신(UPSERT 충돌)은 생략.

  BOMImported      → SupplyChain Domain (BOM 기반 공급망 구성 트리거)
  LotImported      → DPP Domain (Readiness 트리거) — W3에서 lot_id 채움
  ProductImported  → SupplyChain Domain (공급망 맵 초기화 트리거)
                     Compliance Domain (규제 검증 시작 트리거)

[발행 순서 규칙]
  CustomerImported → BOMImported → LotImported → ProductImported
  products.customer_id FK 의존 때문에 customer가 먼저 확정되어야 한다.

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
- [x] 고객사 UPSERT (`customer_code` 기준, 중복 row 없음) — W4 Day2
- [x] 제품 UPSERT 시 `customer_id` · `model_name` · `amperage_ah` 적재 — W4 Day2
- [x] `CustomerImported` 이벤트 신설 및 신규 고객사 시 발행 — W4 Day2
- [ ] `hs_code` 6자리 미만 `422` — W3 parts ingest 함수 생성 시 추가 예정

---

## 9. 구현 파일 구조

```
backend/domains/product/
  ├── README.md               ← 이 문서
  ├── __init__.py
  ├── models.py               ✅ W4 Day1 완료 (Customer 신설, Product 컬럼 3개 추가, BomVersion 개명)
  ├── repository.py           ✅ W4 Day2 완료 (고객사 UPSERT + _upsert_customer 헬퍼 + 시드 데이터 확장)
  ├── state_machine.py        ✅ W2 완료 (BOM 상태 전이 전담)
  ├── service.py              ✅ W4 Day2 완료 (CustomerImported 발행 추가, fetch_from_source 반환 형태 변경 대응)
  └── router.py               ✅ W2 완료 (202 트리거, only_confirmed 파라미터)
```

> `crud.py`는 `repository.py`로 완전 대체됐으므로 PR 머지 전 삭제 필요.

---

## 10. 자가검증 4종 — Product API (W2 화·수)

### 점검 ① 계약 위반 스캔

| 파일 | import `backend.` | `publish` 2-인자 | 상태값 언더스코어 | 컬럼명 schema 일치 |
|---|---|---|---|---|
| `models.py` | ✅ 위반 0 | 해당 없음 | ✅ | ✅ |
| `repository.py` | ✅ 위반 0 | 해당 없음 | ✅ `'active'` `'draft'` `'deprecated'` | ✅ |
| `state_machine.py` | ✅ 위반 0 | 해당 없음 | ✅ Enum 경유 | ✅ |
| `service.py` | ✅ 위반 0 | ✅ 전부 2-인자 | 해당 없음 | ✅ |
| `router.py` | ✅ 위반 0 | 해당 없음 | 해당 없음 | 해당 없음 |

**전체 위반 0**

---

### 점검 ② schema 컬럼 대조

#### `customers` 테이블 (W4 신설)

| 내 코드 컬럼 | schema.sql | 일치 |
|---|---|---|
| `customer_id UUID` | `customer_id UUID PK` | ✅ |
| `customer_code VARCHAR(50) UNIQUE NOT NULL` | `customer_code VARCHAR(50) UNIQUE NOT NULL` | ✅ |
| `customer_name VARCHAR(255) NOT NULL` | `customer_name VARCHAR(255) NOT NULL` | ✅ |
| `country VARCHAR(2)` | `country VARCHAR(2)` | ✅ |
| `source_system VARCHAR(100) DEFAULT 'ERP_PLM'` | `source_system VARCHAR(100) DEFAULT 'ERP_PLM'` | ✅ |
| `external_id VARCHAR(255)` | `external_id VARCHAR(255)` | ✅ |
| `synced_at TIMESTAMPTZ` | `synced_at TIMESTAMPTZ` | ✅ |
| `created_at TIMESTAMPTZ` | `created_at TIMESTAMPTZ DEFAULT now()` | ✅ |

#### `products` 테이블

| 내 코드 컬럼 | schema.sql | 일치 |
|---|---|---|
| `product_id UUID` | `product_id UUID PK` | ✅ |
| `product_code VARCHAR(50)` | `product_code VARCHAR(50) UNIQUE NOT NULL` | ✅ |
| `product_name VARCHAR(255)` | `product_name VARCHAR(255)` | ✅ |
| `customer_id UUID FK` | `customer_id UUID FK → customers` | ✅ |
| `model_name VARCHAR(100)` | `model_name VARCHAR(100)` | ✅ |
| `amperage_ah NUMERIC(10,2)` | `amperage_ah NUMERIC(10,2)` | ✅ |
| `manufacturer_id UUID FK` | `manufacturer_id UUID REFERENCES suppliers` | ✅ |
| `type VARCHAR(50)` | `type VARCHAR(50)` | ✅ |
| `specs JSONB` | `specs JSONB` | ✅ |
| `created_at TIMESTAMPTZ` | `created_at TIMESTAMPTZ` | ✅ |
| `updated_at TIMESTAMPTZ` | `updated_at TIMESTAMPTZ` | ✅ |
| `source_system VARCHAR(100)` | `source_system VARCHAR(100)` | ✅ |
| `external_id VARCHAR(255)` | `external_id VARCHAR(255)` | ✅ |
| `synced_at TIMESTAMPTZ` | `synced_at TIMESTAMPTZ` | ✅ |

#### `bom_versions` 테이블

| 내 코드 컬럼 | schema.sql | 일치 |
|---|---|---|
| `bom_version_id UUID` | `bom_version_id UUID PK` | ✅ |
| `product_id UUID FK` | `product_id UUID FK` | ✅ |
| `version_number VARCHAR(20)` | `version_number VARCHAR(20) NOT NULL` | ✅ |
| `production_from DATE` | `production_from DATE` | ✅ |
| `production_to DATE` | `production_to DATE` | ✅ |
| `status VARCHAR(20)` | `status VARCHAR(20) DEFAULT 'draft'` | ✅ |
| `approved_by UUID FK` | `approved_by UUID REFERENCES users` | ✅ |
| `approved_at TIMESTAMPTZ` | `approved_at TIMESTAMPTZ` | ✅ |
| `created_at TIMESTAMPTZ` | `created_at TIMESTAMPTZ` | ✅ |
| `source_system VARCHAR(100)` | `source_system VARCHAR(100)` | ✅ |
| `external_id VARCHAR(255)` | `external_id VARCHAR(255)` | ✅ |
| `synced_at TIMESTAMPTZ` | `synced_at TIMESTAMPTZ` | ✅ |

**불일치 0건.**

#### `parts` / `bom_items` (재귀 CTE 사용 컬럼)

| 내 코드 컬럼 | schema.sql | 일치 |
|---|---|---|
| `part_id UUID` | `part_id UUID PK` | ✅ |
| `part_code VARCHAR(50)` | `part_code VARCHAR(50) UNIQUE NOT NULL` | ✅ |
| `part_name VARCHAR(255)` | `part_name VARCHAR(255)` | ✅ |
| `tier_level INT` | `tier_level INT` | ✅ |
| `parent_part_id UUID` | `parent_part_id UUID REFERENCES parts` | ✅ |
| `hs_code VARCHAR(15)` | `hs_code VARCHAR(15)` | ✅ |
| `material_type VARCHAR(100)` | `material_type VARCHAR(100)` | ✅ |
| `unit_price NUMERIC(15,4)` | `unit_price NUMERIC(15,4)` | ✅ |
| `required_quantity NUMERIC(15,4)` | `required_quantity NUMERIC(15,4)` | ✅ |
| `required_quantity_unit VARCHAR(20)` | `required_quantity_unit VARCHAR(20)` | ✅ |
| `origin_country VARCHAR(2)` | `origin_country VARCHAR(2)` | ✅ |
| `direct_material_cost NUMERIC(15,4)` | `direct_material_cost NUMERIC(15,4)` | ✅ |

**불일치 0건. migration 대기 컬럼 3종은 B 완료 후 자동 해소.**

---

### 점검 ③ 동작 시나리오

#### POST /products — 동기화 트리거

```bash
curl -X POST http://localhost:8000/api/v1/products \
  -H "Content-Type: application/json" \
  -d '{"source_system": "SEED"}'
```

성공 (202):
```json
{
  "synced_customer_count": 2,
  "new_customer_count": 2,
  "synced_product_count": 4,
  "source_system": "SEED",
  "customers": [
    {
      "customer_id": "a1bbcc99-...",
      "customer_code": "BMW",
      "customer_name": "BMW AG",
      "is_new": true,
      "synced_at": "2026-06-09T09:00:00+00:00"
    }
  ],
  "products": [
    {
      "product_id": "d1eebc99-...",
      "product_code": "BMW-IX3-108",
      "product_name": "BMW iX3 배터리팩 108Ah",
      "customer_id": "a1bbcc99-...",
      "model_name": "iX3",
      "amperage_ah": 108.0,
      "source_system": "SEED",
      "synced_at": "2026-06-09T09:00:00+00:00"
    }
  ]
}
```

흐름: `POST /products` → 고객사 UPSERT → `CustomerImported`(신규만) → 제품별 `BOMImported` + `LotImported` + `ProductImported` 순서 발행

---

#### GET /products — 목록

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

#### GET /products/{id} — 단건

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

#### GET /products/{id}/bom — BOM 트리

```bash
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

#### POST /bom-versions/{id}/activate — BOM 활성화

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

### 점검 ④ 누락 점검

| 체크 항목 | 결과 |
|---|---|
| `activate_bom_version()` `@trace_node` | ✅ `state_machine.py` 적용 |
| `deprecate_bom_version()` `@trace_node` | ✅ `state_machine.py` 적용 |
| `fetch_from_source()` `@trace_tool` | ✅ `repository.py` 적용 |
| `get_bom_tree()` `@trace_tool` | ✅ `repository.py` 적용 |
| 이벤트 발행 순서 | ✅ `CustomerImported` → `BOMImported` → `LotImported` → `ProductImported` |
| `CustomerImported` 신규 고객사만 발행 | ✅ `is_new=True` 조건 분기 |
| 고객사 UPSERT 멱등성 | ✅ `ON CONFLICT(customer_code)` — 동일 ingest 2회 실행 시 중복 row 없음 |
| 제품 UPSERT 시 `customer_id` 채움 | ✅ `customer_cache` 경유 — flush 후 FK 참조 |
| active BOM 없음 404 | ✅ `service.get_bom_tree()` 처리 |
| 제품 없음 404 | ✅ `service.get_product()` / `service.get_bom_tree()` 처리 |
| 잘못된 BOM 전이 422 | ✅ `state_machine._validate_transition()` 처리 |
| BOM active 중복 방지 | ✅ `activate_bom_version()` 기존 active → deprecated 선행 전이 |
| 재귀 CTE depth 상한 | ✅ `depth < 5` 조건 포함 |
| `hs_code` 6자리 검증 | ➡️ W3 parts ingest 함수 생성 시점에 추가 예정 |

---

## 11. 자가검증 4종 — 규제 시드 (W2 목)

### 점검 ① 계약 위반 스캔

| 항목 | 결과 |
|---|---|
| `embedding_status` 허용값 (`pending` / `indexed`) | ✅ 전체 `'pending'` |
| ORM/쿼리 컬럼명 schema 일치 | ✅ 위반 0 |

**전체 위반 0**

---

### 점검 ② schema 컬럼 대조

#### `regulations` 테이블 (시드 파일 사용 컬럼)

| 내 코드 컬럼 | schema.sql | 일치 |
|---|---|---|
| `regulation_id UUID` | `regulation_id UUID PK` | ✅ |
| `name VARCHAR(255)` | `name VARCHAR(255)` | ✅ |
| `regulation_code VARCHAR(50)` | `regulation_code VARCHAR(50)` | ✅ |
| `region VARCHAR(10)` | `region VARCHAR(10)` | ✅ |
| `description TEXT` | `description TEXT` | ✅ |
| `version VARCHAR(50)` | `version VARCHAR(50)` | ✅ |
| `effective_from DATE` | `effective_from DATE` | ✅ |
| `document_s3_url TEXT` | `document_s3_url TEXT` | ✅ |
| `embedding_status VARCHAR(20)` | `embedding_status VARCHAR(20) DEFAULT 'pending'` | ✅ |
| `embedding vector(1536)` | `embedding vector(1536)` | ✅ |

**불일치 0건**

---

### 점검 ③ 동작 시나리오

#### 적재 실행

```bash
psql -U $POSTGRES_USER -d $POSTGRES_DB -f docker/03_seed_regulations.sql
psql -U $POSTGRES_USER -d $POSTGRES_DB -f docker/04_seed_regulations_index.sql
```

#### 적재 확인 쿼리

```sql
SELECT regulation_code, region, effective_from, embedding_status
FROM regulations
ORDER BY region, regulation_code;
```

예상 결과 (10 rows):
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
 IRA               | US     | 2024-01-01     | pending
 UFLPA             | US     | 2022-06-21     | pending
(10 rows)
```

> `EUDR_FSC` 삭제 이유: FSC는 강제 법률이 아닌 민간 인증서. `judge_eudr()` 내부 체크리스트 항목으로 처리.

흐름: 시드 적재 → 이벤트 없음 → `regulations` 테이블 10개 row INSERT

---

### 점검 ④ 누락 점검

| 체크 항목 | 결과 |
|---|---|
| 시드 중복 적재 방지 | ✅ `regulation_code` UNIQUE 제약 + docker/ 최초 1회 실행 |
| pgvector 인덱스 중복 생성 방지 | ✅ `IF NOT EXISTS` 옵션 적용 |
| `uuid_generate_v4()` 함수 사용 가능 여부 | ✅ schema.sql에 `CREATE EXTENSION IF NOT EXISTS "uuid-ossp"` 포함 |
| 파일 실행 순서 보장 | ✅ `03_` → `04_` 숫자 순 정렬로 적재 후 인덱스 생성 순서 보장 |
| `EUDR_FSC` row 미포함 확인 | ✅ 도메인 전문가 피드백 반영 — 10종으로 확정 |

**누락 0**

---

## 12. 블로커 및 잔여 작업

| 항목 | 파일 | 상태 |
|---|---|---|
| `activate_bom_version` import alias 버그 | `service.py` | ✅ W4 Day2 수정 완료 (`sm_` alias 패턴) |
| `repository.py` — customer·model·amperage Ingest 반영 | `repository.py` | ✅ W4 Day2 완료 |
| `events/types.py` — `CustomerImportedEvent` 신설 | `events/types.py` | ✅ W4 Day2 완료 |
| `supply_chain_map.link_status` 필터 활성화 | `repository.py` TODO 주석 | ⏳ schema migration 완료 후 주석 해제 |
| `hs_code` 6자리 미만 `422` 검증 | `service.py` 또는 `repository.py` | ⏳ W3 parts ingest 함수 생성 시 추가 예정 |

---

*이 문서는 `schema.sql` 영역 7과 `PROJECT_CORE.md` 3-1절·4-2절·`DECISION_LOG.md` 결정 #1·#2를 단일 진실 공급원으로 삼는다.*
*컬럼명·타입 불일치 발견 시 `schema.sql`을 기준으로 이 문서를 수정한다.*
