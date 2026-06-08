-- ============================================================================
-- KIRA seed data (W4 풀세트)
--   고객사 2 (BMW, Mercedes) · 제품 4 (고객사·모델·암페어로 분리)
--   제품별 협력사망 상이 · 제품 ③(Mercedes GLC)은 생산 Lot 2개(협력사 교체)
--   시나리오: ① BMW iX3=Happy  ② BMW i4=Gray(HITL)  ③ Mercedes GLC=Sad(신장차단)  ④ Mercedes EQS=Happy
-- ============================================================================
-- ID 규칙
--   tenant : a0ee...a11
--   user   : 1111...000N
--   customer: cu00...000N
--   supplier: 제품군별 접두 (c1=한양/BMW, c3=대성/BMW i4, cu=우진/Mercedes, gm=Global Mining 신장 ...)
--   product : d1=BMW iX3, d2=BMW i4, d3=Mercedes GLC, d4=Mercedes EQS
--   bom_ver : e<prod><lot>
-- ============================================================================

-- 0. tenant / users -----------------------------------------------------------
INSERT INTO tenants (tenant_id, company_name, business_reg_no, subscription_status)
VALUES ('a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '한국배터리팩(주)', '123-45-67890', 'active');

INSERT INTO users (user_id, tenant_id, email, password_hash, name, role) VALUES
('11111111-0000-4000-8000-000000000001', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'admin@kira.demo', 'hashed_pw', 'Admin User',     'admin'),
('11111111-0000-4000-8000-000000000002', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'esg@kira.demo',   'hashed_pw', 'ESG Manager',    'owner_esg'),
('11111111-0000-4000-8000-000000000003', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'buyer@kira.demo', 'hashed_pw', 'Purchasing Lead','owner_purchasing');

-- 1. customers (OEM 고객사 — ERP 수주정보에서 Ingest) --------------------------
INSERT INTO customers (customer_id, customer_code, customer_name, country, source_system, external_id) VALUES
('ca000000-0000-4000-8000-000000000001', 'OEM-BMW', 'BMW AG',            'DE', 'ERP_PLM', 'ERP-CUST-BMW'),
('ca000000-0000-4000-8000-000000000002', 'OEM-MB',  'Mercedes-Benz AG',  'DE', 'ERP_PLM', 'ERP-CUST-MB');

-- 2. suppliers (제품군별 협력사망) --------------------------------------------
-- [BMW iX3 / Happy] 한양 계열 (정상)
INSERT INTO suppliers (supplier_id, tenant_id, company_name, company_name_en, ceo_name, supplier_type, completeness_score, status, risk_level, feoc_status) VALUES
('c1111111-0000-4000-8000-000000000001', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '한양셀 제조(주)',  'Hanyang Cell Mfg',   'Kim CEO',   'manufacturer', 92, 'supplier_verified', 'low', 'eligible'),
('c1111111-0000-4000-8000-000000000002', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '동성머티리얼(주)', 'Dongsung Material',  'Park CEO',  'manufacturer', 88, 'supplier_verified', 'low', 'eligible'),
('c1111111-0000-4000-8000-000000000003', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '청정전구체(주)',   'Cheongjeong Precursor','Choi CEO','manufacturer', 85, 'supplier_verified', 'low', 'eligible');
INSERT INTO suppliers (supplier_id, tenant_id, company_name, company_name_en, ceo_name, supplier_type, completeness_score, status, risk_level, feoc_status) VALUES
('c1111111-0000-4000-8000-000000000004', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'Australia Lithium Pty', 'Australia Lithium Pty', 'Smith CEO', 'miner', 90, 'supplier_verified', 'low', 'eligible');

-- [BMW i4 / Gray] 대성 계열 (저신뢰 — 서류 미비)
INSERT INTO suppliers (supplier_id, tenant_id, company_name, company_name_en, ceo_name, supplier_type, completeness_score, status, risk_level, feoc_status) VALUES
('c3333333-0000-4000-8000-000000000001', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '대성정밀(주)',   'Daesung Precision', 'Lee CEO', 'manufacturer', 55, 'supplier_review',      'medium', 'under_review'),
('c3333333-0000-4000-8000-000000000002', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '미확인전구체(주)', 'Unverified Precursor','Han CEO','manufacturer', 48, 'supplier_in_progress', 'medium', 'under_review');

-- [Mercedes 공통] 우진 계열
INSERT INTO suppliers (supplier_id, tenant_id, company_name, company_name_en, ceo_name, supplier_type, completeness_score, status, risk_level, feoc_status) VALUES
('c9999999-0000-4000-8000-000000000001', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '우진배터리(주)', 'Woojin Battery',   'Seo CEO', 'manufacturer', 90, 'supplier_verified', 'low', 'eligible'),
('c9999999-0000-4000-8000-000000000002', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '우진셀(주)',     'Woojin Cell',      'Yoon CEO','manufacturer', 87, 'supplier_verified', 'low', 'eligible');

-- [Mercedes EQS / Happy] 정상 광물
INSERT INTO suppliers (supplier_id, tenant_id, company_name, company_name_en, ceo_name, supplier_type, completeness_score, status, risk_level, feoc_status) VALUES
('c9999999-0000-4000-8000-000000000003', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'Chile Lithium SpA', 'Chile Lithium SpA', 'Gonzalez CEO', 'miner', 89, 'supplier_verified', 'low', 'eligible');

-- [Mercedes GLC / Sad] 신장 위반 협력사 + 위안진(중간 소재)
INSERT INTO suppliers (supplier_id, tenant_id, company_name, company_name_en, ceo_name, supplier_type, completeness_score, status, risk_level, feoc_status) VALUES
('b0000000-0000-4000-8000-000000000001', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'Global Mining Corp', 'Global Mining Corp', 'Zhang CEO', 'miner',        70, 'supplier_violation', 'critical', 'ineligible'),
('b4000000-0000-4000-8000-000000000001', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'Yuanjin Material Co','Yuanjin Material Co','Wang CEO',  'manufacturer', 75, 'supplier_review',    'high',     'under_review');

-- 3. supplier_factories (좌표 — 신장 인접 판정용) ------------------------------
INSERT INTO supplier_factories (factory_id, supplier_id, factory_name, factory_name_en, country, region, location, factory_role, destination, applicable_regulations, supply_ratio_percent) VALUES
-- 한양 (BMW iX3) — 한국
('f1111111-0000-4000-8000-000000000001', 'c1111111-0000-4000-8000-000000000001', '포항 제1공장', 'Pohang Plant 1', 'KR', 'Pohang',   ST_SetSRID(ST_MakePoint(129.343, 36.019), 4326), 'production', 'EU', '[\"EU_BATTERY\",\"EU_BATTERY_ART7\",\"CSDDD\"]'::jsonb, 100.00),
('f1111111-0000-4000-8000-000000000002', 'c1111111-0000-4000-8000-000000000002', '울산 양극재공장', 'Ulsan CAM Plant', 'KR', 'Ulsan', ST_SetSRID(ST_MakePoint(129.311, 35.538), 4326), 'production', 'EU', '[\"EU_BATTERY\",\"CSDDD\"]'::jsonb, 100.00),
('f1111111-0000-4000-8000-000000000004', 'c1111111-0000-4000-8000-000000000004', 'Pilbara Mine',  'Pilbara Mine',   'AU', 'Pilbara', ST_SetSRID(ST_MakePoint(118.600, -21.600), 4326), 'mining',     'EU', '[\"EUDR\",\"CSDDD\"]'::jsonb, 100.00),
-- 대성 (BMW i4) — 한국
('f3333333-0000-4000-8000-000000000001', 'c3333333-0000-4000-8000-000000000001', '화성 공장', 'Hwaseong Plant', 'KR', 'Hwaseong', ST_SetSRID(ST_MakePoint(126.831, 37.199), 4326), 'production', 'US', '[\"UFLPA\",\"IRA\"]'::jsonb, 100.00),
-- 우진 (Mercedes) — 한국
('f9999999-0000-4000-8000-000000000001', 'c9999999-0000-4000-8000-000000000001', '아산 배터리공장', 'Asan Battery Plant', 'KR', 'Asan', ST_SetSRID(ST_MakePoint(127.004, 36.790), 4326), 'production', 'EU', '[\"EU_BATTERY\",\"CSDDD\"]'::jsonb, 100.00),
-- Chile Lithium (Mercedes EQS) — 정상 광산
('fc999999-0000-4000-8000-000000000003', 'c9999999-0000-4000-8000-000000000003', 'Atacama Mine', 'Atacama Mine', 'CL', 'Atacama', ST_SetSRID(ST_MakePoint(-68.300, -23.500), 4326), 'mining', 'EU', '[\"EUDR\",\"CSDDD\"]'::jsonb, 100.00),
-- Global Mining (Mercedes GLC / Sad) — 신장 좌표 (ST_DWithin 50km 판정 대상)
('fa000000-0000-4000-8000-000000000001', 'b0000000-0000-4000-8000-000000000001', 'Xinjiang Mine A', 'Xinjiang Mine A', 'CN', 'Xinjiang', ST_SetSRID(ST_MakePoint(86.000, 41.000), 4326), 'mining', 'EU', '[\"UFLPA\",\"EUDR\",\"CSDDD\"]'::jsonb, 100.00),
-- Yuanjin (Mercedes GLC 중간소재) — 중국 (신장 아님)
('fb000000-0000-4000-8000-000000000001', 'b4000000-0000-4000-8000-000000000001', 'Ningbo Plant', 'Ningbo Plant', 'CN', 'Ningbo', ST_SetSRID(ST_MakePoint(121.550, 29.870), 4326), 'production', 'EU', '[\"CSDDD\",\"EUDR\"]'::jsonb, 100.00);

-- 4. products (고객사·모델·암페어로 분리) -------------------------------------
INSERT INTO products (product_id, product_code, product_name, manufacturer_id, customer_id, model_name, amperage_ah, type, source_system, external_id) VALUES
-- ① BMW iX3 (Happy) — 한양 셀, 108Ah, NCM811 원통형
('d1111111-0000-4000-8000-000000000001', 'BMW-IX3-NCM811-108', 'BMW iX3 배터리팩 (NCM811 108Ah)', 'c1111111-0000-4000-8000-000000000001', 'ca000000-0000-4000-8000-000000000001', 'iX3 50', 108.00, 'battery_pack', 'ERP_PLM', 'ERP-PROD-IX3'),
-- ② BMW i4 (Gray) — 대성 셀, 81Ah, NCM 각형
('d2222222-0000-4000-8000-000000000001', 'BMW-I4-NCM-81',      'BMW i4 배터리팩 (NCM 81Ah)',       'c3333333-0000-4000-8000-000000000001', 'ca000000-0000-4000-8000-000000000001', 'i4',     81.00, 'battery_pack', 'ERP_PLM', 'ERP-PROD-I4'),
-- ③ Mercedes GLC EV (Sad) — 우진 셀, 94Ah, NCM
('d3333333-0000-4000-8000-000000000001', 'MB-GLC-NCM-94',      'Mercedes GLC EV 배터리팩 (NCM 94Ah)', 'c9999999-0000-4000-8000-000000000001', 'ca000000-0000-4000-8000-000000000002', 'GLC EV', 94.00, 'battery_pack', 'ERP_PLM', 'ERP-PROD-GLC'),
-- ④ Mercedes EQS (Happy) — 우진 셀, 118Ah, NCM
('d4444444-0000-4000-8000-000000000001', 'MB-EQS-NCM-118',     'Mercedes EQS 배터리팩 (NCM 118Ah)',  'c9999999-0000-4000-8000-000000000001', 'ca000000-0000-4000-8000-000000000002', 'EQS',    118.00, 'battery_pack', 'ERP_PLM', 'ERP-PROD-EQS');

-- 5. parts (제품군별 부품 트리 — 5계층) ---------------------------------------
-- 공통 접두: p1=BMW iX3, p2=BMW i4, p3=Mercedes GLC, p4=Mercedes EQS
INSERT INTO parts (part_id, part_code, part_name, tier_level, parent_part_id, hs_code, material_type, unit_price, source_system, external_id) VALUES
-- ① BMW iX3 (NCM811 원통형)
('aa111111-0000-4000-8000-000000000001', 'IX3-PACK',  'iX3 Battery Pack',     1, NULL,                                     '850760', 'assembly',        1200.0, 'ERP_PLM', 'ERP-IX3-PACK'),
('aa111111-0000-4000-8000-000000000003', 'IX3-CELL',  'iX3 Cell (4695)',      3, 'aa111111-0000-4000-8000-000000000001', '850760', 'cell',             180.0, 'ERP_PLM', 'ERP-IX3-CELL'),
('aa111111-0000-4000-8000-000000000006', 'IX3-CAM',   'iX3 CAM (NCM811)',     4, 'aa111111-0000-4000-8000-000000000003', '284190', 'active_material',   95.0, 'ERP_PLM', 'ERP-IX3-CAM'),
('aa111111-0000-4000-8000-000000000004', 'IX3-PRE',   'iX3 NCM Precursor',    5, 'aa111111-0000-4000-8000-000000000006', '382490', 'precursor',         42.0, 'ERP_PLM', 'ERP-IX3-PRE'),
('aa111111-0000-4000-8000-000000000005', 'IX3-LI',    'iX3 Lithium (AU)',     5, 'aa111111-0000-4000-8000-000000000006', '282520', 'refined_metal',     85.0, 'ERP_PLM', 'ERP-IX3-LI'),
-- ② BMW i4 (NCM 각형)
('aa222222-0000-4000-8000-000000000001', 'I4-PACK',   'i4 Battery Pack',      1, NULL,                                     '850760', 'assembly',        1000.0, 'ERP_PLM', 'ERP-I4-PACK'),
('aa222222-0000-4000-8000-000000000003', 'I4-CELL',   'i4 Cell (prismatic)',  3, 'aa222222-0000-4000-8000-000000000001', '850760', 'cell',             150.0, 'ERP_PLM', 'ERP-I4-CELL'),
('aa222222-0000-4000-8000-000000000006', 'I4-CAM',    'i4 CAM (NCM)',         4, 'aa222222-0000-4000-8000-000000000003', '284190', 'active_material',   88.0, 'ERP_PLM', 'ERP-I4-CAM'),
('aa222222-0000-4000-8000-000000000004', 'I4-PRE',    'i4 NCM Precursor',     5, 'aa222222-0000-4000-8000-000000000006', '382490', 'precursor',         40.0, 'ERP_PLM', 'ERP-I4-PRE'),
-- ③ Mercedes GLC (NCM)
('aa333333-0000-4000-8000-000000000001', 'GLC-PACK',  'GLC Battery Pack',     1, NULL,                                     '850760', 'assembly',        1100.0, 'ERP_PLM', 'ERP-GLC-PACK'),
('aa333333-0000-4000-8000-000000000003', 'GLC-CELL',  'GLC Cell',             3, 'aa333333-0000-4000-8000-000000000001', '850760', 'cell',             160.0, 'ERP_PLM', 'ERP-GLC-CELL'),
('aa333333-0000-4000-8000-000000000006', 'GLC-CAM',   'GLC CAM (NCM)',        4, 'aa333333-0000-4000-8000-000000000003', '284190', 'active_material',   90.0, 'ERP_PLM', 'ERP-GLC-CAM'),
('aa333333-0000-4000-8000-000000000004', 'GLC-PRE',   'GLC NCM Precursor',    5, 'aa333333-0000-4000-8000-000000000006', '382490', 'precursor',         41.0, 'ERP_PLM', 'ERP-GLC-PRE'),
('aa333333-0000-4000-8000-000000000005', 'GLC-LI',    'GLC Lithium',          5, 'aa333333-0000-4000-8000-000000000006', '282520', 'refined_metal',     84.0, 'ERP_PLM', 'ERP-GLC-LI'),
-- ④ Mercedes EQS (NCM)
('aa444444-0000-4000-8000-000000000001', 'EQS-PACK',  'EQS Battery Pack',     1, NULL,                                     '850760', 'assembly',        1300.0, 'ERP_PLM', 'ERP-EQS-PACK'),
('aa444444-0000-4000-8000-000000000003', 'EQS-CELL',  'EQS Cell',             3, 'aa444444-0000-4000-8000-000000000001', '850760', 'cell',             190.0, 'ERP_PLM', 'ERP-EQS-CELL'),
('aa444444-0000-4000-8000-000000000006', 'EQS-CAM',   'EQS CAM (NCM)',        4, 'aa444444-0000-4000-8000-000000000003', '284190', 'active_material',   96.0, 'ERP_PLM', 'ERP-EQS-CAM'),
('aa444444-0000-4000-8000-000000000005', 'EQS-LI',    'EQS Lithium (CL)',     5, 'aa444444-0000-4000-8000-000000000006', '282520', 'refined_metal',     86.0, 'ERP_PLM', 'ERP-EQS-LI');

-- 6. bom_versions (생산 Lot — ③만 2개로 협력사 교체 시연) ----------------------
INSERT INTO bom_versions (bom_version_id, product_id, version_number, production_from, production_to, status, approved_by, approved_at, source_system, external_id) VALUES
-- ① BMW iX3
('e1110000-0000-4000-8000-000000000001', 'd1111111-0000-4000-8000-000000000001', '1', '2025-01-01', NULL,         'active', '11111111-0000-4000-8000-000000000002', now(), 'ERP_PLM', 'ERP-BOM-IX3-1'),
-- ② BMW i4
('e2220000-0000-4000-8000-000000000001', 'd2222222-0000-4000-8000-000000000001', '1', '2025-01-01', NULL,         'active', '11111111-0000-4000-8000-000000000002', now(), 'ERP_PLM', 'ERP-BOM-I4-1'),
-- ③ Mercedes GLC — Lot1(과거, 정상 전구체) deprecated / Lot2(현재, Global Mining 교체) active
('e3330000-0000-4000-8000-000000000001', 'd3333333-0000-4000-8000-000000000001', '1', '2024-07-01', '2024-12-31', 'deprecated', '11111111-0000-4000-8000-000000000002', now(), 'ERP_PLM', 'ERP-BOM-GLC-1'),
('e3330000-0000-4000-8000-000000000002', 'd3333333-0000-4000-8000-000000000001', '2', '2025-01-01', NULL,         'active',     '11111111-0000-4000-8000-000000000002', now(), 'ERP_PLM', 'ERP-BOM-GLC-2'),
-- ④ Mercedes EQS
('e4440000-0000-4000-8000-000000000001', 'd4444444-0000-4000-8000-000000000001', '1', '2025-01-01', NULL,         'active', '11111111-0000-4000-8000-000000000002', now(), 'ERP_PLM', 'ERP-BOM-EQS-1');

-- 7. bom_items (버전별 자재 소요·비율) ----------------------------------------
INSERT INTO bom_items (bom_version_id, part_id, required_quantity, required_quantity_unit, percentage, direct_material_cost, origin_country, source_system, external_id) VALUES
-- ① BMW iX3 (전부 정상 출처: KR/AU)
('e1110000-0000-4000-8000-000000000001', 'aa111111-0000-4000-8000-000000000003', 100, 'ea', 60.00, 180.0, 'KR', 'ERP_PLM', 'BI-IX3-CELL'),
('e1110000-0000-4000-8000-000000000001', 'aa111111-0000-4000-8000-000000000006', 40,  'kg', 25.00,  95.0, 'KR', 'ERP_PLM', 'BI-IX3-CAM'),
('e1110000-0000-4000-8000-000000000001', 'aa111111-0000-4000-8000-000000000005', 12,  'kg', 15.00,  85.0, 'AU', 'ERP_PLM', 'BI-IX3-LI'),
-- ② BMW i4 (대성 — 서류 미비, 출처 KR이나 신뢰도 낮음)
('e2220000-0000-4000-8000-000000000001', 'aa222222-0000-4000-8000-000000000003', 96,  'ea', 62.00, 150.0, 'KR', 'ERP_PLM', 'BI-I4-CELL'),
('e2220000-0000-4000-8000-000000000001', 'aa222222-0000-4000-8000-000000000006', 38,  'kg', 38.00,  88.0, 'KR', 'ERP_PLM', 'BI-I4-CAM'),
-- ③ GLC Lot1 (과거 — 청정 출처 CL)
('e3330000-0000-4000-8000-000000000001', 'aa333333-0000-4000-8000-000000000003', 98,  'ea', 60.00, 160.0, 'KR', 'ERP_PLM', 'BI-GLC1-CELL'),
('e3330000-0000-4000-8000-000000000001', 'aa333333-0000-4000-8000-000000000006', 39,  'kg', 25.00,  90.0, 'KR', 'ERP_PLM', 'BI-GLC1-CAM'),
('e3330000-0000-4000-8000-000000000001', 'aa333333-0000-4000-8000-000000000005', 11,  'kg', 15.00,  84.0, 'CL', 'ERP_PLM', 'BI-GLC1-LI'),
-- ③ GLC Lot2 (현재 — 리튬 출처가 신장(CN)으로 교체됨 → 위반 발생)
('e3330000-0000-4000-8000-000000000002', 'aa333333-0000-4000-8000-000000000003', 98,  'ea', 60.00, 160.0, 'KR', 'ERP_PLM', 'BI-GLC2-CELL'),
('e3330000-0000-4000-8000-000000000002', 'aa333333-0000-4000-8000-000000000006', 39,  'kg', 25.00,  90.0, 'CN', 'ERP_PLM', 'BI-GLC2-CAM'),
('e3330000-0000-4000-8000-000000000002', 'aa333333-0000-4000-8000-000000000005', 11,  'kg', 15.00,  84.0, 'CN', 'ERP_PLM', 'BI-GLC2-LI'),
-- ④ Mercedes EQS (정상 CL)
('e4440000-0000-4000-8000-000000000001', 'aa444444-0000-4000-8000-000000000003', 102, 'ea', 60.00, 190.0, 'KR', 'ERP_PLM', 'BI-EQS-CELL'),
('e4440000-0000-4000-8000-000000000001', 'aa444444-0000-4000-8000-000000000006', 42,  'kg', 25.00,  96.0, 'KR', 'ERP_PLM', 'BI-EQS-CAM'),
('e4440000-0000-4000-8000-000000000001', 'aa444444-0000-4000-8000-000000000005', 13,  'kg', 15.00,  86.0, 'CL', 'ERP_PLM', 'BI-EQS-LI');

-- 8. supply_chain_map (BOM 버전별 협력사 연결 — 제품군마다 망이 다름) ----------
INSERT INTO supply_chain_map (map_id, bom_version_id, parent_supplier_id, child_supplier_id, part_id, hop_level, link_status, source_system, verification_status) VALUES
-- ① BMW iX3 (한양 셀 → 동성 CAM → 청정 전구체 → 호주 리튬) [전부 confirmed/verified]
('5c100000-0000-4000-8000-000000000001', 'e1110000-0000-4000-8000-000000000001', NULL,                                     'c1111111-0000-4000-8000-000000000001', 'aa111111-0000-4000-8000-000000000003', 1, 'supplychain_confirmed', 'ERP', 'verified'),
('5c100000-0000-4000-8000-000000000002', 'e1110000-0000-4000-8000-000000000001', 'c1111111-0000-4000-8000-000000000001', 'c1111111-0000-4000-8000-000000000002', 'aa111111-0000-4000-8000-000000000006', 2, 'supplychain_confirmed', 'ERP', 'verified'),
('5c100000-0000-4000-8000-000000000003', 'e1110000-0000-4000-8000-000000000001', 'c1111111-0000-4000-8000-000000000002', 'c1111111-0000-4000-8000-000000000003', 'aa111111-0000-4000-8000-000000000004', 3, 'supplychain_confirmed', 'ERP', 'verified'),
('5c100000-0000-4000-8000-000000000004', 'e1110000-0000-4000-8000-000000000001', 'c1111111-0000-4000-8000-000000000003', 'c1111111-0000-4000-8000-000000000004', 'aa111111-0000-4000-8000-000000000005', 4, 'supplychain_confirmed', 'ERP', 'verified'),
-- ② BMW i4 (대성 → 미확인전구체) [declared만 — 저신뢰]
('5c200000-0000-4000-8000-000000000001', 'e2220000-0000-4000-8000-000000000001', NULL,                                     'c3333333-0000-4000-8000-000000000001', 'aa222222-0000-4000-8000-000000000003', 1, 'supplychain_confirmed', 'ERP',     'verified'),
('5c200000-0000-4000-8000-000000000002', 'e2220000-0000-4000-8000-000000000001', 'c3333333-0000-4000-8000-000000000001', 'c3333333-0000-4000-8000-000000000002', 'aa222222-0000-4000-8000-000000000004', 2, 'supplychain_declared',  'SUPPLIER','pending'),
-- ③ GLC Lot1 (우진 → 위안진 → Chile) [과거, 정상]
('5c300000-0000-4000-8000-000000000001', 'e3330000-0000-4000-8000-000000000001', NULL,                                     'c9999999-0000-4000-8000-000000000002', 'aa333333-0000-4000-8000-000000000003', 1, 'supplychain_confirmed', 'ERP', 'verified'),
('5c300000-0000-4000-8000-000000000002', 'e3330000-0000-4000-8000-000000000001', 'c9999999-0000-4000-8000-000000000002', 'b4000000-0000-4000-8000-000000000001', 'aa333333-0000-4000-8000-000000000006', 2, 'supplychain_confirmed', 'ERP', 'verified'),
('5c300000-0000-4000-8000-000000000003', 'e3330000-0000-4000-8000-000000000001', 'b4000000-0000-4000-8000-000000000001', 'c9999999-0000-4000-8000-000000000003', 'aa333333-0000-4000-8000-000000000005', 3, 'supplychain_confirmed', 'ERP', 'verified'),
-- ③ GLC Lot2 (우진 → 위안진 → Global Mining 신장) [현재, 위반]
('5c300000-0000-4000-8000-000000000011', 'e3330000-0000-4000-8000-000000000002', NULL,                                     'c9999999-0000-4000-8000-000000000002', 'aa333333-0000-4000-8000-000000000003', 1, 'supplychain_confirmed', 'ERP', 'verified'),
('5c300000-0000-4000-8000-000000000012', 'e3330000-0000-4000-8000-000000000002', 'c9999999-0000-4000-8000-000000000002', 'b4000000-0000-4000-8000-000000000001', 'aa333333-0000-4000-8000-000000000006', 2, 'supplychain_confirmed', 'ERP', 'verified'),
('5c300000-0000-4000-8000-000000000013', 'e3330000-0000-4000-8000-000000000002', 'b4000000-0000-4000-8000-000000000001', 'b0000000-0000-4000-8000-000000000001', 'aa333333-0000-4000-8000-000000000005', 3, 'supplychain_confirmed', 'ERP', 'verified'),
-- ④ Mercedes EQS (우진 → Chile) [정상]
('5c400000-0000-4000-8000-000000000001', 'e4440000-0000-4000-8000-000000000001', NULL,                                     'c9999999-0000-4000-8000-000000000002', 'aa444444-0000-4000-8000-000000000003', 1, 'supplychain_confirmed', 'ERP', 'verified'),
('5c400000-0000-4000-8000-000000000002', 'e4440000-0000-4000-8000-000000000001', 'c9999999-0000-4000-8000-000000000002', 'c9999999-0000-4000-8000-000000000003', 'aa444444-0000-4000-8000-000000000005', 2, 'supplychain_confirmed', 'ERP', 'verified');

-- 9. batches (시나리오별 1배치 — 각 제품의 active BOM 기준) ----------------------
INSERT INTO batches (batch_id, product_id, bom_version_id, tenant_id, destination, current_stage, status, confidence_score, source_system, external_id) VALUES
-- ① BMW iX3 — Happy (완주)
('ba100000-0000-4000-8000-000000000001', 'd1111111-0000-4000-8000-000000000001', 'e1110000-0000-4000-8000-000000000001', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'EU', 'stage_issuance',   'batch_completed', 0.9600, 'MES', 'MES-IX3-HAPPY'),
-- ② BMW i4 — Gray (저신뢰 → HITL 대기)
('ba200000-0000-4000-8000-000000000001', 'd2222222-0000-4000-8000-000000000001', 'e2220000-0000-4000-8000-000000000001', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'US', 'stage_compliance', 'batch_hitl_wait', 0.7200, 'MES', 'MES-I4-GRAY'),
-- ③ Mercedes GLC — Sad (신장 위반 → 차단/HITL)
('ba300000-0000-4000-8000-000000000001', 'd3333333-0000-4000-8000-000000000001', 'e3330000-0000-4000-8000-000000000002', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'EU', 'stage_risk',       'batch_hitl_wait', 0.9100, 'MES', 'MES-GLC-SAD'),
-- ④ Mercedes EQS — Happy (완주)
('ba400000-0000-4000-8000-000000000001', 'd4444444-0000-4000-8000-000000000001', 'e4440000-0000-4000-8000-000000000001', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'EU', 'stage_issuance',   'batch_completed', 0.9500, 'MES', 'MES-EQS-HAPPY');

-- 10. dpp_records (발행 성공한 Happy 2건만) ------------------------------------
INSERT INTO dpp_records (dpp_id, batch_id, product_id, issued_at, status, carbon_footprint, recycled_content, qr_code_url, payload, approved_by) VALUES
('dccc0000-0000-4000-8000-000000000001', 'ba100000-0000-4000-8000-000000000001', 'd1111111-0000-4000-8000-000000000001', now() - interval '1 day', 'dpp_issued', 12.34, '{\"Ni\":12,\"Co\":10,\"Li\":8}'::jsonb,  'https://dpp.kira.demo/qr/bmw-ix3', '{\"customer\":\"BMW AG\",\"model\":\"iX3 50\",\"amperage_ah\":108,\"readiness_breakdown\":{\"all_tier_completion\":true,\"no_violation\":true,\"trader_disclosure\":true}}'::jsonb, '11111111-0000-4000-8000-000000000002'),
('dccc0000-0000-4000-8000-000000000004', 'ba400000-0000-4000-8000-000000000001', 'd4444444-0000-4000-8000-000000000001', now() - interval '2 hour', 'dpp_issued', 13.10, '{\"Ni\":13,\"Co\":11,\"Li\":9}'::jsonb, 'https://dpp.kira.demo/qr/mb-eqs', '{\"customer\":\"Mercedes-Benz AG\",\"model\":\"EQS\",\"amperage_ah\":118,\"readiness_breakdown\":{\"all_tier_completion\":true,\"no_violation\":true,\"trader_disclosure\":true}}'::jsonb, '11111111-0000-4000-8000-000000000002');

-- 11. compliance_results (시나리오별 판정) -------------------------------------
-- ① BMW iX3 — passed
INSERT INTO compliance_results (batch_id, regulation_id, supplier_id, verdict, needs_human_review, cited_clauses, confidence_score, reasoning_text)
SELECT 'ba100000-0000-4000-8000-000000000001', regulation_id, 'c1111111-0000-4000-8000-000000000001', 'compliance_passed', FALSE, '[\"EU 2023/1542 Art.7\"]'::jsonb, 0.96, '탄소발자국 신고 정상, 전 Tier 정상'
FROM regulations WHERE regulation_code = 'EU_BATTERY_ART7';
-- ② BMW i4 — warning (저신뢰 → 사람 검토)
INSERT INTO compliance_results (batch_id, regulation_id, supplier_id, verdict, needs_human_review, cited_clauses, confidence_score, reasoning_text)
SELECT 'ba200000-0000-4000-8000-000000000001', regulation_id, 'c3333333-0000-4000-8000-000000000001', 'compliance_warning', TRUE, '[\"EU 2023/1542\"]'::jsonb, 0.72, '하위 협력사 자료 미비로 판단 보류 — 사람 검토 필요'
FROM regulations WHERE regulation_code = 'EU_BATTERY';
-- ③ Mercedes GLC — violation (신장 강제노동)
INSERT INTO compliance_results (batch_id, regulation_id, supplier_id, verdict, needs_human_review, cited_clauses, confidence_score, reasoning_text)
SELECT 'ba300000-0000-4000-8000-000000000001', regulation_id, 'b0000000-0000-4000-8000-000000000001', 'compliance_violation', FALSE, '[\"UFLPA Sec.3\"]'::jsonb, 0.93, '신장 소재 광산 — 강제노동 의혹, 발행 차단'
FROM regulations WHERE regulation_code = 'UFLPA';
-- ④ Mercedes EQS — passed
INSERT INTO compliance_results (batch_id, regulation_id, supplier_id, verdict, needs_human_review, cited_clauses, confidence_score, reasoning_text)
SELECT 'ba400000-0000-4000-8000-000000000001', regulation_id, 'c9999999-0000-4000-8000-000000000001', 'compliance_passed', FALSE, '[\"EU 2023/1542 Art.7\"]'::jsonb, 0.95, '전 Tier 정상, 칠레 리튬 정상 출처'
FROM regulations WHERE regulation_code = 'EU_BATTERY_ART7';

-- 12. hitl_reviews (Gray·Sad 배치가 검토 대기) ---------------------------------
INSERT INTO hitl_reviews (review_id, batch_id, reason, trigger_stage, status) VALUES
('11100000-0000-4000-8000-000000000001', 'ba200000-0000-4000-8000-000000000001', 'gray_zone',       'stage_compliance', 'hitl_pending'),
('11100000-0000-4000-8000-000000000002', 'ba300000-0000-4000-8000-000000000001', 'risk_escalated',  'stage_risk',       'hitl_pending');
