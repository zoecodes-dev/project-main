-- ============================================================
-- 03_supply_map_seed.sql
-- 공급망 맵 화면 렌더링에 필요한 누락 데이터 보완
--
-- 추가 항목:
--   A. KIRA OEM 공장 (hop0 엣지 factory_id 참조용)
--   B. supply_ratio — 전체 엣지 비율 데이터 (ERP/시드 전용)
--
-- 중복 방어: WHERE NOT EXISTS — 이미 있으면 조용히 스킵
-- 실행 전제: 01_schema.sql → 02_seed_data.sql 이후 자동 적재
-- ============================================================


-- ============================================================
-- A. KIRA OEM 공장 추가 (f0000000)
-- ============================================================
-- KIRA(a0000000)는 hop0 루트인데 공장 레코드가 없음
-- → supply_ratio.factory_id 참조 불가 → 지도 핀 미표시
-- ============================================================
INSERT INTO supplier_factories
  (factory_id, supplier_id, factory_name, factory_name_en,
   country, region, location, factory_role, destination,
   applicable_regulations, supply_ratio_percent)
SELECT
  'f0000000-0000-4000-8000-000000000000',
  'a0000000-0000-4000-8000-000000000000',
  'KIRA 수원 팩 생산공장', 'KIRA Suwon Pack Plant',
  'KR', 'Suwon',
  ST_SetSRID(ST_MakePoint(127.009, 37.264), 4326),
  'production', 'BOTH',
  '["EU_BATTERY","EU_BATTERY_ART7","CSDDD"]'::jsonb,
  100.00
WHERE NOT EXISTS (
  SELECT 1 FROM supplier_factories
  WHERE factory_id = 'f0000000-0000-4000-8000-000000000000'
);


-- ============================================================
-- B. supply_ratio — 전체 엣지 비율 데이터
-- ============================================================
-- supply_ratio 가 없으면:
--   · 공급비율 배지 → 빈값
--   · validation(합계 100% 검증) → 아무것도 안 뜸
--   · supplier_factories API 응답 → 비어서 지도 핀 없음
--
-- 02_seed_data.sql 에 이미 있는 것:
--   iX3 hop1 (51111111-..002, f1111111) 1건
--   → WHERE NOT EXISTS 로 자동 스킵됨
--
-- Gray 시나리오 의도 유지:
--   i4 hop4 (52222222-..005, Unverified Precursor Trading)
--   → 미확인 트레이더라 공장·비율 없음이 정상 → 넣지 않음
-- ============================================================


-- ── ① BMW iX3 [Happy] ────────────────────────────────────────
-- 엣지: KIRA→한양셀(Module겸Cell)→동성CAM→한중제련→호주리튬

-- hop0: NULL→KIRA(a0), Pack, f0000000
INSERT INTO supply_ratio (edge_id, factory_id, ratio_percentage, volume, unit)
SELECT '51111111-0000-4000-8000-000000000001',
       'f0000000-0000-4000-8000-000000000000', 100.00, 500, 'ea'
WHERE NOT EXISTS (
  SELECT 1 FROM supply_ratio
  WHERE edge_id = '51111111-0000-4000-8000-000000000001'
);

-- hop1: KIRA(a0)→한양셀(a1), Module, f1111111 ← 02에 이미 존재, WHERE NOT EXISTS 로 스킵
INSERT INTO supply_ratio (edge_id, factory_id, ratio_percentage, volume, unit)
SELECT '51111111-0000-4000-8000-000000000002',
       'f1111111-0000-4000-8000-000000000001', 100.00, 10000, 'ea'
WHERE NOT EXISTS (
  SELECT 1 FROM supply_ratio
  WHERE edge_id = '51111111-0000-4000-8000-000000000002'
);

-- hop2: 한양셀(a1)→한양셀(a1) self-loop, Cell, f1111111
INSERT INTO supply_ratio (edge_id, factory_id, ratio_percentage, volume, unit)
SELECT '51111111-0000-4000-8000-000000000003',
       'f1111111-0000-4000-8000-000000000001', 100.00, 10000, 'ea'
WHERE NOT EXISTS (
  SELECT 1 FROM supply_ratio
  WHERE edge_id = '51111111-0000-4000-8000-000000000003'
);

-- hop3: 한양셀(a1)→동성CAM(a2), CAM, f2222222
INSERT INTO supply_ratio (edge_id, factory_id, ratio_percentage, volume, unit)
SELECT '51111111-0000-4000-8000-000000000004',
       'f2222222-0000-4000-8000-000000000002', 100.00, 40000, 'kg'
WHERE NOT EXISTS (
  SELECT 1 FROM supply_ratio
  WHERE edge_id = '51111111-0000-4000-8000-000000000004'
);

-- hop4: 동성CAM(a2)→한중제련(aa), REF-NI, faaaaaaa
INSERT INTO supply_ratio (edge_id, factory_id, ratio_percentage, volume, unit)
SELECT '51111111-0000-4000-8000-000000000005',
       'faaaaaaa-0000-4000-8000-00000000000a', 100.00, 24000, 'kg'
WHERE NOT EXISTS (
  SELECT 1 FROM supply_ratio
  WHERE edge_id = '51111111-0000-4000-8000-000000000005'
);

-- hop5: 한중제련(aa)→호주리튬(a3), MIN-NI, f3333333
INSERT INTO supply_ratio (edge_id, factory_id, ratio_percentage, volume, unit)
SELECT '51111111-0000-4000-8000-000000000006',
       'f3333333-0000-4000-8000-000000000003', 100.00, 30000, 'kg'
WHERE NOT EXISTS (
  SELECT 1 FROM supply_ratio
  WHERE edge_id = '51111111-0000-4000-8000-000000000006'
);


-- ── ② BMW i4 [Gray] ──────────────────────────────────────────
-- 엣지: KIRA→한양셀(Module겸Cell)→동성CAM→(미확인트레이더 — 비율 없음)

-- hop0: NULL→KIRA(a0), Pack, f0000000
INSERT INTO supply_ratio (edge_id, factory_id, ratio_percentage, volume, unit)
SELECT '52222222-0000-4000-8000-000000000001',
       'f0000000-0000-4000-8000-000000000000', 100.00, 400, 'ea'
WHERE NOT EXISTS (
  SELECT 1 FROM supply_ratio
  WHERE edge_id = '52222222-0000-4000-8000-000000000001'
);

-- hop1: KIRA(a0)→한양셀(a1), Module, f1111111
INSERT INTO supply_ratio (edge_id, factory_id, ratio_percentage, volume, unit)
SELECT '52222222-0000-4000-8000-000000000002',
       'f1111111-0000-4000-8000-000000000001', 100.00, 9000, 'ea'
WHERE NOT EXISTS (
  SELECT 1 FROM supply_ratio
  WHERE edge_id = '52222222-0000-4000-8000-000000000002'
);

-- hop2: 한양셀(a1)→한양셀(a1) self-loop, Cell, f1111111
INSERT INTO supply_ratio (edge_id, factory_id, ratio_percentage, volume, unit)
SELECT '52222222-0000-4000-8000-000000000003',
       'f1111111-0000-4000-8000-000000000001', 100.00, 9000, 'ea'
WHERE NOT EXISTS (
  SELECT 1 FROM supply_ratio
  WHERE edge_id = '52222222-0000-4000-8000-000000000003'
);

-- hop3: 한양셀(a1)→동성CAM(a2), CAM, f2222222
INSERT INTO supply_ratio (edge_id, factory_id, ratio_percentage, volume, unit)
SELECT '52222222-0000-4000-8000-000000000004',
       'f2222222-0000-4000-8000-000000000002', 100.00, 38000, 'kg'
WHERE NOT EXISTS (
  SELECT 1 FROM supply_ratio
  WHERE edge_id = '52222222-0000-4000-8000-000000000004'
);
-- hop4 (52222222-..005): Unverified Precursor Trading → 의도적 미입력 (Gray 시나리오)


-- ── ③ Mercedes GLC Lot1 2024 [Sad-정상] ─────────────────────
-- 엣지: KIRA→우진셀→청정전구체

-- hop0: NULL→KIRA(a0), Pack, f0000000
INSERT INTO supply_ratio (edge_id, factory_id, ratio_percentage, volume, unit)
SELECT '53111111-0000-4000-8000-000000000001',
       'f0000000-0000-4000-8000-000000000000', 100.00, 450, 'ea'
WHERE NOT EXISTS (
  SELECT 1 FROM supply_ratio
  WHERE edge_id = '53111111-0000-4000-8000-000000000001'
);

-- hop1: KIRA(a0)→우진셀(a8), Cell, f8888888
INSERT INTO supply_ratio (edge_id, factory_id, ratio_percentage, volume, unit)
SELECT '53111111-0000-4000-8000-000000000002',
       'f8888888-0000-4000-8000-000000000008', 100.00, 9500, 'ea'
WHERE NOT EXISTS (
  SELECT 1 FROM supply_ratio
  WHERE edge_id = '53111111-0000-4000-8000-000000000002'
);

-- hop2: 우진셀(a8)→청정전구체(a6), PRE, f6666666
INSERT INTO supply_ratio (edge_id, factory_id, ratio_percentage, volume, unit)
SELECT '53111111-0000-4000-8000-000000000003',
       'f6666666-0000-4000-8000-000000000006', 100.00, 22000, 'kg'
WHERE NOT EXISTS (
  SELECT 1 FROM supply_ratio
  WHERE edge_id = '53111111-0000-4000-8000-000000000003'
);


-- ── ④ Mercedes GLC Lot2 2025 [Sad-위반] ─────────────────────
-- 엣지: KIRA→우진셀→신장니켈제련→Global Mining(신장 광산)

-- hop0: NULL→KIRA(a0), Pack, f0000000
INSERT INTO supply_ratio (edge_id, factory_id, ratio_percentage, volume, unit)
SELECT '53222222-0000-4000-8000-000000000001',
       'f0000000-0000-4000-8000-000000000000', 100.00, 450, 'ea'
WHERE NOT EXISTS (
  SELECT 1 FROM supply_ratio
  WHERE edge_id = '53222222-0000-4000-8000-000000000001'
);

-- hop1: KIRA(a0)→우진셀(a8), Cell, f8888888
INSERT INTO supply_ratio (edge_id, factory_id, ratio_percentage, volume, unit)
SELECT '53222222-0000-4000-8000-000000000002',
       'f8888888-0000-4000-8000-000000000008', 100.00, 9500, 'ea'
WHERE NOT EXISTS (
  SELECT 1 FROM supply_ratio
  WHERE edge_id = '53222222-0000-4000-8000-000000000002'
);

-- hop2: 우진셀(a8)→신장니켈제련(acac), PRE, facacaca
INSERT INTO supply_ratio (edge_id, factory_id, ratio_percentage, volume, unit)
SELECT '53222222-0000-4000-8000-000000000003',
       'facacaca-0000-4000-8000-0000000000ac', 100.00, 22000, 'kg'
WHERE NOT EXISTS (
  SELECT 1 FROM supply_ratio
  WHERE edge_id = '53222222-0000-4000-8000-000000000003'
);

-- hop3: 신장니켈제련(acac)→Global Mining(a5), MIN-NI, f5555555
INSERT INTO supply_ratio (edge_id, factory_id, ratio_percentage, volume, unit)
SELECT '53222222-0000-4000-8000-000000000004',
       'f5555555-0000-4000-8000-000000000005', 100.00, 30000, 'kg'
WHERE NOT EXISTS (
  SELECT 1 FROM supply_ratio
  WHERE edge_id = '53222222-0000-4000-8000-000000000004'
);


-- ── ⑤ Mercedes EQS [Happy] ───────────────────────────────────
-- 엣지: KIRA→우진배터리→동성CAM→한중제련→칠레리튬

-- hop0: NULL→KIRA(a0), Pack, f0000000
INSERT INTO supply_ratio (edge_id, factory_id, ratio_percentage, volume, unit)
SELECT '54444444-0000-4000-8000-000000000001',
       'f0000000-0000-4000-8000-000000000000', 100.00, 500, 'ea'
WHERE NOT EXISTS (
  SELECT 1 FROM supply_ratio
  WHERE edge_id = '54444444-0000-4000-8000-000000000001'
);

-- hop1: KIRA(a0)→우진배터리(a7), Cell, f7777777
INSERT INTO supply_ratio (edge_id, factory_id, ratio_percentage, volume, unit)
SELECT '54444444-0000-4000-8000-000000000002',
       'f7777777-0000-4000-8000-000000000007', 100.00, 11000, 'ea'
WHERE NOT EXISTS (
  SELECT 1 FROM supply_ratio
  WHERE edge_id = '54444444-0000-4000-8000-000000000002'
);

-- hop2: 우진배터리(a7)→동성CAM(a2), CAM, f2222222
INSERT INTO supply_ratio (edge_id, factory_id, ratio_percentage, volume, unit)
SELECT '54444444-0000-4000-8000-000000000003',
       'f2222222-0000-4000-8000-000000000002', 100.00, 45000, 'kg'
WHERE NOT EXISTS (
  SELECT 1 FROM supply_ratio
  WHERE edge_id = '54444444-0000-4000-8000-000000000003'
);

-- hop3: 동성CAM(a2)→한중제련(aa), LIOH, faaaaaaa
INSERT INTO supply_ratio (edge_id, factory_id, ratio_percentage, volume, unit)
SELECT '54444444-0000-4000-8000-000000000004',
       'faaaaaaa-0000-4000-8000-00000000000a', 100.00, 25000, 'kg'
WHERE NOT EXISTS (
  SELECT 1 FROM supply_ratio
  WHERE edge_id = '54444444-0000-4000-8000-000000000004'
);

-- hop4: 한중제련(aa)→칠레리튬(a9), MIN-LI, f9999999
INSERT INTO supply_ratio (edge_id, factory_id, ratio_percentage, volume, unit)
SELECT '54444444-0000-4000-8000-000000000005',
       'f9999999-0000-4000-8000-000000000009', 100.00, 14000, 'kg'
WHERE NOT EXISTS (
  SELECT 1 FROM supply_ratio
  WHERE edge_id = '54444444-0000-4000-8000-000000000005'
);
