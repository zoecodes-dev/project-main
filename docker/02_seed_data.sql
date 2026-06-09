-- ============================================================
-- KIRA 플랫폼 통합 시드 데이터 (02_seed_data.sql)
-- ============================================================
-- [버전] 7계층 × 4제품 × 2고객사(BMW/Mercedes) × 12협력사 풀세트
--
-- [regulations 제외]
--   regulations 10종 + pgvector hnsw 인덱스는 01_schema.sql이 적재한다.
--   (regulations: schema가 단일 소스, seed는 시나리오 데이터만)
--
-- [제품 3축] customer_id(고객사) + model_name(차종) + amperage_ah(Ah)
--   bom_versions.production_from/to 로 생산 Lot 기간 추적.
--
-- [7계층 트리] 1 Pack / 2 Module / 3 Cell / 4 활물질(CAM·ANO)
--             / 5 전구체 / 6 제련·정제 / 7 광산
--
-- [4대 시나리오]
--   ① BMW iX3 (108Ah 원통 NCM811) ── Happy: 한양셀→동성CAM→호주리튬, FEOC 통과 → DPP 발행
--   ② BMW i4  (81Ah 각형)         ── Gray : 대성정밀 전구체 미확인(신뢰도 0.70) → HITL 대기
--   ③ Mercedes GLC EV (94Ah 각형) ── Sad  : Lot1(2024)=청정전구체 정상 / Lot2(2025)=Global Mining 신장 위반·외국지분 25%↑ → 차단
--   ④ Mercedes EQS (118Ah 각형)   ── Happy: 우진배터리→동성CAM→칠레리튬, 정상
--
-- 실행 전제: 01_schema.sql 이후 적재(파일명 알파벳순 자동 실행).
--           파괴적 변경 → 로컬은 docker compose down -v 선행 필수.
-- ============================================================


-- ============================================================
-- 1. 테넌트 / 사용자 / 권한 (영역 1)
-- ============================================================
INSERT INTO tenants (tenant_id, company_name, business_reg_no, subscription_status)
VALUES ('a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'KIRA Platform OEM', '123-45-67890', 'active');

-- 원청 관리자 + ESG/구매 담당자 + 협력사 사용자
INSERT INTO users (user_id, tenant_id, email, password_hash, name, role) VALUES
('11111111-0000-4000-8000-000000000001', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'admin@kira.demo',       'hashed_pw', 'Admin User',      'admin'),
('11111111-0000-4000-8000-000000000002', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'esg@kira.demo',         'hashed_pw', 'ESG Manager',     'owner_esg'),
('11111111-0000-4000-8000-000000000003', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'buyer@kira.demo',       'hashed_pw', 'Purchasing Lead', 'owner_purchasing'),
('11111111-0000-4000-8000-000000000004', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'ceo@hanyang.demo',      'hashed_pw', 'Hanyang CEO',     'supplier_ceo'),
('11111111-0000-4000-8000-000000000005', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'esg@globalmining.demo', 'hashed_pw', 'GMC ESG',         'supplier_esg'),
('11111111-0000-4000-8000-000000000006', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'esg@daesung.demo',      'hashed_pw', 'Daesung ESG',     'supplier_esg'),
('11111111-0000-4000-8000-000000000007', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'ceo@woojin.demo',       'hashed_pw', 'Woojin CEO',      'supplier_ceo');


-- ============================================================
-- 2. 고객사 마스터 (영역 7 선행) — OEM 2개
-- ============================================================
INSERT INTO customers (customer_id, customer_code, customer_name, country, source_system, external_id) VALUES
('c0000000-0000-4000-8000-0000000000b1', 'BMW',      'BMW AG',                'DE', 'ERP_PLM', 'ERP-CUST-BMW'),
('c0000000-0000-4000-8000-0000000000b2', 'MERCEDES', 'Mercedes-Benz Group AG','DE', 'ERP_PLM', 'ERP-CUST-MB');


-- ============================================================
-- 4. 협력사 마스터 (영역 2) — 12개사
-- ============================================================
-- 제조사/셀
INSERT INTO suppliers (supplier_id, tenant_id, company_name, company_name_en, company_name_ko, ceo_name, supplier_type, completeness_score, status, risk_level, feoc_status) VALUES
('a1111111-1111-4000-8000-000000000001', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '한양셀 제조(주)', 'Hanyang Cell Mfg',   '한양셀 제조(주)', 'Kim CEO',   'manufacturer', 92, 'supplier_verified',    'low',      'eligible'),
('a7777777-7777-4000-8000-000000000007', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '우진배터리(주)',  'Woojin Battery',     '우진배터리(주)',  'Park CEO',  'manufacturer', 90, 'supplier_verified',    'low',      'eligible'),
('a8888888-8888-4000-8000-000000000008', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '우진셀(주)',      'Woojin Cell',        '우진셀(주)',      'Park CTO',  'manufacturer', 88, 'supplier_verified',    'low',      'eligible');

-- CAM/전구체 (활물질·전구체 tier 4~5)
INSERT INTO suppliers (supplier_id, tenant_id, company_name, company_name_en, company_name_ko, ceo_name, supplier_type, completeness_score, status, risk_level, feoc_status) VALUES
('a2222222-2222-4000-8000-000000000002', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '동성머티리얼(주)', 'Dongsung Material', '동성머티리얼(주)', 'Choi CEO',  'manufacturer', 89, 'supplier_verified',    'low',      'eligible'),
('a4444444-4444-4000-8000-000000000004', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '대성정밀(주)',     'Daesung Precision', '대성정밀(주)',     'Lee CEO',   'manufacturer', 55, 'supplier_review',      'medium',   'under_review'),
('a6666666-6666-4000-8000-000000000006', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '청정전구체(주)',   'Cheongjeong Precursor','청정전구체(주)', 'Jung CEO',  'manufacturer', 85, 'supplier_verified',    'low',      'eligible');

-- 제련·정제 (tier 6)
INSERT INTO suppliers (supplier_id, tenant_id, company_name, company_name_en, company_name_ko, ceo_name, supplier_type, completeness_score, status, risk_level, feoc_status) VALUES
('aaaaaaaa-aaaa-4000-8000-00000000000a', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '한중제련(주)',    'Hanjung Refinery',  '한중제련(주)',    'Yoon CEO',  'manufacturer', 80, 'supplier_verified',    'low',      'eligible'),
('acacacac-acac-4000-8000-0000000000ac', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'Xinjiang Nickel Refinery', 'Xinjiang Nickel Refinery', NULL, 'Wang CEO', 'manufacturer', 60, 'supplier_review', 'high', 'under_review');

-- 광산 (tier 7)
INSERT INTO suppliers (supplier_id, tenant_id, company_name, company_name_en, company_name_ko, ceo_name, supplier_type, completeness_score, status, risk_level, feoc_status) VALUES
('a3333333-3333-4000-8000-000000000003', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '호주리튬광업', 'Australia Lithium Mining', NULL, 'Smith CEO', 'miner', 86, 'supplier_verified',  'low',      'eligible'),
('a9999999-9999-4000-8000-000000000009', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '칠레리튬광업', 'Chile Lithium Mining',     NULL, 'Garcia CEO','miner', 84, 'supplier_verified',  'low',      'eligible'),
('a5555555-5555-4000-8000-000000000005', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'Global Mining Corp', 'Global Mining Corp', NULL, 'Zhang CEO', 'miner', 35, 'supplier_violation', 'critical', 'ineligible');

-- 트레이더 (i4 Gray — 미확인 전구체)
INSERT INTO suppliers (supplier_id, tenant_id, company_name, company_name_en, supplier_type, completeness_score, status, risk_level, feoc_status) VALUES
('abababab-abab-4000-8000-0000000000ab', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'Unverified Precursor Trading', 'Unverified Precursor Trading', 'trader', 40, 'supplier_in_progress', 'medium', 'under_review');


-- ============================================================
-- 5. 공장 / 사업장 (영역 2) — PostGIS 좌표 (Geo Audit 핵심)
-- ============================================================
-- 신장 좌표 ST_MakePoint(86.0, 41.0) = 신장 폴리곤 내부 (Sad 위반 트리거)
INSERT INTO supplier_factories (factory_id, supplier_id, factory_name, factory_name_en, country, region, location, factory_role, destination, applicable_regulations, supply_ratio_percent) VALUES
-- 한양셀 [Happy] 포항(EU向)
('f1111111-0000-4000-8000-000000000001', 'a1111111-1111-4000-8000-000000000001', '포항 제1공장', 'Pohang Plant 1', 'KR', 'Pohang', ST_SetSRID(ST_MakePoint(129.343, 36.019), 4326), 'production', 'EU', '["EU_BATTERY","EU_BATTERY_ART7","EU_BATTERY_ART47","EUDR","CSDDD"]'::jsonb, 100.00),
-- 우진배터리 [Happy] 울산(EU向)
('f7777777-0000-4000-8000-000000000007', 'a7777777-7777-4000-8000-000000000007', '울산 공장', 'Ulsan Plant', 'KR', 'Ulsan', ST_SetSRID(ST_MakePoint(129.311, 35.538), 4326), 'production', 'EU', '["EU_BATTERY","EU_BATTERY_ART7","CSDDD"]'::jsonb, 100.00),
-- 우진셀
('f8888888-0000-4000-8000-000000000008', 'a8888888-8888-4000-8000-000000000008', '청주 셀공장', 'Cheongju Cell Plant', 'KR', 'Cheongju', ST_SetSRID(ST_MakePoint(127.489, 36.642), 4326), 'production', 'EU', '["EU_BATTERY"]'::jsonb, 100.00),
-- 동성머티리얼 CAM
('f2222222-0000-4000-8000-000000000002', 'a2222222-2222-4000-8000-000000000002', '천안 양극재공장', 'Cheonan CAM Plant', 'KR', 'Cheonan', ST_SetSRID(ST_MakePoint(127.114, 36.815), 4326), 'processing', 'BOTH', '["EU_BATTERY","CRMA","CONFLICT_MINERALS"]'::jsonb, 100.00),
-- 대성정밀 [Gray] 화성
('f4444444-0000-4000-8000-000000000004', 'a4444444-4444-4000-8000-000000000004', '화성 공장', 'Hwaseong Plant', 'KR', 'Hwaseong', ST_SetSRID(ST_MakePoint(126.831, 37.199), 4326), 'processing', 'EU', '["EU_BATTERY","CSDDD"]'::jsonb, 100.00),
-- 청정전구체 [Sad-Lot1 정상]
('f6666666-0000-4000-8000-000000000006', 'a6666666-6666-4000-8000-000000000006', '광양 전구체공장', 'Gwangyang Precursor', 'KR', 'Gwangyang', ST_SetSRID(ST_MakePoint(127.700, 34.940), 4326), 'processing', 'BOTH', '["EU_BATTERY","CRMA"]'::jsonb, 100.00),
-- 한중제련 tier6
('faaaaaaa-0000-4000-8000-00000000000a', 'aaaaaaaa-aaaa-4000-8000-00000000000a', '온산 제련소', 'Onsan Refinery', 'KR', 'Onsan', ST_SetSRID(ST_MakePoint(129.347, 35.428), 4326), 'processing', 'BOTH', '["IRA","CRMA"]'::jsonb, 100.00),
-- 신장니켈제련 [Sad tier6]
('facacaca-0000-4000-8000-0000000000ac', 'acacacac-acac-4000-8000-0000000000ac', 'Xinjiang Refinery', 'Xinjiang Refinery', 'CN', 'Xinjiang', ST_SetSRID(ST_MakePoint(86.150, 41.120), 4326), 'processing', 'US', '["UFLPA","IRA"]'::jsonb, 100.00),
-- 호주리튬광산 [Happy tier7]
('f3333333-0000-4000-8000-000000000003', 'a3333333-3333-4000-8000-000000000003', 'Greenbushes Mine', 'Greenbushes Mine', 'AU', 'Western Australia', ST_SetSRID(ST_MakePoint(116.060, -33.860), 4326), 'mining', 'BOTH', '["CRMA"]'::jsonb, 100.00),
-- 칠레리튬광산 [Happy tier7]
('f9999999-0000-4000-8000-000000000009', 'a9999999-9999-4000-8000-000000000009', 'Atacama Mine', 'Atacama Mine', 'CL', 'Antofagasta', ST_SetSRID(ST_MakePoint(-68.200, -23.500), 4326), 'mining', 'BOTH', '["CRMA"]'::jsonb, 100.00),
-- Global Mining 신장 광산 [Sad tier7 — 위반 핵심 노드]
('f5555555-0000-4000-8000-000000000005', 'a5555555-5555-4000-8000-000000000005', 'Xinjiang NCM Mine A', 'Xinjiang NCM Mine A', 'CN', 'Xinjiang', ST_SetSRID(ST_MakePoint(86.000, 41.000), 4326), 'mining', 'US', '["UFLPA","IRA"]'::jsonb, 100.00);

-- view_permissions: ESG 담당자가 한양셀 하위 3차수까지 열람
INSERT INTO view_permissions (user_id, viewable_supplier_id, can_view_parent, can_view_children, can_view_siblings, depth_limit, granted_by) VALUES
('11111111-0000-4000-8000-000000000002', 'a1111111-1111-4000-8000-000000000001', FALSE, TRUE, FALSE, 3, '11111111-0000-4000-8000-000000000001');

-- 연락 담당자 (주요 3사)
INSERT INTO supplier_contacts (supplier_id, factory_id, name, name_en, role, department, email, phone, is_primary, language) VALUES
('a1111111-1111-4000-8000-000000000001', 'f1111111-0000-4000-8000-000000000001', '김담당', 'Mr. Kim', 'ESG Manager', 'Sustainability', 'kim@hanyang.demo', '+82-54-000-0001', TRUE, 'ko'),
('a5555555-5555-4000-8000-000000000005', 'f5555555-0000-4000-8000-000000000005', 'Li Manager', 'Li Manager', 'Compliance', 'Compliance', 'li@globalmining.demo', '+86-991-000-0005', TRUE, 'en'),
('a4444444-4444-4000-8000-000000000004', 'f4444444-0000-4000-8000-000000000004', '이담당', 'Ms. Lee', 'Quality', 'QA', 'lee@daesung.demo', '+82-31-000-0004', TRUE, 'ko');

-- 온보딩 / SLA
INSERT INTO supplier_onboarding (supplier_id, consent_status, consent_signed_at, agreement_status, last_invited_at, sla_due_date, reminder_count) VALUES
('a1111111-1111-4000-8000-000000000001', 'consent_agreed',  now() - interval '20 days', 'agreed',  now() - interval '21 days', now() - interval '7 days', 0),
('a4444444-4444-4000-8000-000000000004', 'consent_agreed',  now() - interval '5 days',  'agreed',  now() - interval '6 days',  now() + interval '8 days', 1),
('abababab-abab-4000-8000-0000000000ab', 'consent_pending', NULL,                        'pending', now() - interval '22 days', now() - interval '8 days', 3);

-- 인증서
INSERT INTO supplier_certifications (supplier_id, certification_type, certification_no, issued_at, expires_at, issuing_body) VALUES
('a1111111-1111-4000-8000-000000000001', 'ISO 14001', 'ISO-14001-HY-2023', '2023-01-01', '2026-12-31', 'KAB'),
('a5555555-5555-4000-8000-000000000005', 'Bettercoal', 'BC-GMC-2022',       '2022-06-01', now()::date + 20, 'Bettercoal');


-- ============================================================
-- 3. 제품 마스터 4종 + BOM 버전 (영역 7) — 3축(고객사·기간·조성)
-- ============================================================
-- ① BMW iX3 50 — 108Ah 원통형 NCM811 [Happy]
-- ② BMW i4     — 81Ah 각형 NCM       [Gray]
-- ③ Mercedes GLC EV — 94Ah 각형 NCM  [Sad, 기간별 2 Lot]
-- ④ Mercedes EQS    — 118Ah 각형 NCM [Happy]
-- [순서 이동 이유] products.manufacturer_id → suppliers FK 의존.
--   suppliers 마스터(4번)와 공장(5번)이 모두 INSERT된 뒤에 와야 FK 위반이 안 난다.
INSERT INTO products (product_id, product_code, product_name, manufacturer_id, customer_id, model_name, amperage_ah, type, source_system, external_id) VALUES
('d1111111-0000-4000-8000-000000000001', 'BMW-IX3-NCM811-108', 'BMW iX3 Cylindrical NCM811 108Ah', 'a1111111-1111-4000-8000-000000000001', 'c0000000-0000-4000-8000-0000000000b1', 'iX3 50',  108.00, 'battery_pack', 'ERP_PLM', 'ERP-PROD-IX3'),
('d2222222-0000-4000-8000-000000000002', 'BMW-I4-NCM-81',      'BMW i4 Prismatic NCM 81Ah',        'a1111111-1111-4000-8000-000000000001', 'c0000000-0000-4000-8000-0000000000b1', 'i4',       81.00, 'battery_pack', 'ERP_PLM', 'ERP-PROD-I4'),
('d3333333-0000-4000-8000-000000000003', 'MB-GLC-NCM-94',      'Mercedes GLC EV Prismatic NCM 94Ah','a7777777-7777-4000-8000-000000000007', 'c0000000-0000-4000-8000-0000000000b2', 'GLC EV',   94.00, 'battery_pack', 'ERP_PLM', 'ERP-PROD-GLC'),
('d4444444-0000-4000-8000-000000000004', 'MB-EQS-NCM-118',     'Mercedes EQS Prismatic NCM 118Ah', 'a7777777-7777-4000-8000-000000000007', 'c0000000-0000-4000-8000-0000000000b2', 'EQS',     118.00, 'battery_pack', 'ERP_PLM', 'ERP-PROD-EQS');

-- BOM 버전: ③ GLC만 기간별 2 Lot(2024 정상 / 2025 신장 위반), 나머지 단일
INSERT INTO bom_versions (bom_version_id, product_id, version_number, production_from, production_to, status, source_system, external_id) VALUES
('e1111111-0000-4000-8000-000000000001', 'd1111111-0000-4000-8000-000000000001', '1.0', '2025-01-01', NULL,         'active',     'ERP_PLM', 'ERP-BOM-IX3'),
('e2222222-0000-4000-8000-000000000002', 'd2222222-0000-4000-8000-000000000002', '1.0', '2025-01-01', NULL,         'active',     'ERP_PLM', 'ERP-BOM-I4'),
('e3333333-0000-4000-8000-000000000031', 'd3333333-0000-4000-8000-000000000003', '1.0', '2024-01-01', '2024-12-31', 'deprecated', 'ERP_PLM', 'ERP-BOM-GLC-2024'),
('e3333333-0000-4000-8000-000000000032', 'd3333333-0000-4000-8000-000000000003', '2.0', '2025-01-01', NULL,         'active',     'ERP_PLM', 'ERP-BOM-GLC-2025'),
('e4444444-0000-4000-8000-000000000004', 'd4444444-0000-4000-8000-000000000004', '1.0', '2025-01-01', NULL,         'active',     'ERP_PLM', 'ERP-BOM-EQS');


-- ============================================================
-- 6. Provider Type CTI 상세 (영역 3)
-- ============================================================
-- 제조 탄소집약도 (EU 배터리법 Art.7)
INSERT INTO supplier_manufacturer_details (supplier_id, manufacturing_process, energy_source, capacity, carbon_intensity) VALUES
('a1111111-1111-4000-8000-000000000001', 'NCM811 Cell Assembly', 'renewable', '10GWh/yr', 2.3400),
('a7777777-7777-4000-8000-000000000007', 'Prismatic NCM Cell Assembly', 'renewable', '8GWh/yr', 2.5100),
('a2222222-2222-4000-8000-000000000002', 'CAM Sintering (NCM811)', 'mixed', '5GWh/yr', 3.1000),
-- 대성정밀: energy_source NULL (저신뢰 파싱 원인 — Gray)
('a4444444-4444-4000-8000-000000000004', 'NCM 양극재/활물질 가공', NULL, '2GWh/yr', NULL);

-- 신장 광산 상세 (Sad — Ni/Co/Mn/Li 원광) + 신장 좌표
INSERT INTO supplier_miner_details (supplier_id, mine_name, mining_method, extraction_volume, mine_coordinates, active_period_from) VALUES
('a5555555-5555-4000-8000-000000000005', 'Xinjiang NCM Mineral Mine A', 'open_pit', 50000.00, ST_SetSRID(ST_MakePoint(86.000, 41.000), 4326), '2020-01-01'),
('a3333333-3333-4000-8000-000000000003', 'Greenbushes Lithium', 'open_pit', 80000.00, ST_SetSRID(ST_MakePoint(116.060, -33.860), 4326), '2018-01-01'),
('a9999999-9999-4000-8000-000000000009', 'Atacama Brine', 'brine', 60000.00, ST_SetSRID(ST_MakePoint(-68.200, -23.500), 4326), '2019-01-01');

-- 트레이더 공개율 낮음 (i4 Gray)
INSERT INTO supplier_trader_details (supplier_id, trading_license, broker_certification, disclosure_completeness) VALUES
('abababab-abab-4000-8000-0000000000ab', 'TR-LIC-2023', NULL, 45.00);

INSERT INTO trader_disclosure_obligation (trader_supplier_id, upstream_supplier_id, disclosure_completeness, last_audited_at) VALUES
('abababab-abab-4000-8000-0000000000ab', 'a5555555-5555-4000-8000-000000000005', 45.00, now() - interval '10 days');


-- ============================================================
-- 7. 리스크 프로필 (영역 4)
-- ============================================================
INSERT INTO supplier_risk_profiles (supplier_id, overall_risk_score, risk_level, self_reported_risk_level, feoc_status, feoc_direct_ownership, is_high_risk_flag, high_risk_reasons, last_risk_review_at) VALUES
('a1111111-1111-4000-8000-000000000001', 10, 'low',      'low',     'eligible',     0.00,  FALSE, NULL, now() - interval '7 days'),
('a7777777-7777-4000-8000-000000000007', 10, 'low',      'low',     'eligible',     0.00,  FALSE, NULL, now() - interval '7 days'),
('a2222222-2222-4000-8000-000000000002', 15, 'low',      'low',     'eligible',     0.00,  FALSE, NULL, now() - interval '7 days'),
-- Global Mining: critical (FEOC 외국지분 28.5% > 25% 차단선, 신장)
('a5555555-5555-4000-8000-000000000005', 80, 'critical', 'medium',  'ineligible',  28.50, TRUE,  '["FEOC 우려국 지분 28.5% (25% 초과)","신장 인접 광산","UFLPA 강제노동 의혹"]'::jsonb, now() - interval '2 days'),
('acacacac-acac-4000-8000-0000000000ac', 55, 'high',     'low',     'under_review', 0.00, TRUE,  '["신장 인접 제련소"]'::jsonb, now() - interval '4 days'),
-- 대성정밀: medium (자료 미비)
('a4444444-4444-4000-8000-000000000004', 35, 'medium',   'low',     'under_review', 0.00, FALSE, '["자료 완성도 미흡"]'::jsonb, now() - interval '3 days'),
('abababab-abab-4000-8000-0000000000ab', 30, 'medium',   'unknown', 'under_review', 0.00, FALSE, '["공개율 45%"]'::jsonb, now() - interval '10 days');

-- 실사 기록 (Global Mining 보완 필요)
INSERT INTO supplier_audit_records (supplier_id, audit_date, audit_type, auditor, audit_status, inspector_id, result, next_audit_due) VALUES
('a5555555-5555-4000-8000-000000000005', now()::date - 30, 'on_site', 'Third Party Auditor', 'in_progress', '11111111-0000-4000-8000-000000000002', 'pending', now()::date + 30);

-- 인권 이슈 (Global Mining 강제노동 — UFLPA 근거)
INSERT INTO supplier_human_rights_issues (supplier_id, factory_id, issue_type, severity, description, detected_at, status, source) VALUES
('a5555555-5555-4000-8000-000000000005', 'f5555555-0000-4000-8000-000000000005', 'forced_labor', 'critical', '신장 지역 강제노동 의혹', now() - interval '40 days', 'open', 'NGO Report');

-- 산재 (조사중)
INSERT INTO supplier_industrial_accidents (supplier_id, factory_id, accident_date, accident_type, description, casualties, status) VALUES
('a5555555-5555-4000-8000-000000000005', 'f5555555-0000-4000-8000-000000000005', now()::date - 15, 'serious_injury', '광산 붕괴 사고', 2, 'investigating');


-- ============================================================
-- 8. 원산지 증명서 (영역 5)
-- ============================================================
INSERT INTO origin_certificates (supplier_id, factory_id, cert_type, cert_number, issuing_authority, issued_at, expires_at, origin_country, status) VALUES
('a1111111-1111-4000-8000-000000000001', 'f1111111-0000-4000-8000-000000000001', 'CONFLICT_FREE', 'CF-HY-2024',  'RMI',  '2024-06-01', now()::date + 200, 'KR', 'valid'),
('a3333333-3333-4000-8000-000000000003', 'f3333333-0000-4000-8000-000000000003', 'CONFLICT_FREE', 'CF-AU-2024',  'RMI',  '2024-05-01', now()::date + 250, 'AU', 'valid'),
('a9999999-9999-4000-8000-000000000009', 'f9999999-0000-4000-8000-000000000009', 'CONFLICT_FREE', 'CF-CL-2024',  'RMI',  '2024-04-01', now()::date + 240, 'CL', 'valid'),
('a5555555-5555-4000-8000-000000000005', 'f5555555-0000-4000-8000-000000000005', 'UFLPA_REBUTTAL','UF-GMC-2024', 'Self', '2024-01-01', now()::date + 15,  'CN', 'expiring_soon'),
('a4444444-4444-4000-8000-000000000004', 'f4444444-0000-4000-8000-000000000004', 'GENERAL',       'GEN-DS-2024', 'KCCI', '2024-03-01', now()::date + 100, 'KR', 'under_review');


-- ============================================================
-- 9. 교육 관리 (영역 6)
-- ============================================================
INSERT INTO training_materials (material_id, title, title_en, category, format, duration_minutes, required_for, version) VALUES
('a1111111-0000-4000-8000-0000000000a1', '인권 실사 교육', 'Human Rights DD',    'human_rights',      'online', 60, '["CSDDD"]'::jsonb, 'v1'),
('a1111111-0000-4000-8000-0000000000a2', '분쟁광물 교육',  'Conflict Minerals',  'conflict_minerals', 'video',  30, '["CONFLICT_MINERALS"]'::jsonb, 'v1');

INSERT INTO training_records (supplier_id, factory_id, material_id, trainee_count, total_eligible, completion_rate, completed_at, due_date, status) VALUES
('a1111111-1111-4000-8000-000000000001', 'f1111111-0000-4000-8000-000000000001', 'a1111111-0000-4000-8000-0000000000a1', 50, 50, 100.00, now() - interval '10 days', now()::date - 5, 'completed'),
('a5555555-5555-4000-8000-000000000005', 'f5555555-0000-4000-8000-000000000005', 'a1111111-0000-4000-8000-0000000000a1', 5,  40, 12.50,  NULL,                       now()::date - 10, 'overdue');


-- ============================================================
-- 10. 부품 7계층 트리 (영역 7) — NCM811 공유 마스터
-- ============================================================
-- T1 Pack → T2 Module → T3 Cell → T4 활물질(CAM·ANO)
--   → T5 전구체(PRE)·정제리튬(LiOH) → T6 제련(Ni·Co·Mn) → T7 광산 원광(Ni·Co·Mn·Li)
INSERT INTO parts (part_id, part_code, part_name, tier_level, parent_part_id, hs_code, material_type, unit_price, source_system, external_id) VALUES
-- T1
('b1111111-0000-4000-8000-000000000001', 'PACK-NCM811',  'Battery Pack',            1, NULL,                                     '850760', 'assembly',        1000.0000, 'ERP_PLM', 'ERP-PART-PACK'),
-- T2
('b1111111-0000-4000-8000-000000000002', 'MOD-NCM811',   'Module',                  2, 'b1111111-0000-4000-8000-000000000001', '850760', 'assembly',         400.0000, 'ERP_PLM', 'ERP-PART-MOD'),
-- T3
('b1111111-0000-4000-8000-000000000003', 'CELL-NCM811',  'Battery Cell',            3, 'b1111111-0000-4000-8000-000000000002', '850760', 'cell',             150.0000, 'ERP_PLM', 'ERP-PART-CELL'),
-- T4 활물질
('b1111111-0000-4000-8000-000000000006', 'CAM-NCM811',   'Cathode Active Material', 4, 'b1111111-0000-4000-8000-000000000003', '284190', 'active_material',    90.0000, 'ERP_PLM', 'ERP-PART-CAM'),
('b1111111-0000-4000-8000-000000000007', 'ANO-GRAPHITE', 'Anode Active Material',   4, 'b1111111-0000-4000-8000-000000000003', '380110', 'active_material',    30.0000, 'ERP_PLM', 'ERP-PART-ANO'),
-- T5 전구체·정제리튬
('b1111111-0000-4000-8000-000000000004', 'PRE-NCM',      'NCM Precursor',           5, 'b1111111-0000-4000-8000-000000000006', '382490', 'precursor',          40.0000, 'ERP_PLM', 'ERP-PART-PRE'),
('b1111111-0000-4000-8000-000000000005', 'LIOH-REFINED', 'Lithium Hydroxide',       5, 'b1111111-0000-4000-8000-000000000006', '282520', 'refined_metal',      84.0000, 'ERP_PLM', 'ERP-PART-LIOH'),
-- T6 제련 (전구체의 상위 = Ni·Co·Mn 황산염/정제금속)
('b1111111-0000-4000-8000-000000000011', 'REF-NI',       'Refined Nickel Sulfate',  6, 'b1111111-0000-4000-8000-000000000004', '283324', 'refined_metal',      22.0000, 'ERP_PLM', 'ERP-PART-REFNI'),
('b1111111-0000-4000-8000-000000000012', 'REF-CO',       'Refined Cobalt Sulfate',  6, 'b1111111-0000-4000-8000-000000000004', '283329', 'refined_metal',      36.0000, 'ERP_PLM', 'ERP-PART-REFCO'),
('b1111111-0000-4000-8000-000000000013', 'REF-MN',       'Refined Manganese Sulfate',6,'b1111111-0000-4000-8000-000000000004', '283339', 'refined_metal',       6.0000, 'ERP_PLM', 'ERP-PART-REFMN'),
-- T7 광산 원광 (제련의 상위)
('b1111111-0000-4000-8000-000000000008', 'MIN-NI',       'Nickel Ore',              7, 'b1111111-0000-4000-8000-000000000011', '260400', 'mineral',            18.0000, 'ERP_PLM', 'ERP-PART-NI'),
('b1111111-0000-4000-8000-000000000009', 'MIN-CO',       'Cobalt Ore',              7, 'b1111111-0000-4000-8000-000000000012', '260500', 'mineral',            32.0000, 'ERP_PLM', 'ERP-PART-CO'),
('b1111111-0000-4000-8000-00000000000a', 'MIN-MN',       'Manganese Ore',           7, 'b1111111-0000-4000-8000-000000000013', '260200', 'mineral',             4.0000, 'ERP_PLM', 'ERP-PART-MN'),
('b1111111-0000-4000-8000-00000000000b', 'MIN-LI',       'Lithium Ore (Spodumene)', 7, 'b1111111-0000-4000-8000-000000000005', '253090', 'mineral',            12.0000, 'ERP_PLM', 'ERP-PART-LI');

-- ------------------------------------------------------------
-- bom_items: 5개 BOM 버전에 동일 부품 트리 연결 (조성비 NCM811: Ni80/Co10/Mn10)
--   GLC는 Lot1(2024)/Lot2(2025) 2버전 — 동일 부품, 공급사만 supply_chain_map에서 분기
-- ------------------------------------------------------------
-- 매크로적으로 각 bom_version_id별 7계층 전 품목 반복.
-- ① BMW iX3 (e1)
INSERT INTO bom_items (bom_version_id, part_id, required_quantity, required_quantity_unit, percentage, direct_material_cost, origin_country, source_system, external_id) VALUES
('e1111111-0000-4000-8000-000000000001', 'b1111111-0000-4000-8000-000000000003', 100, 'ea', 60.00, 150.0000, 'KR', 'ERP_PLM', 'ERP-BI-IX3-CELL'),
('e1111111-0000-4000-8000-000000000001', 'b1111111-0000-4000-8000-000000000006', 40,  'kg', 18.00,  90.0000, 'KR', 'ERP_PLM', 'ERP-BI-IX3-CAM'),
('e1111111-0000-4000-8000-000000000001', 'b1111111-0000-4000-8000-000000000007', 35,  'kg', 12.00,  30.0000, 'KR', 'ERP_PLM', 'ERP-BI-IX3-ANO'),
('e1111111-0000-4000-8000-000000000001', 'b1111111-0000-4000-8000-000000000011', 24,  'kg',  8.00,  22.0000, 'KR', 'ERP_PLM', 'ERP-BI-IX3-REFNI'),
('e1111111-0000-4000-8000-000000000001', 'b1111111-0000-4000-8000-000000000008', 30,  'kg',  4.00,  18.0000, 'AU', 'ERP_PLM', 'ERP-BI-IX3-NI'),
('e1111111-0000-4000-8000-000000000001', 'b1111111-0000-4000-8000-00000000000b', 12,  'kg',  2.00,  12.0000, 'AU', 'ERP_PLM', 'ERP-BI-IX3-LI');

-- ② BMW i4 (e2) — Gray: 전구체 미확인
INSERT INTO bom_items (bom_version_id, part_id, required_quantity, required_quantity_unit, percentage, direct_material_cost, origin_country, source_system, external_id) VALUES
('e2222222-0000-4000-8000-000000000002', 'b1111111-0000-4000-8000-000000000003', 90,  'ea', 60.00, 150.0000, 'KR', 'ERP_PLM', 'ERP-BI-I4-CELL'),
('e2222222-0000-4000-8000-000000000002', 'b1111111-0000-4000-8000-000000000006', 38,  'kg', 18.00,  90.0000, 'KR', 'ERP_PLM', 'ERP-BI-I4-CAM'),
('e2222222-0000-4000-8000-000000000002', 'b1111111-0000-4000-8000-000000000004', 20,  'kg', 10.00,  40.0000, NULL, 'ERP_PLM', 'ERP-BI-I4-PRE');

-- ③ Mercedes GLC Lot1 2024 (e31) — 정상: 청정전구체
INSERT INTO bom_items (bom_version_id, part_id, required_quantity, required_quantity_unit, percentage, direct_material_cost, origin_country, source_system, external_id) VALUES
('e3333333-0000-4000-8000-000000000031', 'b1111111-0000-4000-8000-000000000003', 95,  'ea', 60.00, 150.0000, 'KR', 'ERP_PLM', 'ERP-BI-GLC1-CELL'),
('e3333333-0000-4000-8000-000000000031', 'b1111111-0000-4000-8000-000000000004', 22,  'kg', 12.00,  40.0000, 'KR', 'ERP_PLM', 'ERP-BI-GLC1-PRE');

-- ③ Mercedes GLC Lot2 2025 (e32) — Sad: Global Mining 신장 전구체
INSERT INTO bom_items (bom_version_id, part_id, required_quantity, required_quantity_unit, percentage, direct_material_cost, origin_country, source_system, external_id) VALUES
('e3333333-0000-4000-8000-000000000032', 'b1111111-0000-4000-8000-000000000003', 95,  'ea', 60.00, 150.0000, 'KR', 'ERP_PLM', 'ERP-BI-GLC2-CELL'),
('e3333333-0000-4000-8000-000000000032', 'b1111111-0000-4000-8000-000000000004', 22,  'kg', 12.00,  40.0000, 'CN', 'ERP_PLM', 'ERP-BI-GLC2-PRE'),
('e3333333-0000-4000-8000-000000000032', 'b1111111-0000-4000-8000-000000000008', 30,  'kg',  4.00,  18.0000, 'CN', 'ERP_PLM', 'ERP-BI-GLC2-NI');

-- ④ Mercedes EQS (e4) — Happy: 칠레리튬
INSERT INTO bom_items (bom_version_id, part_id, required_quantity, required_quantity_unit, percentage, direct_material_cost, origin_country, source_system, external_id) VALUES
('e4444444-0000-4000-8000-000000000004', 'b1111111-0000-4000-8000-000000000003', 110, 'ea', 60.00, 150.0000, 'KR', 'ERP_PLM', 'ERP-BI-EQS-CELL'),
('e4444444-0000-4000-8000-000000000004', 'b1111111-0000-4000-8000-000000000006', 45,  'kg', 18.00,  90.0000, 'KR', 'ERP_PLM', 'ERP-BI-EQS-CAM'),
('e4444444-0000-4000-8000-000000000004', 'b1111111-0000-4000-8000-00000000000b', 14,  'kg',  2.00,  12.0000, 'CL', 'ERP_PLM', 'ERP-BI-EQS-LI');

-- ------------------------------------------------------------
-- 협력사↔원청 코드 매핑
-- ------------------------------------------------------------
INSERT INTO part_code_mapping (part_id, supplier_id, supplier_part_code, original_part_code) VALUES
('b1111111-0000-4000-8000-000000000003', 'a1111111-1111-4000-8000-000000000001', 'HY-CELL-001', 'CELL-NCM811'),
('b1111111-0000-4000-8000-000000000006', 'a2222222-2222-4000-8000-000000000002', 'DM-CAM-001',  'CAM-NCM811'),
('b1111111-0000-4000-8000-000000000004', 'a4444444-4444-4000-8000-000000000004', 'DS-PRE-001',  'PRE-NCM'),
('b1111111-0000-4000-8000-000000000008', 'a5555555-5555-4000-8000-000000000005', 'GMC-NI-001',  'MIN-NI'),
('b1111111-0000-4000-8000-00000000000b', 'a3333333-3333-4000-8000-000000000003', 'AU-LI-001',   'MIN-LI');

-- ------------------------------------------------------------
-- 공정 (CSDDD 추적)
-- ------------------------------------------------------------
INSERT INTO manufacturing_process (part_id, sequence_no, process_name, is_outsourced) VALUES
('b1111111-0000-4000-8000-000000000003', 1, 'Cell Coating',      FALSE),
('b1111111-0000-4000-8000-000000000003', 2, 'Cell Assembly',     FALSE),
('b1111111-0000-4000-8000-000000000006', 1, 'Cathode Sintering', FALSE),
('b1111111-0000-4000-8000-000000000011', 1, 'Nickel Refining',   FALSE);


-- ============================================================
-- 11. 공급망 맵 (영역 8) — 7계층 hop 연결
-- ============================================================
-- hop_level: 원청(parent NULL)=1 기준. Cell=hop1, CAM=hop2, 전구체/Li=hop3, 제련=hop4, 광산=hop5
-- ------------------------------------------------------------
-- ① BMW iX3 [Happy] 한양셀→동성CAM→(전구체 자체)→한중제련→호주리튬
INSERT INTO supply_chain_map (map_id, bom_version_id, parent_supplier_id, child_supplier_id, part_id, hop_level, link_status, source_system, verification_status) VALUES
('51111111-0000-4000-8000-000000000001', 'e1111111-0000-4000-8000-000000000001', NULL,                                     'a1111111-1111-4000-8000-000000000001', 'b1111111-0000-4000-8000-000000000003', 1, 'supplychain_confirmed', 'ERP', 'verified'),
('51111111-0000-4000-8000-000000000002', 'e1111111-0000-4000-8000-000000000001', 'a1111111-1111-4000-8000-000000000001', 'a2222222-2222-4000-8000-000000000002', 'b1111111-0000-4000-8000-000000000006', 2, 'supplychain_confirmed', 'SUPPLIER_DECLARED', 'verified'),
('51111111-0000-4000-8000-000000000003', 'e1111111-0000-4000-8000-000000000001', 'a2222222-2222-4000-8000-000000000002', 'aaaaaaaa-aaaa-4000-8000-00000000000a', 'b1111111-0000-4000-8000-000000000011', 4, 'supplychain_confirmed', 'SUPPLIER_DECLARED', 'verified'),
('51111111-0000-4000-8000-000000000004', 'e1111111-0000-4000-8000-000000000001', 'aaaaaaaa-aaaa-4000-8000-00000000000a', 'a3333333-3333-4000-8000-000000000003', 'b1111111-0000-4000-8000-000000000008', 5, 'supplychain_confirmed', 'SUPPLIER_DECLARED', 'verified'),
('51111111-0000-4000-8000-000000000005', 'e1111111-0000-4000-8000-000000000001', 'a2222222-2222-4000-8000-000000000002', 'a3333333-3333-4000-8000-000000000003', 'b1111111-0000-4000-8000-00000000000b', 3, 'supplychain_confirmed', 'SUPPLIER_DECLARED', 'verified');

-- ② BMW i4 [Gray] 한양셀→동성CAM→미확인트레이더(전구체, 선언만)
INSERT INTO supply_chain_map (map_id, bom_version_id, parent_supplier_id, child_supplier_id, part_id, hop_level, link_status, source_system, verification_status) VALUES
('52222222-0000-4000-8000-000000000001', 'e2222222-0000-4000-8000-000000000002', NULL,                                     'a1111111-1111-4000-8000-000000000001', 'b1111111-0000-4000-8000-000000000003', 1, 'supplychain_confirmed', 'ERP', 'verified'),
('52222222-0000-4000-8000-000000000002', 'e2222222-0000-4000-8000-000000000002', 'a1111111-1111-4000-8000-000000000001', 'a2222222-2222-4000-8000-000000000002', 'b1111111-0000-4000-8000-000000000006', 2, 'supplychain_confirmed', 'SUPPLIER_DECLARED', 'verified'),
('52222222-0000-4000-8000-000000000003', 'e2222222-0000-4000-8000-000000000002', 'a2222222-2222-4000-8000-000000000002', 'abababab-abab-4000-8000-0000000000ab', 'b1111111-0000-4000-8000-000000000004', 3, 'supplychain_declared',  'SUPPLIER_DECLARED', 'unverified');

-- ③ Mercedes GLC Lot1 2024 [Sad-정상] 우진셀→대성CAM→청정전구체
INSERT INTO supply_chain_map (map_id, bom_version_id, parent_supplier_id, child_supplier_id, part_id, hop_level, link_status, source_system, verification_status) VALUES
('53111111-0000-4000-8000-000000000001', 'e3333333-0000-4000-8000-000000000031', NULL,                                     'a8888888-8888-4000-8000-000000000008', 'b1111111-0000-4000-8000-000000000003', 1, 'supplychain_confirmed', 'ERP', 'verified'),
('53111111-0000-4000-8000-000000000002', 'e3333333-0000-4000-8000-000000000031', 'a8888888-8888-4000-8000-000000000008', 'a6666666-6666-4000-8000-000000000006', 'b1111111-0000-4000-8000-000000000004', 3, 'supplychain_confirmed', 'SUPPLIER_DECLARED', 'verified');

-- ③ Mercedes GLC Lot2 2025 [Sad-위반] 우진셀→대성CAM→Global Mining(신장 전구체·니켈)
INSERT INTO supply_chain_map (map_id, bom_version_id, parent_supplier_id, child_supplier_id, part_id, hop_level, link_status, source_system, verification_status) VALUES
('53222222-0000-4000-8000-000000000001', 'e3333333-0000-4000-8000-000000000032', NULL,                                     'a8888888-8888-4000-8000-000000000008', 'b1111111-0000-4000-8000-000000000003', 1, 'supplychain_confirmed', 'ERP', 'verified'),
('53222222-0000-4000-8000-000000000002', 'e3333333-0000-4000-8000-000000000032', 'a8888888-8888-4000-8000-000000000008', 'acacacac-acac-4000-8000-0000000000ac', 'b1111111-0000-4000-8000-000000000004', 3, 'supplychain_confirmed', 'SUPPLIER_DECLARED', 'verified'),
('53222222-0000-4000-8000-000000000003', 'e3333333-0000-4000-8000-000000000032', 'acacacac-acac-4000-8000-0000000000ac', 'a5555555-5555-4000-8000-000000000005', 'b1111111-0000-4000-8000-000000000008', 5, 'supplychain_confirmed', 'SUPPLIER_DECLARED', 'verified');

-- ④ Mercedes EQS [Happy] 우진배터리→동성CAM→칠레리튬
INSERT INTO supply_chain_map (map_id, bom_version_id, parent_supplier_id, child_supplier_id, part_id, hop_level, link_status, source_system, verification_status) VALUES
('54444444-0000-4000-8000-000000000001', 'e4444444-0000-4000-8000-000000000004', NULL,                                     'a7777777-7777-4000-8000-000000000007', 'b1111111-0000-4000-8000-000000000003', 1, 'supplychain_confirmed', 'ERP', 'verified'),
('54444444-0000-4000-8000-000000000002', 'e4444444-0000-4000-8000-000000000004', 'a7777777-7777-4000-8000-000000000007', 'a2222222-2222-4000-8000-000000000002', 'b1111111-0000-4000-8000-000000000006', 2, 'supplychain_confirmed', 'SUPPLIER_DECLARED', 'verified'),
('54444444-0000-4000-8000-000000000003', 'e4444444-0000-4000-8000-000000000004', 'a2222222-2222-4000-8000-000000000002', 'a9999999-9999-4000-8000-000000000009', 'b1111111-0000-4000-8000-00000000000b', 3, 'supplychain_confirmed', 'SUPPLIER_DECLARED', 'verified');

-- 분할 납품 비율 (iX3 셀 — 한양 단일공장 100%)
INSERT INTO supply_ratio (map_id, factory_id, ratio_percentage, volume, unit) VALUES
('51111111-0000-4000-8000-000000000001', 'f1111111-0000-4000-8000-000000000001', 100.00, 10000, 'ea');


-- ============================================================
-- 12. 운영 / 배치 / DPP (영역 9) — 4제품 배치
-- ============================================================
-- ① iX3 [Happy] EU向 발행완료
INSERT INTO batches (batch_id, product_id, bom_version_id, tenant_id, destination, current_stage, status, confidence_score, source_system, external_id) VALUES
('ba111111-0000-4000-8000-000000000001', 'd1111111-0000-4000-8000-000000000001', 'e1111111-0000-4000-8000-000000000001', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'EU', 'stage_issuance',   'batch_completed', 0.9600, 'MES', 'MES-LOT-IX3'),
-- ② i4 [Gray] EU向 저신뢰 → HITL 대기
('ba222222-0000-4000-8000-000000000002', 'd2222222-0000-4000-8000-000000000002', 'e2222222-0000-4000-8000-000000000002', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'EU', 'stage_compliance', 'batch_hitl_wait',  0.7000, 'MES', 'MES-LOT-I4'),
-- ③ GLC Lot2 [Sad] US向 risk 70+ → HITL 반려 예정
('ba333333-0000-4000-8000-000000000003', 'd3333333-0000-4000-8000-000000000003', 'e3333333-0000-4000-8000-000000000032', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'US', 'stage_risk',       'batch_hitl_wait',  0.9100, 'MES', 'MES-LOT-GLC2'),
-- ④ EQS [Happy] EU向 발행완료
('ba444444-0000-4000-8000-000000000004', 'd4444444-0000-4000-8000-000000000004', 'e4444444-0000-4000-8000-000000000004', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'EU', 'stage_issuance',   'batch_completed', 0.9500, 'MES', 'MES-LOT-EQS');

-- DPP (Happy 2건 — iX3, EQS 발행완료)
INSERT INTO dpp_records (dpp_id, batch_id, product_id, issued_at, status, carbon_footprint, recycled_content, qr_code_url, payload, approved_by) VALUES
('dccc1111-0000-4000-8000-000000000001', 'ba111111-0000-4000-8000-000000000001', 'd1111111-0000-4000-8000-000000000001', now() - interval '1 day', 'dpp_issued', 12.3400, '{"Co":15,"Ni":12,"Li":8}'::jsonb, 'https://dpp.kira.demo/qr/ix3', '{"readiness_breakdown":{"all_tier_completion":true,"no_violation":true}}'::jsonb, '11111111-0000-4000-8000-000000000002'),
('dccc4444-0000-4000-8000-000000000004', 'ba444444-0000-4000-8000-000000000004', 'd4444444-0000-4000-8000-000000000004', now() - interval '2 days', 'dpp_issued', 13.1000, '{"Co":16,"Ni":13,"Li":9}'::jsonb, 'https://dpp.kira.demo/qr/eqs', '{"readiness_breakdown":{"all_tier_completion":true,"no_violation":true}}'::jsonb, '11111111-0000-4000-8000-000000000002');


-- ============================================================
-- 13. 규제 / 컴플라이언스 (영역 10) — 배치별 판정
-- ============================================================
-- ① iX3 [Happy] EU 통과
INSERT INTO compliance_results (batch_id, regulation_id, supplier_id, verdict, needs_human_review, cited_clauses, confidence_score, reasoning_text)
SELECT 'ba111111-0000-4000-8000-000000000001', regulation_id, 'a1111111-1111-4000-8000-000000000001', 'compliance_passed', FALSE, '["EU 2023/1542 Art.7"]'::jsonb, 0.96, '탄소발자국 신고 정상'
FROM regulations WHERE regulation_code = 'EU_BATTERY_ART7';

-- ④ EQS [Happy] EU 통과
INSERT INTO compliance_results (batch_id, regulation_id, supplier_id, verdict, needs_human_review, cited_clauses, confidence_score, reasoning_text)
SELECT 'ba444444-0000-4000-8000-000000000004', regulation_id, 'a7777777-7777-4000-8000-000000000007', 'compliance_passed', FALSE, '["EU 2023/1542 Art.7"]'::jsonb, 0.95, '탄소발자국 신고 정상'
FROM regulations WHERE regulation_code = 'EU_BATTERY_ART7';

-- ② i4 [Gray] EU_BATTERY 회색지대 (needs_human_review)
INSERT INTO compliance_results (batch_id, regulation_id, supplier_id, verdict, needs_human_review, cited_clauses, confidence_score, reasoning_text)
SELECT 'ba222222-0000-4000-8000-000000000002', regulation_id, 'a4444444-4444-4000-8000-000000000004', 'compliance_warning', TRUE, '["EU 2023/1542"]'::jsonb, 0.70, '전구체 원산지 미확인 — 사람 검토 필요'
FROM regulations WHERE regulation_code = 'EU_BATTERY';

-- ③ GLC Lot2 [Sad] UFLPA 위반
INSERT INTO compliance_results (batch_id, regulation_id, supplier_id, verdict, needs_human_review, cited_clauses, confidence_score, reasoning_text)
SELECT 'ba333333-0000-4000-8000-000000000003', regulation_id, 'a5555555-5555-4000-8000-000000000005', 'compliance_violation', FALSE, '["UFLPA Sec.3"]'::jsonb, 0.93, '신장 강제노동 의혹 — 위반'
FROM regulations WHERE regulation_code = 'UFLPA';

-- ③ GLC Lot2 [Sad] IRA FEOC 위반 (외국지분 28.5% > 25%)
INSERT INTO compliance_results (batch_id, regulation_id, supplier_id, verdict, needs_human_review, cited_clauses, confidence_score, reasoning_text)
SELECT 'ba333333-0000-4000-8000-000000000003', regulation_id, 'a5555555-5555-4000-8000-000000000005', 'compliance_violation', FALSE, '["IRA FEOC"]'::jsonb, 0.94, 'FEOC 우려국 지분 28.5% 초과 — 차단'
FROM regulations WHERE regulation_code = 'IRA';


-- ============================================================
-- 14. 데이터 흐름 / Submission (영역 11)
-- ============================================================
INSERT INTO data_request_log (request_id, requester_user_id, target_supplier_id, requested_data_type, requested_at, due_date, response_status, submission_status) VALUES
('da111111-0000-4000-8000-000000000001', '11111111-0000-4000-8000-000000000002', 'a1111111-1111-4000-8000-000000000001', '탄소발자국 증빙', now() - interval '15 days', now() - interval '1 day', 'response_responded', 'submission_approved'),
('da444444-0000-4000-8000-000000000004', '11111111-0000-4000-8000-000000000002', 'a4444444-4444-4000-8000-000000000004', '공장 정보',       now() - interval '6 days',  now() + interval '8 days', 'response_responded', 'submission_rework'),
('daababab-0000-4000-8000-0000000000ab', '11111111-0000-4000-8000-000000000002', 'abababab-abab-4000-8000-0000000000ab', '원산지 증빙',     now() - interval '22 days', now() - interval '8 days', 'response_escalated', 'submission_requested');

INSERT INTO submission_documents (document_id, request_id, supplier_id, file_url, file_name, file_type, doc_category, file_hash, uploaded_by) VALUES
('d0c11111-0000-4000-8000-000000000001', 'da111111-0000-4000-8000-000000000001', 'a1111111-1111-4000-8000-000000000001', 's3://kira-docs/hy_carbon.pdf',  'hy_carbon.pdf',  'pdf',  'carbon_data', 'a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90', '11111111-0000-4000-8000-000000000004'),
('d0c44444-0000-4000-8000-000000000004', 'da444444-0000-4000-8000-000000000004', 'a4444444-4444-4000-8000-000000000004', 's3://kira-docs/ds_factory.xlsx','ds_factory.xlsx','xlsx', 'factory_doc', 'b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90a1', '11111111-0000-4000-8000-000000000006');

INSERT INTO document_extraction_results (request_id, document_id, parsed_fields, confidence_map, unparsed_fields, supplier_confirmed, confirmed_at) VALUES
('da111111-0000-4000-8000-000000000001', 'd0c11111-0000-4000-8000-000000000001', '{"carbon_intensity":2.34,"energy_source":"renewable"}'::jsonb, '{"carbon_intensity":0.96,"energy_source":0.91}'::jsonb, '[]'::jsonb, TRUE, now() - interval '2 days'),
('da444444-0000-4000-8000-000000000004', 'd0c44444-0000-4000-8000-000000000004', '{"factory_name":"화성 공장","capacity":"2GWh"}'::jsonb, '{"factory_name":0.95,"capacity":0.62}'::jsonb, '["energy_source"]'::jsonb, FALSE, NULL);

INSERT INTO submission_status_history (request_id, from_status, to_status, actor_id, reason) VALUES
('da111111-0000-4000-8000-000000000001', 'submission_submitted', 'submission_approved', '11111111-0000-4000-8000-000000000002', '검토 통과'),
('da444444-0000-4000-8000-000000000004', 'submission_review',    'submission_rework',  '11111111-0000-4000-8000-000000000002', '자료 보완 요청');

INSERT INTO data_completeness_status (entity_type, entity_id, required_field_count, filled_field_count, completion_rate, missing_fields, last_updated_by) VALUES
('supplier', 'a1111111-1111-4000-8000-000000000001', 12, 11, 91.67, '[]'::jsonb, '11111111-0000-4000-8000-000000000002'),
('supplier', 'a4444444-4444-4000-8000-000000000004', 12, 7,  58.33, '["energy_source","cert"]'::jsonb, '11111111-0000-4000-8000-000000000002');

INSERT INTO notifications (user_id, channel, notification_type, subject, body, status, dedup_key) VALUES
('11111111-0000-4000-8000-000000000005', 'email', 'sla_warning', 'SLA 임박', '원산지 증빙 제출 기한이 지났습니다', 'pending', 'sla_reminder:daababab:2026-05-29');


-- ============================================================
-- 15. 감사 추적 / HITL (영역 12)
-- ============================================================
-- HITL: ③ Sad=risk_escalated 반려예정 / ② Gray=gray_zone 검토대기
INSERT INTO hitl_reviews (review_id, batch_id, reason, trigger_stage, assigned_to, status) VALUES
('41111111-0000-4000-8000-000000000003', 'ba333333-0000-4000-8000-000000000003', 'risk_escalated', 'stage_risk',       '11111111-0000-4000-8000-000000000002', 'hitl_pending'),
('41111111-0000-4000-8000-000000000002', 'ba222222-0000-4000-8000-000000000002', 'gray_zone',      'stage_compliance', '11111111-0000-4000-8000-000000000002', 'hitl_pending');

-- 감사 해시체인 (iX3 Happy 최소 예시)
INSERT INTO audit_trail (batch_id, step_number, node_type, node_name, input_hash, output_hash, prev_hash, duration_ms) VALUES
('ba111111-0000-4000-8000-000000000001', 1, 'agent', 'data_gateway', '0000000000000000000000000000000000000000000000000000000000000001', '0000000000000000000000000000000000000000000000000000000000000002', NULL, 120),
('ba111111-0000-4000-8000-000000000001', 2, 'agent', 'compliance',   '0000000000000000000000000000000000000000000000000000000000000002', '0000000000000000000000000000000000000000000000000000000000000003', '0000000000000000000000000000000000000000000000000000000000000002', 340);