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
-- [7계층 트리] 0 Pack / 1 Module / 2 Cell / 3 활물질(CAM·ANO)
--             / 4 전구체 / 5 제련·정제 / 6 광산
--
-- [4대 시나리오]
--   ① BMW iX3 (108Ah 원통 NCM811) ── Happy: 한양셀→동성CAM→호주리튬, FEOC 통과 → 발행 완료
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
('11111111-0000-4000-8000-000000000001', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'admin@kira.demo',       '$2b$12$XO1O./JYL5VKDkodX2RdpOZSfFA7PSkeViaPqiOSQG4szW7fGVjf.', 'Admin User',      'admin'),
('11111111-0000-4000-8000-000000000002', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'esg@kira.demo',         '$2b$12$XO1O./JYL5VKDkodX2RdpOZSfFA7PSkeViaPqiOSQG4szW7fGVjf.', 'ESG Manager',     'owner_esg'),
('11111111-0000-4000-8000-000000000003', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'buyer@kira.demo',       '$2b$12$XO1O./JYL5VKDkodX2RdpOZSfFA7PSkeViaPqiOSQG4szW7fGVjf.', 'Purchasing Lead', 'owner_purchasing'),
('11111111-0000-4000-8000-000000000004', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'ceo@hanyang.demo',      '$2b$12$XO1O./JYL5VKDkodX2RdpOZSfFA7PSkeViaPqiOSQG4szW7fGVjf.', 'Hanyang CEO',     'supplier_ceo'),
('11111111-0000-4000-8000-000000000005', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'esg@globalmining.demo', '$2b$12$XO1O./JYL5VKDkodX2RdpOZSfFA7PSkeViaPqiOSQG4szW7fGVjf.', 'GMC ESG',         'supplier_esg'),
('11111111-0000-4000-8000-000000000006', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'esg@daesung.demo',      '$2b$12$XO1O./JYL5VKDkodX2RdpOZSfFA7PSkeViaPqiOSQG4szW7fGVjf.', 'Daesung ESG',     'supplier_esg'),
('11111111-0000-4000-8000-000000000007', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'ceo@woojin.demo',       '$2b$12$XO1O./JYL5VKDkodX2RdpOZSfFA7PSkeViaPqiOSQG4szW7fGVjf.', 'Woojin CEO',      'supplier_ceo');

-- 데모 로그인 계정 (프론트 로그인 화면 기본값 — oem/supplier). password: demo1234
-- (구 alembic 0004_demo_accounts 에서 이관 — DDL/데이터 모두 docker schema·seed 로 일원화)
INSERT INTO users (user_id, tenant_id, email, password_hash, name, role) VALUES
('11111111-0000-4000-8000-0000000000a1', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'oem@kira.demo',              '$2b$12$LdrfIceVZR7twTzU8rxKF.M0uqv9vmcUawZNKRoLjbjb9gAidiynS', 'Demo OEM',          'admin'),
('11111111-0000-4000-8000-0000000000b1', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'supplier@hanyang-cell.com',  '$2b$12$LdrfIceVZR7twTzU8rxKF.M0uqv9vmcUawZNKRoLjbjb9gAidiynS', '한양셀 데모 협력사', 'supplier_ceo');

-- 협력사 계정 ↔ 본인 supplier 매핑 (§0.5 — 로그인 supplier_id 클레임 / 협력사 포털 스코프 소스).
-- 데모 협력사 = 한양셀 제조(주)(a1111111). ceo@hanyang.demo 도 동일 회사로 매핑.
UPDATE users SET supplier_id = 'a1111111-1111-4000-8000-000000000001'
 WHERE email IN ('supplier@hanyang-cell.com', 'ceo@hanyang.demo');


-- ============================================================
-- 2. 고객사 마스터 (영역 7 선행) — OEM 2개
-- ============================================================
INSERT INTO customers (customer_id, customer_code, customer_name, country, source_system, external_id) VALUES
('c0000000-0000-4000-8000-0000000000b1', 'BMW',      'BMW AG',                'DE', 'ERP_PLM', 'ERP-CUST-BMW'),
('c0000000-0000-4000-8000-0000000000b2', 'MERCEDES', 'Mercedes-Benz Group AG','DE', 'ERP_PLM', 'ERP-CUST-MB');


-- ============================================================
-- 4. 협력사 마스터 (영역 2) — 원청 1 + 협력사 12개사
-- ============================================================
-- 원청 (OEM, tier0) — 공급망 트리 루트. supply_chain_map 최상위 parent로 사용.
-- 본질은 배터리 팩 '제조사'(provider_type=manufacturer). 원청/협력사 구분은 tier0(hop0)로.
INSERT INTO suppliers (supplier_id, tenant_id, company_name, company_name_en, company_name_ko, ceo_name, provider_type, completeness_score, status, risk_level) VALUES
('a0000000-0000-4000-8000-000000000000', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'KIRA Energy Solutions', 'KIRA Energy Solutions', '키라에너지솔루션(주)', 'KIRA CEO', 'manufacturer', 100, 'supplier_verified', 'low');

-- 제조사/셀
INSERT INTO suppliers (supplier_id, tenant_id, company_name, company_name_en, company_name_ko, ceo_name, provider_type, completeness_score, status, risk_level) VALUES
('a1111111-1111-4000-8000-000000000001', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '한양셀 제조(주)', 'Hanyang Cell Mfg',   '한양셀 제조(주)', 'Kim CEO',   'manufacturer', 92, 'supplier_verified',    'low'),
('a7777777-7777-4000-8000-000000000007', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '우진배터리(주)',  'Woojin Battery',     '우진배터리(주)',  'Park CEO',  'manufacturer', 90, 'supplier_verified',    'low'),
('a8888888-8888-4000-8000-000000000008', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '우진셀(주)',      'Woojin Cell',        '우진셀(주)',      'Park CTO',  'manufacturer', 88, 'supplier_verified',    'low');

-- CAM/전구체 (활물질·전구체 tier 4~5)
INSERT INTO suppliers (supplier_id, tenant_id, company_name, company_name_en, company_name_ko, ceo_name, provider_type, completeness_score, status, risk_level) VALUES
('a2222222-2222-4000-8000-000000000002', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '동성머티리얼(주)', 'Dongsung Material', '동성머티리얼(주)', 'Choi CEO',  'manufacturer', 89, 'supplier_verified',    'low'),
('a4444444-4444-4000-8000-000000000004', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '대성정밀(주)',     'Daesung Precision', '대성정밀(주)',     'Lee CEO',   'manufacturer', 55, 'supplier_review',      'medium'),
('a6666666-6666-4000-8000-000000000006', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '청정전구체(주)',   'Cheongjeong Precursor','청정전구체(주)', 'Jung CEO',  'manufacturer', 85, 'supplier_verified',    'low');

-- 제련·정제 (tier 6)
INSERT INTO suppliers (supplier_id, tenant_id, company_name, company_name_en, company_name_ko, ceo_name, provider_type, completeness_score, status, risk_level) VALUES
('aaaaaaaa-aaaa-4000-8000-00000000000a', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '한중제련(주)',    'Hanjung Refinery',  '한중제련(주)',    'Yoon CEO',  'smelter', 80, 'supplier_verified',    'low'),
('acacacac-acac-4000-8000-0000000000ac', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'Xinjiang Nickel Refinery', 'Xinjiang Nickel Refinery', NULL, 'Wang CEO', 'smelter', 60, 'supplier_review', 'high');

-- 제련소 세부(RMI 기준): 검증완료 = RMAP conformant → rmi / 고위험 신장 = private.
UPDATE suppliers SET smelter_type = 'rmi'     WHERE supplier_id = 'aaaaaaaa-aaaa-4000-8000-00000000000a';
UPDATE suppliers SET smelter_type = 'private' WHERE supplier_id = 'acacacac-acac-4000-8000-0000000000ac';

-- 광산 (tier 7)
INSERT INTO suppliers (supplier_id, tenant_id, company_name, company_name_en, company_name_ko, ceo_name, provider_type, completeness_score, status, risk_level) VALUES
('a3333333-3333-4000-8000-000000000003', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '호주리튬광업', 'Australia Lithium Mining', NULL, 'Smith CEO', 'miner', 86, 'supplier_verified',  'low'),
('a9999999-9999-4000-8000-000000000009', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '칠레리튬광업', 'Chile Lithium Mining',     NULL, 'Garcia CEO','miner', 84, 'supplier_verified',  'low'),
('a5555555-5555-4000-8000-000000000005', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'Global Mining Corp', 'Global Mining Corp', NULL, 'Zhang CEO', 'miner', 35, 'supplier_violation', 'critical');

-- 트레이더 (i4 Gray — 미확인 전구체)
INSERT INTO suppliers (supplier_id, tenant_id, company_name, company_name_en, provider_type, completeness_score, status, risk_level) VALUES
('abababab-abab-4000-8000-0000000000ab', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'Unverified Precursor Trading', 'Unverified Precursor Trading', 'trader', 40, 'supplier_in_progress', 'medium');

-- 소재 국가(ISO 3166-1 alpha-2) 시드 — INSERT에 country 미포함이라 전부 null이던 것 보완(화면 '미입력' 해소).
UPDATE suppliers SET country = CASE supplier_id
  WHEN 'a0000000-0000-4000-8000-000000000000' THEN 'KR'  -- KIRA Energy Solutions(OEM)
  WHEN 'a1111111-1111-4000-8000-000000000001' THEN 'KR'  -- 한양셀 제조
  WHEN 'a7777777-7777-4000-8000-000000000007' THEN 'KR'  -- 우진배터리
  WHEN 'a8888888-8888-4000-8000-000000000008' THEN 'KR'  -- 우진셀
  WHEN 'a2222222-2222-4000-8000-000000000002' THEN 'KR'  -- 동성머티리얼
  WHEN 'a4444444-4444-4000-8000-000000000004' THEN 'KR'  -- 대성정밀
  WHEN 'a6666666-6666-4000-8000-000000000006' THEN 'KR'  -- 청정전구체
  WHEN 'aaaaaaaa-aaaa-4000-8000-00000000000a' THEN 'KR'  -- 한중제련
  WHEN 'acacacac-acac-4000-8000-0000000000ac' THEN 'CN'  -- Xinjiang Nickel Refinery
  WHEN 'a3333333-3333-4000-8000-000000000003' THEN 'AU'  -- 호주리튬광업
  WHEN 'a9999999-9999-4000-8000-000000000009' THEN 'CL'  -- 칠레리튬광업
  WHEN 'a5555555-5555-4000-8000-000000000005' THEN 'CN'  -- Global Mining Corp(신장 인접·FEOC 부적격)
  WHEN 'abababab-abab-4000-8000-0000000000ab' THEN 'CN'  -- Unverified Precursor Trading(미확인 원산지)
  ELSE country END;


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
('faaaaaaa-0000-4000-8000-00000000000a', 'aaaaaaaa-aaaa-4000-8000-00000000000a', '온산 제련소', 'Onsan Refinery', 'KR', 'Onsan', ST_SetSRID(ST_MakePoint(129.347, 35.428), 4326), 'processing', 'BOTH', '["CRMA"]'::jsonb, 100.00),
-- 신장니켈제련 [Sad tier6]
('facacaca-0000-4000-8000-0000000000ac', 'acacacac-acac-4000-8000-0000000000ac', 'Xinjiang Refinery', 'Xinjiang Refinery', 'CN', 'Xinjiang', ST_SetSRID(ST_MakePoint(86.150, 41.120), 4326), 'processing', 'US', '["UFLPA"]'::jsonb, 100.00),
-- 호주리튬광산 [Happy tier7]
('f3333333-0000-4000-8000-000000000003', 'a3333333-3333-4000-8000-000000000003', 'Greenbushes Mine', 'Greenbushes Mine', 'AU', 'Western Australia', ST_SetSRID(ST_MakePoint(116.060, -33.860), 4326), 'mining', 'BOTH', '["CRMA"]'::jsonb, 100.00),
-- 칠레리튬광산 [Happy tier7]
('f9999999-0000-4000-8000-000000000009', 'a9999999-9999-4000-8000-000000000009', 'Atacama Mine', 'Atacama Mine', 'CL', 'Antofagasta', ST_SetSRID(ST_MakePoint(-68.200, -23.500), 4326), 'mining', 'BOTH', '["CRMA"]'::jsonb, 100.00),
-- Global Mining 신장 광산 [Sad tier7 — 위반 핵심 노드]
('f5555555-0000-4000-8000-000000000005', 'a5555555-5555-4000-8000-000000000005', 'Xinjiang NCM Mine A', 'Xinjiang NCM Mine A', 'CN', 'Xinjiang', ST_SetSRID(ST_MakePoint(86.000, 41.000), 4326), 'mining', 'US', '["UFLPA"]'::jsonb, 100.00);

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


-- ============================================================
-- 3. 제품 마스터 4종 + BOM 버전 (영역 7) — 3축(고객사·기간·조성)
-- ============================================================
-- ① BMW iX3 50 — 108Ah 원통형 NCM811 [Happy]
-- ② BMW i4     — 81Ah 각형 NCM       [Gray]
-- ③ Mercedes GLC EV — 94Ah 각형 NCM  [Sad, 기간별 2 Lot]
-- ④ Mercedes EQS    — 118Ah 각형 NCM [Happy]
-- [순서 이동 이유] products.manufacturer_id → suppliers FK 의존.
--   suppliers 마스터(4번)와 공장(5번)이 모두 INSERT된 뒤에 와야 FK 위반이 안 난다.
INSERT INTO products (product_id, product_code, product_name, manufacturer_id, tenant_id, customer_id, model_name, amperage_ah, type, source_system, external_id) VALUES
-- manufacturer_id = KIRA(원청·팩 제조사). 우리가 만드는 팩이므로 제조사는 KIRA. (이전 시드: 한양셀/우진배터리로 잘못 지정)
-- product_name/code: 실제 셀 제조사 관례대로 자사 브랜드(KIRA PRiMX)+사양(폼팩터·화학조성·용량). OEM 차종은 제품명에 박지 않고
-- customer_id(고객사)와 model_name(납품 차종)으로 분리 — (제품 × 고객사 × 단위기간) 그룹핑·검색·맵핑을 위해.
('d1111111-0000-4000-8000-000000000001', 'KE-CYL-NCM811-108', 'KIRA PRiMX Cylindrical NCM811 108Ah', 'a0000000-0000-4000-8000-000000000000', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'c0000000-0000-4000-8000-0000000000b1', 'iX3 50',  108.00, 'battery_pack', 'ERP_PLM', 'ERP-PROD-IX3'),
('d2222222-0000-4000-8000-000000000002', 'KE-PRI-NCM-081',    'KIRA PRiMX Prismatic NCM 81Ah',       'a0000000-0000-4000-8000-000000000000', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'c0000000-0000-4000-8000-0000000000b1', 'i4',       81.00, 'battery_pack', 'ERP_PLM', 'ERP-PROD-I4'),
('d3333333-0000-4000-8000-000000000003', 'KE-PRI-NCM-094',    'KIRA PRiMX Prismatic NCM 94Ah',       'a0000000-0000-4000-8000-000000000000', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'c0000000-0000-4000-8000-0000000000b2', 'GLC EV',   94.00, 'battery_pack', 'ERP_PLM', 'ERP-PROD-GLC'),
('d4444444-0000-4000-8000-000000000004', 'KE-PRI-NCM-118',    'KIRA PRiMX Prismatic NCM 118Ah',      'a0000000-0000-4000-8000-000000000000', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'c0000000-0000-4000-8000-0000000000b2', 'EQS',     118.00, 'battery_pack', 'ERP_PLM', 'ERP-PROD-EQS');

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


-- ============================================================
-- 7. 리스크 프로필 (영역 4)
-- ============================================================
INSERT INTO supplier_risk_profiles (supplier_id, overall_risk_score, risk_level, self_reported_risk_level, is_high_risk_flag, high_risk_reasons, last_risk_review_at) VALUES
-- 원청 (tier0 루트) — 트리 루트 노드 색상/리스크 NULL 방지용 최소 프로필
('a0000000-0000-4000-8000-000000000000', 0,  'low',      'low',     FALSE, NULL, now() - interval '7 days'),
('a1111111-1111-4000-8000-000000000001', 10, 'low',      'low',     FALSE, NULL, now() - interval '7 days'),
('a7777777-7777-4000-8000-000000000007', 10, 'low',      'low',     FALSE, NULL, now() - interval '7 days'),
('a2222222-2222-4000-8000-000000000002', 15, 'low',      'low',     FALSE, NULL, now() - interval '7 days'),
-- Global Mining: critical (신장 인접 광산 / UFLPA)
('a5555555-5555-4000-8000-000000000005', 80, 'critical', 'medium',  TRUE,  '["신장 인접 광산","UFLPA 강제노동 의혹"]'::jsonb, now() - interval '2 days'),
('acacacac-acac-4000-8000-0000000000ac', 55, 'high',     'low',     TRUE,  '["신장 인접 제련소"]'::jsonb, now() - interval '4 days'),
-- 대성정밀: medium (자료 미비)
('a4444444-4444-4000-8000-000000000004', 35, 'medium',   'low',     FALSE, '["자료 완성도 미흡"]'::jsonb, now() - interval '3 days'),
('abababab-abab-4000-8000-0000000000ab', 30, 'medium',   'unknown', FALSE, '["공개율 45%"]'::jsonb, now() - interval '10 days');

-- 실사 기록 (Global Mining 보완 필요)
INSERT INTO supplier_audit_records (supplier_id, audit_date, audit_type, auditor, audit_status, inspector_id, result, next_audit_due) VALUES
('a5555555-5555-4000-8000-000000000005', now()::date - 30, 'on_site', 'Third Party Auditor', 'in_progress', '11111111-0000-4000-8000-000000000002', 'pending', now()::date + 30);


-- ============================================================
-- 10. 부품 7계층 트리 (영역 7) — NCM811 공유 마스터
-- ============================================================
-- T0 Pack → T1 Module → T2 Cell → T3 활물질(CAM·ANO)
--   → T4 전구체(PRE)·정제리튬(LiOH) → T5 제련(Ni·Co·Mn) → T6 광산 원광(Ni·Co·Mn·Li)
INSERT INTO parts (part_id, part_code, part_name, tier_level, parent_part_id, hs_code, material_type, unit_price, source_system, external_id) VALUES
-- T1
('b1111111-0000-4000-8000-000000000001', 'PACK-NCM811',  'Battery Pack',            0, NULL,                                     '850760', 'assembly',        1000.0000, 'ERP_PLM', 'ERP-PART-PACK'),
-- T2
('b1111111-0000-4000-8000-000000000002', 'MOD-NCM811',   'Module',                  1, 'b1111111-0000-4000-8000-000000000001', '850760', 'assembly',         400.0000, 'ERP_PLM', 'ERP-PART-MOD'),
-- T3
('b1111111-0000-4000-8000-000000000003', 'CELL-NCM811',  'Battery Cell',            2, 'b1111111-0000-4000-8000-000000000002', '850760', 'cell',             150.0000, 'ERP_PLM', 'ERP-PART-CELL'),
-- T4 활물질
('b1111111-0000-4000-8000-000000000006', 'CAM-NCM811',   'Cathode Active Material', 3, 'b1111111-0000-4000-8000-000000000003', '284190', 'active_material',    90.0000, 'ERP_PLM', 'ERP-PART-CAM'),
('b1111111-0000-4000-8000-000000000007', 'ANO-GRAPHITE', 'Anode Active Material',   3, 'b1111111-0000-4000-8000-000000000003', '380110', 'active_material',    30.0000, 'ERP_PLM', 'ERP-PART-ANO'),
-- T5 전구체·정제리튬
('b1111111-0000-4000-8000-000000000004', 'PRE-NCM',      'NCM Precursor',           4, 'b1111111-0000-4000-8000-000000000006', '382490', 'precursor',          40.0000, 'ERP_PLM', 'ERP-PART-PRE'),
('b1111111-0000-4000-8000-000000000005', 'LIOH-REFINED', 'Lithium Hydroxide',       4, 'b1111111-0000-4000-8000-000000000006', '282520', 'refined_metal',      84.0000, 'ERP_PLM', 'ERP-PART-LIOH'),
-- T6 제련 (전구체의 상위 = Ni·Co·Mn 황산염/정제금속)
('b1111111-0000-4000-8000-000000000011', 'REF-NI',       'Refined Nickel Sulfate',  5, 'b1111111-0000-4000-8000-000000000004', '283324', 'refined_metal',      22.0000, 'ERP_PLM', 'ERP-PART-REFNI'),
('b1111111-0000-4000-8000-000000000012', 'REF-CO',       'Refined Cobalt Sulfate',  5, 'b1111111-0000-4000-8000-000000000004', '283329', 'refined_metal',      36.0000, 'ERP_PLM', 'ERP-PART-REFCO'),
('b1111111-0000-4000-8000-000000000013', 'REF-MN',       'Refined Manganese Sulfate',5,'b1111111-0000-4000-8000-000000000004', '283339', 'refined_metal',       6.0000, 'ERP_PLM', 'ERP-PART-REFMN'),
-- T7 광산 원광 (제련의 상위)
('b1111111-0000-4000-8000-000000000008', 'MIN-NI',       'Nickel Ore',              6, 'b1111111-0000-4000-8000-000000000011', '260400', 'mineral',            18.0000, 'ERP_PLM', 'ERP-PART-NI'),
('b1111111-0000-4000-8000-000000000009', 'MIN-CO',       'Cobalt Ore',              6, 'b1111111-0000-4000-8000-000000000012', '260500', 'mineral',            32.0000, 'ERP_PLM', 'ERP-PART-CO'),
('b1111111-0000-4000-8000-00000000000a', 'MIN-MN',       'Manganese Ore',           6, 'b1111111-0000-4000-8000-000000000013', '260200', 'mineral',             4.0000, 'ERP_PLM', 'ERP-PART-MN'),
('b1111111-0000-4000-8000-00000000000b', 'MIN-LI',       'Lithium Ore (Spodumene)', 6, 'b1111111-0000-4000-8000-000000000005', '253090', 'mineral',            12.0000, 'ERP_PLM', 'ERP-PART-LI');

-- 부품 용도/기능(parts.function_purpose) 시드 — INSERT에 미포함이라 전부 null이던 것 보완(BOM 트리 표시용).
UPDATE parts SET function_purpose = CASE part_code
  WHEN 'PACK-NCM811'  THEN 'EV 구동용 배터리 팩 — 셀·모듈 통합 및 BMS 제어'
  WHEN 'MOD-NCM811'   THEN '셀 직병렬 묶음 모듈 — 전압 구성·열 관리'
  WHEN 'CELL-NCM811'  THEN '전기 저장·방출 단위 셀(NCM811)'
  WHEN 'ANO-GRAPHITE' THEN '음극 활물질 — 리튬이온 흡장·방출(흑연)'
  WHEN 'CAM-NCM811'   THEN '양극 활물질 — 에너지밀도 결정(NCM811)'
  WHEN 'LIOH-REFINED' THEN '양극재 합성용 리튬 원료(수산화리튬)'
  WHEN 'PRE-NCM'      THEN '양극 활물질 전구체(Ni·Co·Mn 수산화물)'
  WHEN 'REF-CO'       THEN '전구체용 정제 코발트(황산코발트)'
  WHEN 'REF-MN'       THEN '전구체용 정제 망간(황산망간)'
  WHEN 'REF-NI'       THEN '전구체용 정제 니켈(황산니켈)'
  WHEN 'MIN-CO'       THEN '코발트 원광 — 정제 전 원자재'
  WHEN 'MIN-LI'       THEN '리튬 원광(스포듀민) — 수산화리튬 원자재'
  WHEN 'MIN-MN'       THEN '망간 원광 — 정제 전 원자재'
  WHEN 'MIN-NI'       THEN '니켈 원광 — 정제 전 원자재'
  ELSE function_purpose END;

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
-- 11. 공급망 맵 (영역 8) — 원청 루트 + hop 경로순번 연속 연결
-- ============================================================
-- [차수 SSOT] hop_level = 원청(parent NULL)=0 기준 경로 순번(+1 연속, 건너뛰기 금지).
--   · 트리 루트 = 원청 KIRA Energy Solutions(a0..0) 가 Pack(hop0) 을 만든다.
--   · 부품 tier(bom_depth=parts.tier_level)와는 독립축 → 같은 hop 이라도 tier 는 다를 수 있고,
--     겸업/계층건너뜀 시 hop != tier 가 정상.
--   · 겸업(다중역할) 공급사는 같은 supplier_id 가 연속 hop 에 self-edge(parent=child)로 중복 등장.
--     예) 한양셀 = Module(hop1) + Cell(hop2).
-- ------------------------------------------------------------
-- ① BMW iX3 [Happy] 원청→한양셀(Module→Cell 겸업)→동성CAM→한중제련→호주리튬
INSERT INTO supply_chain_map (edge_id, bom_version_id, parent_supplier_id, child_supplier_id, part_id, hop_level, link_status, source_system, verification_status, supply_period_from, supply_period_to) VALUES
('51111111-0000-4000-8000-000000000001', 'e1111111-0000-4000-8000-000000000001', NULL,                                     'a0000000-0000-4000-8000-000000000000', 'b1111111-0000-4000-8000-000000000001', 0, 'supplychain_confirmed', 'ERP', 'verified', '2025-01-01', '2025-12-31'),
('51111111-0000-4000-8000-000000000002', 'e1111111-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-000000000000', 'a1111111-1111-4000-8000-000000000001', 'b1111111-0000-4000-8000-000000000002', 1, 'supplychain_confirmed', 'ERP', 'verified', '2025-01-01', '2025-12-31'),
('51111111-0000-4000-8000-000000000003', 'e1111111-0000-4000-8000-000000000001', 'a1111111-1111-4000-8000-000000000001', 'a1111111-1111-4000-8000-000000000001', 'b1111111-0000-4000-8000-000000000003', 2, 'supplychain_confirmed', 'ERP', 'verified', '2025-01-01', '2025-12-31'),
('51111111-0000-4000-8000-000000000004', 'e1111111-0000-4000-8000-000000000001', 'a1111111-1111-4000-8000-000000000001', 'a2222222-2222-4000-8000-000000000002', 'b1111111-0000-4000-8000-000000000006', 3, 'supplychain_confirmed', 'SUPPLIER_DECLARED', 'verified', '2025-01-01', '2025-12-31'),
('51111111-0000-4000-8000-000000000005', 'e1111111-0000-4000-8000-000000000001', 'a2222222-2222-4000-8000-000000000002', 'aaaaaaaa-aaaa-4000-8000-00000000000a', 'b1111111-0000-4000-8000-000000000011', 4, 'supplychain_confirmed', 'SUPPLIER_DECLARED', 'verified', '2025-01-01', '2025-12-31'),
('51111111-0000-4000-8000-000000000006', 'e1111111-0000-4000-8000-000000000001', 'aaaaaaaa-aaaa-4000-8000-00000000000a', 'a3333333-3333-4000-8000-000000000003', 'b1111111-0000-4000-8000-000000000008', 5, 'supplychain_confirmed', 'SUPPLIER_DECLARED', 'verified', '2025-01-01', '2025-12-31');

-- ② BMW i4 [Gray] 원청→한양셀(Module→Cell 겸업)→동성CAM→미확인트레이더(전구체, 선언만)
INSERT INTO supply_chain_map (edge_id, bom_version_id, parent_supplier_id, child_supplier_id, part_id, hop_level, link_status, source_system, verification_status, supply_period_from, supply_period_to) VALUES
('52222222-0000-4000-8000-000000000001', 'e2222222-0000-4000-8000-000000000002', NULL,                                     'a0000000-0000-4000-8000-000000000000', 'b1111111-0000-4000-8000-000000000001', 0, 'supplychain_confirmed', 'ERP', 'verified', '2025-01-01', '2025-12-31'),
('52222222-0000-4000-8000-000000000002', 'e2222222-0000-4000-8000-000000000002', 'a0000000-0000-4000-8000-000000000000', 'a1111111-1111-4000-8000-000000000001', 'b1111111-0000-4000-8000-000000000002', 1, 'supplychain_confirmed', 'ERP', 'verified', '2025-01-01', '2025-12-31'),
('52222222-0000-4000-8000-000000000003', 'e2222222-0000-4000-8000-000000000002', 'a1111111-1111-4000-8000-000000000001', 'a1111111-1111-4000-8000-000000000001', 'b1111111-0000-4000-8000-000000000003', 2, 'supplychain_confirmed', 'ERP', 'verified', '2025-01-01', '2025-12-31'),
('52222222-0000-4000-8000-000000000004', 'e2222222-0000-4000-8000-000000000002', 'a1111111-1111-4000-8000-000000000001', 'a2222222-2222-4000-8000-000000000002', 'b1111111-0000-4000-8000-000000000006', 3, 'supplychain_confirmed', 'SUPPLIER_DECLARED', 'verified', '2025-01-01', '2025-12-31'),
('52222222-0000-4000-8000-000000000005', 'e2222222-0000-4000-8000-000000000002', 'a2222222-2222-4000-8000-000000000002', 'abababab-abab-4000-8000-0000000000ab', 'b1111111-0000-4000-8000-000000000004', 4, 'supplychain_declared',  'SUPPLIER_DECLARED', 'unverified', '2025-01-01', '2025-12-31');

-- ③ Mercedes GLC Lot1 2024 [Sad-정상] 원청→우진셀→청정전구체 (CAM 계층 건너뜀: hop 연속, tier 점프)
INSERT INTO supply_chain_map (edge_id, bom_version_id, parent_supplier_id, child_supplier_id, part_id, hop_level, link_status, source_system, verification_status, supply_period_from, supply_period_to) VALUES
('53111111-0000-4000-8000-000000000001', 'e3333333-0000-4000-8000-000000000031', NULL,                                     'a0000000-0000-4000-8000-000000000000', 'b1111111-0000-4000-8000-000000000001', 0, 'supplychain_confirmed', 'ERP', 'verified', '2024-01-01', '2024-12-31'),
('53111111-0000-4000-8000-000000000002', 'e3333333-0000-4000-8000-000000000031', 'a0000000-0000-4000-8000-000000000000', 'a8888888-8888-4000-8000-000000000008', 'b1111111-0000-4000-8000-000000000003', 1, 'supplychain_confirmed', 'ERP', 'verified', '2024-01-01', '2024-12-31'),
('53111111-0000-4000-8000-000000000003', 'e3333333-0000-4000-8000-000000000031', 'a8888888-8888-4000-8000-000000000008', 'a6666666-6666-4000-8000-000000000006', 'b1111111-0000-4000-8000-000000000004', 2, 'supplychain_confirmed', 'SUPPLIER_DECLARED', 'verified', '2024-01-01', '2024-12-31');

-- ③ Mercedes GLC Lot2 2025 [Sad-위반] 원청→우진셀→신장니켈제련(전구체)→Global Mining(신장 니켈광산)
INSERT INTO supply_chain_map (edge_id, bom_version_id, parent_supplier_id, child_supplier_id, part_id, hop_level, link_status, source_system, verification_status, supply_period_from, supply_period_to) VALUES
('53222222-0000-4000-8000-000000000001', 'e3333333-0000-4000-8000-000000000032', NULL,                                     'a0000000-0000-4000-8000-000000000000', 'b1111111-0000-4000-8000-000000000001', 0, 'supplychain_confirmed', 'ERP', 'verified', '2025-01-01', '2025-12-31'),
('53222222-0000-4000-8000-000000000002', 'e3333333-0000-4000-8000-000000000032', 'a0000000-0000-4000-8000-000000000000', 'a8888888-8888-4000-8000-000000000008', 'b1111111-0000-4000-8000-000000000003', 1, 'supplychain_confirmed', 'ERP', 'verified', '2025-01-01', '2025-12-31'),
('53222222-0000-4000-8000-000000000003', 'e3333333-0000-4000-8000-000000000032', 'a8888888-8888-4000-8000-000000000008', 'acacacac-acac-4000-8000-0000000000ac', 'b1111111-0000-4000-8000-000000000004', 2, 'supplychain_confirmed', 'SUPPLIER_DECLARED', 'verified', '2025-01-01', '2025-12-31'),
('53222222-0000-4000-8000-000000000004', 'e3333333-0000-4000-8000-000000000032', 'acacacac-acac-4000-8000-0000000000ac', 'a5555555-5555-4000-8000-000000000005', 'b1111111-0000-4000-8000-000000000008', 3, 'supplychain_confirmed', 'SUPPLIER_DECLARED', 'verified', '2025-01-01', '2025-12-31');

-- ④ Mercedes EQS [Happy] 원청→우진배터리→동성CAM→칠레리튬
INSERT INTO supply_chain_map (edge_id, bom_version_id, parent_supplier_id, child_supplier_id, part_id, hop_level, link_status, source_system, verification_status, supply_period_from, supply_period_to) VALUES
('54444444-0000-4000-8000-000000000001', 'e4444444-0000-4000-8000-000000000004', NULL,                                     'a0000000-0000-4000-8000-000000000000', 'b1111111-0000-4000-8000-000000000001', 0, 'supplychain_confirmed', 'ERP', 'verified', '2025-01-01', '2025-12-31'),
('54444444-0000-4000-8000-000000000002', 'e4444444-0000-4000-8000-000000000004', 'a0000000-0000-4000-8000-000000000000', 'a7777777-7777-4000-8000-000000000007', 'b1111111-0000-4000-8000-000000000003', 1, 'supplychain_confirmed', 'ERP', 'verified', '2025-01-01', '2025-12-31'),
('54444444-0000-4000-8000-000000000003', 'e4444444-0000-4000-8000-000000000004', 'a7777777-7777-4000-8000-000000000007', 'a2222222-2222-4000-8000-000000000002', 'b1111111-0000-4000-8000-000000000006', 2, 'supplychain_confirmed', 'SUPPLIER_DECLARED', 'verified', '2025-01-01', '2025-12-31'),
('54444444-0000-4000-8000-000000000004', 'e4444444-0000-4000-8000-000000000004', 'a2222222-2222-4000-8000-000000000002', 'aaaaaaaa-aaaa-4000-8000-00000000000a', 'b1111111-0000-4000-8000-000000000005', 3, 'supplychain_confirmed', 'SUPPLIER_DECLARED', 'verified', '2025-01-01', '2025-12-31'),
-- hop4: 한중제련(smelter)→칠레리튬(광산). 광산은 무조건 상위 제련소(smelter)와 엮여야 함(정보관리 주체=smelter).
('54444444-0000-4000-8000-000000000005', 'e4444444-0000-4000-8000-000000000004', 'aaaaaaaa-aaaa-4000-8000-00000000000a', 'a9999999-9999-4000-8000-000000000009', 'b1111111-0000-4000-8000-00000000000b', 4, 'supplychain_confirmed', 'SUPPLIER_DECLARED', 'verified', '2025-01-01', '2025-12-31');

-- 공급망 맵 헤더(supply_chain_maps): bom_version(제품×Lot)당 1개. 엣지의 map_id(헤더 FK) 백필.
INSERT INTO supply_chain_maps (map_id, bom_version_id, product_id, status)
SELECT gen_random_uuid(), bv.bom_version_id, bv.product_id, 'completed'
FROM bom_versions bv
WHERE EXISTS (SELECT 1 FROM supply_chain_map scm WHERE scm.bom_version_id = bv.bom_version_id);
UPDATE supply_chain_map scm SET map_id = h.map_id
FROM supply_chain_maps h WHERE h.bom_version_id = scm.bom_version_id;

-- 분할 납품 비율 (iX3 1차 납품: 한양셀→원청, hop1 — 한양 단일공장 100%)
--   최상위 납품 조인이 hop_level=1 엣지의 supply_ratio.volume 을 사용 → hop1(edge ...002)에 연결.
INSERT INTO supply_ratio (edge_id, factory_id, ratio_percentage, volume, unit) VALUES
('51111111-0000-4000-8000-000000000002', 'f1111111-0000-4000-8000-000000000001', 100.00, 10000, 'ea');

-- 공장별 탄소발자국 선언 (EU 배터리법 ART7)
-- 기존 공급사 단위 carbon_intensity → 공장 단위 선언으로 이관.
-- 대성정밀 화성공장(f4)은 의도적으로 미INSERT → ART7 선언 누락 → needs_human_review 트리거 유지.
INSERT INTO factory_carbon_declarations (factory_id, carbon_intensity, methodology, declared_at, valid_from, source) VALUES
('f1111111-0000-4000-8000-000000000001', 2.3400, 'PEF', '2025-01-01', '2025-01-01', 'third_party_verified'),  -- 한양셀 포항 (Happy)
('f7777777-0000-4000-8000-000000000007', 2.5100, 'PEF', '2025-01-01', '2025-01-01', 'third_party_verified'),  -- 우진배터리 울산 (Happy)
('f2222222-0000-4000-8000-000000000002', 3.1000, 'PEF', '2025-01-01', '2025-01-01', 'supplier_declared');     -- 동성머티리얼 천안


-- ============================================================
-- 12. 운영 / 배치 (영역 9) — 4제품 배치
-- ============================================================
-- ① iX3 [Happy] EU向 발행완료
INSERT INTO batches (batch_id, product_id, bom_version_id, tenant_id, destination, current_stage, status, confidence_score, source_system, external_id) VALUES
('ba111111-0000-4000-8000-000000000001', 'd1111111-0000-4000-8000-000000000001', 'e1111111-0000-4000-8000-000000000001', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'EU', 'stage_risk',   'batch_completed', 0.9600, 'MES', 'MES-LOT-IX3'),
-- ② i4 [Gray] EU向 저신뢰 → HITL 대기
('ba222222-0000-4000-8000-000000000002', 'd2222222-0000-4000-8000-000000000002', 'e2222222-0000-4000-8000-000000000002', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'EU', 'stage_compliance', 'batch_hitl_wait',  0.7000, 'MES', 'MES-LOT-I4'),
-- ③ GLC Lot2 [Sad] US向 risk 70+ → HITL 반려 예정
('ba333333-0000-4000-8000-000000000003', 'd3333333-0000-4000-8000-000000000003', 'e3333333-0000-4000-8000-000000000032', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'US', 'stage_risk',       'batch_hitl_wait',  0.9100, 'MES', 'MES-LOT-GLC2'),
-- ④ EQS [Happy] EU向 발행완료
('ba444444-0000-4000-8000-000000000004', 'd4444444-0000-4000-8000-000000000004', 'e4444444-0000-4000-8000-000000000004', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'EU', 'stage_risk',   'batch_completed', 0.9500, 'MES', 'MES-LOT-EQS');


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

-- ③ GLC Lot2 [Sad] EU 배터리 탄소발자국 위반 (신고 탄소집약도 기준 초과)
--   근거: Global Mining 제출 탄소발자국 증빙(da555555)의 carbon_intensity 18.7 > 기준 16.
INSERT INTO compliance_results (batch_id, regulation_id, supplier_id, verdict, needs_human_review, cited_clauses, confidence_score, reasoning_text)
SELECT 'ba333333-0000-4000-8000-000000000003', regulation_id, 'a5555555-5555-4000-8000-000000000005', 'compliance_violation', FALSE, '["EU 2023/1542 Art.7"]'::jsonb, 0.93, '신고 탄소집약도 18.7 kgCO2e/kWh — 기준 16 초과, 화석연료(석탄) 기반 검증 불일치'
FROM regulations WHERE regulation_code = 'EU_BATTERY_ART7';


-- ============================================================
-- 13-B. W5 C1 — 규제별 필수 필드 명세 시드 (regulation_required_fields)
-- ============================================================
-- EU_BATTERY_ART7 (Art.7 / Annex II — 탄소발자국)
INSERT INTO regulation_required_fields (regulation_id, field_name, field_type, provider_type_applicable, is_mandatory)
SELECT regulation_id, 'carbon_intensity', 'numeric', '["manufacturer"]'::jsonb, TRUE
FROM regulations WHERE regulation_code = 'EU_BATTERY_ART7';

INSERT INTO regulation_required_fields (regulation_id, field_name, field_type, provider_type_applicable, is_mandatory)
SELECT regulation_id, 'factory_carbon_declarations', 'jsonb', '["manufacturer"]'::jsonb, TRUE
FROM regulations WHERE regulation_code = 'EU_BATTERY_ART7';

-- EUDR (삼림벌채 — GPS)
INSERT INTO regulation_required_fields (regulation_id, field_name, field_type, provider_type_applicable, is_mandatory)
SELECT regulation_id, 'mine_coordinates', 'geojson', '["miner"]'::jsonb, TRUE
FROM regulations WHERE regulation_code = 'EUDR';

-- UFLPA (강제노동 위험 플래그)
INSERT INTO regulation_required_fields (regulation_id, field_name, field_type, provider_type_applicable, is_mandatory)
SELECT regulation_id, 'geo_risk_flags', 'jsonb', '["miner"]'::jsonb, FALSE
FROM regulations WHERE regulation_code = 'UFLPA';


-- ============================================================
-- 14. 데이터 흐름 / Submission (영역 11)
-- ============================================================
INSERT INTO data_request_log (request_id, requester_user_id, target_supplier_id, requested_data_type, requested_at, due_date, response_status, submission_status) VALUES
('da111111-0000-4000-8000-000000000001', '11111111-0000-4000-8000-000000000002', 'a1111111-1111-4000-8000-000000000001', '탄소발자국 증빙', now() - interval '15 days', now() - interval '1 day', 'response_responded', 'submission_approved'),
('da444444-0000-4000-8000-000000000004', '11111111-0000-4000-8000-000000000002', 'a4444444-4444-4000-8000-000000000004', '공장 정보',       now() - interval '6 days',  now() + interval '8 days', 'response_responded', 'submission_rework'),
('daababab-0000-4000-8000-0000000000ab', '11111111-0000-4000-8000-000000000002', 'abababab-abab-4000-8000-0000000000ab', '원산지 증빙',     now() - interval '22 days', now() - interval '8 days', 'response_escalated', 'submission_requested'),
('da555555-0000-4000-8000-000000000005', '11111111-0000-4000-8000-000000000002', 'a5555555-5555-4000-8000-000000000005', '탄소발자국 증빙', now() - interval '5 days',  now() - interval '1 day',  'response_responded', 'submission_submitted');

INSERT INTO submission_documents (document_id, request_id, supplier_id, file_url, file_name, file_type, doc_category, file_hash, uploaded_by) VALUES
('d0c11111-0000-4000-8000-000000000001', 'da111111-0000-4000-8000-000000000001', 'a1111111-1111-4000-8000-000000000001', 's3://kira-docs/hy_carbon.pdf',  'hy_carbon.pdf',  'pdf',  'carbon_footprint_declaration', 'a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90', '11111111-0000-4000-8000-000000000004'),
('d0c44444-0000-4000-8000-000000000004', 'da444444-0000-4000-8000-000000000004', 'a4444444-4444-4000-8000-000000000004', 's3://kira-docs/ds_factory.xlsx','ds_factory.xlsx','xlsx', 'product_spec', 'b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90a1', '11111111-0000-4000-8000-000000000006'),
('d0c44444-0000-4000-8000-000000000044', 'da444444-0000-4000-8000-000000000004', 'a4444444-4444-4000-8000-000000000004', 's3://kira-docs/ds_process.pdf', 'ds_process.pdf', 'pdf',  'manufacturing_process_doc', 'd4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3', '11111111-0000-4000-8000-000000000006'),
('d0c55555-0000-4000-8000-000000000005', 'da555555-0000-4000-8000-000000000005', 'a5555555-5555-4000-8000-000000000005', 's3://kira-docs/gm_carbon.pdf',  'gm_carbon.pdf',  'pdf',  'carbon_footprint_declaration', 'c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2', '11111111-0000-4000-8000-000000000004');

INSERT INTO document_extraction_results (request_id, document_id, parsed_fields, confidence_map, unparsed_fields, supplier_confirmed, confirmed_at) VALUES
('da111111-0000-4000-8000-000000000001', 'd0c11111-0000-4000-8000-000000000001', '{"carbon_intensity":2.34,"energy_source":"renewable"}'::jsonb, '{"carbon_intensity":0.96,"energy_source":0.91}'::jsonb, '[]'::jsonb, TRUE, now() - interval '2 days'),
('da444444-0000-4000-8000-000000000004', 'd0c44444-0000-4000-8000-000000000004', '{"factory_name":"화성 공장","capacity":"2GWh"}'::jsonb, '{"factory_name":0.95,"capacity":0.62}'::jsonb, '["energy_source"]'::jsonb, FALSE, NULL),
('da555555-0000-4000-8000-000000000005', 'd0c55555-0000-4000-8000-000000000005', '{"carbon_intensity":18.7,"energy_source":"coal"}'::jsonb, '{"carbon_intensity":0.93,"energy_source":0.9}'::jsonb, '[]'::jsonb, TRUE, now() - interval '1 day');

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
-- ============================================================
-- TO-BE 확장 시드 (프로세스 정의서 반영)
-- ============================================================

-- 1) 다단계 결재선용 조직도(manager_id). 기존 role: admin(0001) / owner_esg(0002) / owner_purchasing(0003)
-- Admin(0001) = 최고 임원. owner_purchasing(0003) 상급자 → owner_esg(0002).
-- (002→008 결재선은 아래 SEED DELTA 블록에서 지정한다.)
UPDATE users SET manager_id = '11111111-0000-4000-8000-000000000002'
WHERE user_id = '11111111-0000-4000-8000-000000000003';

-- 2) Watchlist (UFLPA Entity List 예시). matched_supplier_id 로 실제 Sad path 공급사에 매칭.
--    'Global Mining Corp' → Xinjiang Nickel Refinery(acac…ac) 매칭 = 소급 강등 시연용.
--    'Xinjiang Mining Group' → 미매칭(NULL, 텍스트 후보만) = 자동대조 미스 케이스 시연.
INSERT INTO watchlists (watchlist_id, entity_name, country, reason, matched_supplier_id, source) VALUES
('a0000000-0000-4000-8000-000000000001', 'Global Mining Corp',     'CN', '신장 위구르 강제노동 의혹 제재 대상 (UFLPA Entity List)', 'acacacac-acac-4000-8000-0000000000ac', 'UFLPA_ENTITY_LIST'),
('a0000000-0000-4000-8000-000000000002', 'Xinjiang Mining Group',  'CN', '신장 지역 채굴 제재 대상',                              NULL,                                   'UFLPA_ENTITY_LIST');

-- 3) 실사 정책 문서 1건 (CSDDD 대응, active)
INSERT INTO due_diligence_policies (policy_id, title, version, status, document_url, created_by, published_at) VALUES
('d0000000-0000-4000-8000-000000000001', 'KIRA 공급망 실사 정책', 'v1.0', 'active', 's3://kira-documents/policies/dd_policy_v1.pdf', '11111111-0000-4000-8000-000000000002', now());


-- ===== SEED DELTA: 결재선용 부서장 추가 (02_seed_data.sql) =====
-- A 방향: role enum 변경 없음. 직책 계층(담당↔부서장)은 manager_id 로만 표현.
-- ESG 담당(002)이 컴플라이언스 보고서 기안 → ESG 부서장(008) 결재 → 끝. (2단계)

-- 1) ESG 부서장(008) 단건 INSERT (결재선 최상단, manager_id NULL).
--    001~007 은 위 라인 34 블록에서 이미 적재됨 — 재INSERT 시 PK 충돌이므로 008만 추가.
INSERT INTO users (user_id, tenant_id, email, password_hash, name, role, manager_id) VALUES
('11111111-0000-4000-8000-000000000008', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'esg.head@kira.demo',    '$2b$12$XO1O./JYL5VKDkodX2RdpOZSfFA7PSkeViaPqiOSQG4szW7fGVjf.', 'ESG Head',        'owner_esg',        NULL);

-- 2) ESG 담당(002)의 상급자를 ESG 부서장(008)으로 지정 (기안→부서장 결재 2단계).
UPDATE users SET manager_id = '11111111-0000-4000-8000-000000000008'
WHERE user_id = '11111111-0000-4000-8000-000000000002';
-- ============================================================
-- 제3자 정보제공 동의서 = 데이터 계약(Data Contract) — 한양셀 동의 완료 샘플
-- ============================================================
INSERT INTO data_provision_consents
  (supplier_id, tenant_id, data_scope, purpose, third_party_sharing, allowed_recipients, valid_from, valid_to, revocable,
   status, requested_at, returned_at, agreed_at, signer_name, signer_title, signer_email, signature_method, form_version, form_data, agreement_hash)
VALUES
  ('a1111111-1111-4000-8000-000000000001', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11',
   '["company","contacts","factories","carbon_epd","origin"]'::jsonb, 'EU_BATTERY', TRUE, '["BMW AG"]'::jsonb,
   '2026-01-01', '2027-12-31', TRUE, 'agreed', now() - interval '20 days', now() - interval '14 days', now() - interval '13 days',
   '김철수', 'ESG팀장', 'cs.kim@hanyangmfg.com', 'email_form', 'v1.0',
   '{"data_subject":"한양셀 제조(주)","sub_supplier_consent":true,"retention_years":7}'::jsonb, 'a3f5c9e1d2b4');

-- HITL 연동: 검토 필요 자료요청(da444444)을 gray_zone HITL 리뷰 batch에 연결(승인/반려가 hitl_reviews도 갱신).
UPDATE data_request_log SET batch_id='ba222222-0000-4000-8000-000000000002' WHERE request_id='da444444-0000-4000-8000-000000000004';


-- ============================================================
-- 16. Ingest 묶음 + 1~5차 협력사 계층 확장 (PM 요구 데이터셋)
-- ============================================================
-- [목표 개수] 위 섹션까지의 기존 데이터 대비 아래를 추가해 최종치를 맞춘다.
--   Ingest 묶음(bom_version): 기존 5 + 신규 6  = 11개
--   1차 협력사: 기존 3(한양셀·우진배터리·우진셀) + 신규 7 = 10개
--   2차 협력사: 기존 3(동성머티리얼·청정전구체·신장니켈제련) + 신규 6 = 9개
--   3차 협력사: 기존 2(한중제련·GlobalMining) + 신규 6 = 8개
--   4차 협력사: 기존 2(칠레리튬·Unverified Trader) + 신규 5 = 7개
--   5차 협력사: 기존 1(호주리튬) + 신규 5 = 6개
--   (차수 = 각 공급망 시나리오에서 해당 협력사가 등장하는 최소 hop_level)
--
-- [신규 제품 6종] BMW iX / Mercedes EQE / Hyundai IONIQ 6 / Hyundai IONIQ 5
--                / VW ID.4 / VW ID.7 — 각각 KIRA→1차→2차→3차→4차→5차 5-hop 체인
-- [Gray 시나리오 재현] VW ID.7(B10)은 4차에서 기존 Unverified Precursor Trading(ab)을
--   재사용하고 5차 없이 종료 — i4 시나리오와 동일한 '미확인 트레이더 = 추적 단절' 패턴.
-- [Dual-source 재현] BMW iX(B5)는 1차가 삼보배터리(주력) + 신성배터리(보조·미검증)
--   2곳으로 이중 소싱 — 1차 협력사 수를 6개가 아닌 7개로 맞추는 실제 업계 패턴.
-- ============================================================

-- ── 16-1. 신규 고객사 2개 ──────────────────────────────────────
INSERT INTO customers (customer_id, customer_code, customer_name, country, source_system, external_id) VALUES
('c0000000-0000-4000-8000-0000000000b3', 'HYUNDAI', 'Hyundai Motor Company', 'KR', 'ERP_PLM', 'ERP-CUST-HMC'),
('c0000000-0000-4000-8000-0000000000b4', 'VW',      'Volkswagen AG',         'DE', 'ERP_PLM', 'ERP-CUST-VWG');

-- ── 16-2. 신규 제품 6개 ────────────────────────────────────────
INSERT INTO products (product_id, product_code, product_name, manufacturer_id, tenant_id, customer_id, model_name, amperage_ah, type, source_system, external_id) VALUES
('d5555555-0000-4000-8000-000000000005', 'KE-PRI-NCM-100', 'KIRA PRiMX Prismatic NCM 100Ah', 'a0000000-0000-4000-8000-000000000000', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'c0000000-0000-4000-8000-0000000000b1', 'iX',      100.00, 'battery_pack', 'ERP_PLM', 'ERP-PROD-IX'),
('d6666666-0000-4000-8000-000000000006', 'KE-PRI-NCM-096', 'KIRA PRiMX Prismatic NCM 96Ah',  'a0000000-0000-4000-8000-000000000000', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'c0000000-0000-4000-8000-0000000000b2', 'EQE',      96.00, 'battery_pack', 'ERP_PLM', 'ERP-PROD-EQE'),
('d7777777-0000-4000-8000-000000000007', 'KE-CYL-NCM-095', 'KIRA PRiMX Cylindrical NCM 95Ah','a0000000-0000-4000-8000-000000000000', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'c0000000-0000-4000-8000-0000000000b3', 'IONIQ 6',  95.00, 'battery_pack', 'ERP_PLM', 'ERP-PROD-I6'),
('d8888888-0000-4000-8000-000000000008', 'KE-PRI-NCM-084', 'KIRA PRiMX Prismatic NCM 84Ah',  'a0000000-0000-4000-8000-000000000000', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'c0000000-0000-4000-8000-0000000000b3', 'IONIQ 5',  84.00, 'battery_pack', 'ERP_PLM', 'ERP-PROD-I5'),
('d9999999-0000-4000-8000-000000000009', 'KE-PRI-NCM-082', 'KIRA PRiMX Prismatic NCM 82Ah',  'a0000000-0000-4000-8000-000000000000', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'c0000000-0000-4000-8000-0000000000b4', 'ID.4',     82.00, 'battery_pack', 'ERP_PLM', 'ERP-PROD-ID4'),
('daaaaaaa-0000-4000-8000-00000000000a', 'KE-PRI-NCM-110', 'KIRA PRiMX Prismatic NCM 110Ah', 'a0000000-0000-4000-8000-000000000000', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'c0000000-0000-4000-8000-0000000000b4', 'ID.7',    110.00, 'battery_pack', 'ERP_PLM', 'ERP-PROD-ID7');

-- ── 16-3. 신규 BOM 버전 6개 (= Ingest 묶음 5→11) ─────────────────
INSERT INTO bom_versions (bom_version_id, product_id, version_number, production_from, production_to, status, source_system, external_id) VALUES
('e5555555-0000-4000-8000-000000000005', 'd5555555-0000-4000-8000-000000000005', 'Gen5-R1', '2024-07-01', NULL,         'active',    'ERP_PLM', 'ERP-BOM-IX'),
('e6666666-0000-4000-8000-000000000006', 'd6666666-0000-4000-8000-000000000006', 'Rev.A',   '2024-04-01', NULL,         'active',    'ERP_PLM', 'ERP-BOM-EQE'),
('e7777777-0000-4000-8000-000000000007', 'd7777777-0000-4000-8000-000000000007', 'v3.1',    '2024-01-01', '2024-12-31', 'deprecated','ERP_PLM', 'ERP-BOM-I6'),
('e8888888-0000-4000-8000-000000000008', 'd8888888-0000-4000-8000-000000000008', 'Rev.B',   '2024-03-01', NULL,         'active',    'ERP_PLM', 'ERP-BOM-I5'),
('e9999999-0000-4000-8000-000000000009', 'd9999999-0000-4000-8000-000000000009', 'v1.2',    '2024-06-01', '2024-12-31', 'deprecated','ERP_PLM', 'ERP-BOM-ID4'),
('eaaaaaaa-0000-4000-8000-00000000000a', 'daaaaaaa-0000-4000-8000-00000000000a', 'v1.0',    '2025-01-01', NULL,         'active',    'ERP_PLM', 'ERP-BOM-ID7');


-- ============================================================
-- 16-4. 1차 협력사 — 신규 7개 (기준정보: 회사명/사업자등록번호/주소/provider_type/
--        핵심광물+유해물질/서류 URL) + 공장 + PIC 3명 + 제조상세 + 탄소선언 + 리스크
-- ============================================================
INSERT INTO suppliers (supplier_id, tenant_id, company_name, company_name_en, company_name_ko, ceo_name, business_reg_no, provider_type, core_minerals, country, address, business_reg_doc_url, environmental_report_url, self_assessment_doc_url, completeness_score, status, risk_level) VALUES
('61111111-0000-4000-8000-000000000001', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '삼보배터리(주)', 'Sambo Battery Co.', '삼보배터리(주)', 'Park JH CEO', '111-86-11111', 'manufacturer', '{"Li":7.2,"Ni":80.0,"Co":10.0,"Mn":10.0,"hazardous_substances":["Pb","Cd"]}'::jsonb, 'KR', '경기도 평택시 포승읍 산업단지로 120', 's3://kira-docs/suppliers/61111111/biz_reg.pdf', 's3://kira-docs/suppliers/61111111/env_report.pdf', 's3://kira-docs/suppliers/61111111/self_assess.pdf', 88, 'supplier_verified', 'low'),
('61222222-0000-4000-8000-000000000002', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '하나에너지셀(주)', 'Hana Energy Cell Corp', '하나에너지셀(주)', 'Choi SY CEO', '222-86-11122', 'manufacturer', '{"Li":7.0,"Ni":80.0,"Co":10.0,"Mn":10.0}'::jsonb, 'KR', '충청북도 청주시 흥덕구 오송읍 오송산단로 55', 's3://kira-docs/suppliers/61222222/biz_reg.pdf', 's3://kira-docs/suppliers/61222222/env_report.pdf', 's3://kira-docs/suppliers/61222222/self_assess.pdf', 91, 'supplier_verified', 'low'),
('61333333-0000-4000-8000-000000000003', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '성진셀(주)', 'Sungjin Cell Co.', '성진셀(주)', 'Kim DH CEO', '333-86-11133', 'manufacturer', '{"Li":7.1,"Ni":79.0,"Co":11.0,"Mn":10.0}'::jsonb, 'KR', '경상남도 창원시 성산구 공단로 88', 's3://kira-docs/suppliers/61333333/biz_reg.pdf', 's3://kira-docs/suppliers/61333333/env_report.pdf', 's3://kira-docs/suppliers/61333333/self_assess.pdf', 85, 'supplier_verified', 'low'),
('61444444-0000-4000-8000-000000000004', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '동아셀(주)', 'Donga Cell Corp', '동아셀(주)', 'Lee SB CEO', '444-86-11144', 'manufacturer', '{"Li":7.0,"Ni":78.5,"Co":11.5,"Mn":10.0}'::jsonb, 'KR', '전라남도 광양시 광양읍 산단1로 200', 's3://kira-docs/suppliers/61444444/biz_reg.pdf', 's3://kira-docs/suppliers/61444444/env_report.pdf', NULL, 78, 'supplier_in_progress', 'low'),
('61555555-0000-4000-8000-000000000005', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '한독배터리(주)', 'Handok Battery Co.', '한독배터리(주)', 'Jung KW CEO', '555-86-11155', 'manufacturer', '{"Li":7.3,"Ni":80.0,"Co":10.0,"Mn":10.0}'::jsonb, 'KR', '경상북도 구미시 산동읍 구미국가산단로 350', 's3://kira-docs/suppliers/61555555/biz_reg.pdf', 's3://kira-docs/suppliers/61555555/env_report.pdf', 's3://kira-docs/suppliers/61555555/self_assess.pdf', 93, 'supplier_verified', 'low'),
('61666666-0000-4000-8000-000000000006', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '고려에너지(주)', 'Koryo Energy Corp', '고려에너지(주)', 'Han MJ CEO', '666-86-11166', 'manufacturer', '{"Li":7.0,"Ni":80.0,"Co":10.0,"Mn":10.0}'::jsonb, 'KR', '인천광역시 남동구 남동공단로 99', 's3://kira-docs/suppliers/61666666/biz_reg.pdf', 's3://kira-docs/suppliers/61666666/env_report.pdf', 's3://kira-docs/suppliers/61666666/self_assess.pdf', 87, 'supplier_verified', 'low'),
-- 신성배터리: BMW iX(B5) 이중소싱 보조 1차사 — 온보딩 초기(서류 미비) 상태로 재현
('61777777-0000-4000-8000-000000000007', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '신성배터리(주)', 'Shinsung Battery Co.', '신성배터리(주)', 'Oh JH CEO', '777-86-11177', 'manufacturer', NULL, 'KR', '경기도 화성시 향남읍 향남로 175', 's3://kira-docs/suppliers/61777777/biz_reg.pdf', NULL, NULL, 42, 'supplier_in_progress', 'low');

INSERT INTO supplier_factories (factory_id, supplier_id, factory_name, factory_name_en, country, region, location, factory_role, destination, applicable_regulations, supply_ratio_percent) VALUES
('71111111-0000-4000-8000-000000000001', '61111111-0000-4000-8000-000000000001', '평택 제1공장', 'Pyeongtaek Plant 1', 'KR', 'Pyeongtaek', ST_SetSRID(ST_MakePoint(126.996, 36.993), 4326), 'production', 'EU',   '["EU_BATTERY","EU_BATTERY_ART7","EU_BATTERY_ART47","CSDDD"]'::jsonb, 100.00),
('71222222-0000-4000-8000-000000000002', '61222222-0000-4000-8000-000000000002', '오송 셀공장',  'Osong Cell Plant',   'KR', 'Cheongju',  ST_SetSRID(ST_MakePoint(127.344, 36.634), 4326), 'production', 'EU',   '["EU_BATTERY","EU_BATTERY_ART7","CSDDD"]'::jsonb, 100.00),
('71333333-0000-4000-8000-000000000003', '61333333-0000-4000-8000-000000000003', '창원 원통형공장','Changwon Cylindrical Plant','KR', 'Changwon', ST_SetSRID(ST_MakePoint(128.681, 35.228), 4326), 'production', 'EU',   '["EU_BATTERY","EU_BATTERY_ART7","CSDDD"]'::jsonb, 100.00),
('71444444-0000-4000-8000-000000000004', '61444444-0000-4000-8000-000000000004', '광양 공장',   'Gwangyang Plant',    'KR', 'Gwangyang', ST_SetSRID(ST_MakePoint(127.694, 34.940), 4326), 'production', 'EU',   '["EU_BATTERY","EU_BATTERY_ART7"]'::jsonb, 100.00),
('71555555-0000-4000-8000-000000000005', '61555555-0000-4000-8000-000000000005', '구미 배터리공장','Gumi Battery Plant','KR', 'Gumi',     ST_SetSRID(ST_MakePoint(128.319, 36.119), 4326), 'production', 'BOTH', '["EU_BATTERY","EU_BATTERY_ART7","EU_BATTERY_ART47","CSDDD"]'::jsonb, 100.00),
('71666666-0000-4000-8000-000000000006', '61666666-0000-4000-8000-000000000006', '인천 공장',   'Incheon Plant',      'KR', 'Incheon',   ST_SetSRID(ST_MakePoint(126.727, 37.454), 4326), 'production', 'BOTH', '["EU_BATTERY","EU_BATTERY_ART7","CSDDD"]'::jsonb, 100.00),
('71777777-0000-4000-8000-000000000007', '61777777-0000-4000-8000-000000000007', '화성 공장',   'Hwaseong Plant',     'KR', 'Hwaseong',  ST_SetSRID(ST_MakePoint(126.831, 37.199), 4326), 'production', 'EU',   '["EU_BATTERY"]'::jsonb, 100.00);

-- PIC 3명씩 (신성배터리는 온보딩 초기라 1명만 — 실제 운영 상태 재현)
INSERT INTO supplier_contacts (supplier_id, factory_id, name, name_en, role, department, email, phone, is_primary, language) VALUES
('61111111-0000-4000-8000-000000000001', '71111111-0000-4000-8000-000000000001', '박지훈', 'Park JH', 'ESG Manager', 'ESG팀', 'jh.park@sambo.demo', '+82-31-111-0001', TRUE, 'ko'),
('61111111-0000-4000-8000-000000000001', '71111111-0000-4000-8000-000000000001', '김수영', 'Kim SY', 'Quality Manager', '품질관리팀', 'sy.kim@sambo.demo', '+82-31-111-0002', FALSE, 'ko'),
('61111111-0000-4000-8000-000000000001', '71111111-0000-4000-8000-000000000001', '이정민', 'Lee JM', 'Compliance Officer', '법무팀', 'jm.lee@sambo.demo', '+82-31-111-0003', FALSE, 'ko'),
('61222222-0000-4000-8000-000000000002', '71222222-0000-4000-8000-000000000002', '최선영', 'Choi SY', 'ESG Team Lead', 'ESG팀', 'sy.choi@hanaenergy.demo', '+82-43-222-0001', TRUE, 'ko'),
('61222222-0000-4000-8000-000000000002', '71222222-0000-4000-8000-000000000002', '오민준', 'Oh MJ', 'Plant Manager', '생산기술팀', 'mj.oh@hanaenergy.demo', '+82-43-222-0002', FALSE, 'ko'),
('61222222-0000-4000-8000-000000000002', '71222222-0000-4000-8000-000000000002', '신하은', 'Shin HE', 'Safety Manager', '안전환경팀', 'he.shin@hanaenergy.demo', '+82-43-222-0003', FALSE, 'ko'),
('61333333-0000-4000-8000-000000000003', '71333333-0000-4000-8000-000000000003', '김동현', 'Kim DH', 'ESG Officer', 'ESG팀', 'dh.kim@sungjincell.demo', '+82-55-333-0001', TRUE, 'ko'),
('61333333-0000-4000-8000-000000000003', '71333333-0000-4000-8000-000000000003', '정유진', 'Jung YJ', 'Purchasing Manager', '구매팀', 'yj.jung@sungjincell.demo', '+82-55-333-0002', FALSE, 'ko'),
('61333333-0000-4000-8000-000000000003', '71333333-0000-4000-8000-000000000003', '백승호', 'Baek SH', 'R&D Manager', '연구개발팀', 'sh.baek@sungjincell.demo', '+82-55-333-0003', FALSE, 'ko'),
('61444444-0000-4000-8000-000000000004', '71444444-0000-4000-8000-000000000004', '이상범', 'Lee SB', 'ESG Manager', 'ESG팀', 'sb.lee@dongacell.demo', '+82-61-444-0001', TRUE, 'ko'),
('61444444-0000-4000-8000-000000000004', '71444444-0000-4000-8000-000000000004', '강민지', 'Kang MJ', 'Quality Engineer', '품질팀', 'mj.kang@dongacell.demo', '+82-61-444-0002', FALSE, 'ko'),
('61444444-0000-4000-8000-000000000004', '71444444-0000-4000-8000-000000000004', '윤재혁', 'Yoon JH', 'Environmental Mgr', '환경팀', 'jh.yoon@dongacell.demo', '+82-61-444-0003', FALSE, 'ko'),
('61555555-0000-4000-8000-000000000005', '71555555-0000-4000-8000-000000000005', '정광우', 'Jung KW', 'ESG Director', 'ESG본부', 'kw.jung@handokbattery.demo', '+82-54-555-0001', TRUE, 'ko'),
('61555555-0000-4000-8000-000000000005', '71555555-0000-4000-8000-000000000005', 'Thomas Müller', 'Thomas Müller', 'QM Representative', 'QM부', 't.mueller@handokbattery.demo', '+82-54-555-0002', FALSE, 'en'),
('61555555-0000-4000-8000-000000000005', '71555555-0000-4000-8000-000000000005', '박혜진', 'Park HJ', 'Supply Chain Mgr', '공급망팀', 'hj.park@handokbattery.demo', '+82-54-555-0003', FALSE, 'ko'),
('61666666-0000-4000-8000-000000000006', '71666666-0000-4000-8000-000000000006', '한민지', 'Han MJ', 'ESG Team Lead', 'ESG팀', 'mj.han@koryoenergy.demo', '+82-32-666-0001', TRUE, 'ko'),
('61666666-0000-4000-8000-000000000006', '71666666-0000-4000-8000-000000000006', '송재원', 'Song JW', 'Compliance Mgr', '준법팀', 'jw.song@koryoenergy.demo', '+82-32-666-0002', FALSE, 'ko'),
('61666666-0000-4000-8000-000000000006', '71666666-0000-4000-8000-000000000006', '임수진', 'Lim SJ', 'Carbon Manager', '탄소중립팀', 'sj.lim@koryoenergy.demo', '+82-32-666-0003', FALSE, 'ko'),
('61777777-0000-4000-8000-000000000007', '71777777-0000-4000-8000-000000000007', '오준혁', 'Oh JH', 'ESG Officer', 'ESG팀', 'jh.oh@shinsungbattery.demo', '+82-31-777-0001', TRUE, 'ko');

INSERT INTO supplier_manufacturer_details (supplier_id, manufacturing_process, energy_source, capacity, carbon_intensity) VALUES
('61111111-0000-4000-8000-000000000001', 'NCM811 Prismatic Cell Assembly', 'renewable', '12GWh/yr', 2.1800),
('61222222-0000-4000-8000-000000000002', 'NCM811 Prismatic Cell Assembly', 'renewable', '9GWh/yr',  2.3100),
('61333333-0000-4000-8000-000000000003', 'NCM811 Cylindrical Cell Assembly', 'mixed',    '7GWh/yr',  2.8900),
('61444444-0000-4000-8000-000000000004', 'NCM Prismatic Cell Assembly',     'mixed',    '6GWh/yr',  3.0500),
('61555555-0000-4000-8000-000000000005', 'NCM811 Cell & Module Assembly',   'renewable','11GWh/yr', 2.2200),
('61666666-0000-4000-8000-000000000006', 'NCM811 Prismatic Cell Assembly',  'renewable','8GWh/yr',  2.4500);

INSERT INTO factory_carbon_declarations (factory_id, carbon_intensity, methodology, declared_at, valid_from, source) VALUES
('71111111-0000-4000-8000-000000000001', 2.1800, 'PEF', '2024-07-01', '2024-07-01', 'third_party_verified'),
('71222222-0000-4000-8000-000000000002', 2.3100, 'PEF', '2024-04-01', '2024-04-01', 'third_party_verified'),
('71333333-0000-4000-8000-000000000003', 2.8900, 'PEF', '2024-01-01', '2024-01-01', 'supplier_declared'),
('71555555-0000-4000-8000-000000000005', 2.2200, 'PEF', '2024-06-01', '2024-06-01', 'third_party_verified'),
('71666666-0000-4000-8000-000000000006', 2.4500, 'PEF', '2025-01-01', '2025-01-01', 'supplier_declared');
-- 71444444(동아셀)·71777777(신성배터리): 선언 미제출 — ART7 needs_human_review 갭 재현

INSERT INTO supplier_onboarding (supplier_id, consent_status, consent_signed_at, agreement_status, last_invited_at, sla_due_date, reminder_count) VALUES
('61111111-0000-4000-8000-000000000001', 'consent_agreed',  now() - interval '30 days', 'agreed',  now() - interval '31 days', now() - interval '17 days', 0),
('61222222-0000-4000-8000-000000000002', 'consent_agreed',  now() - interval '25 days', 'agreed',  now() - interval '26 days', now() - interval '12 days', 0),
('61333333-0000-4000-8000-000000000003', 'consent_agreed',  now() - interval '20 days', 'agreed',  now() - interval '21 days', now() - interval '7 days',  0),
('61444444-0000-4000-8000-000000000004', 'consent_agreed',  now() - interval '10 days', 'agreed',  now() - interval '11 days', now() + interval '3 days',  1),
('61555555-0000-4000-8000-000000000005', 'consent_agreed',  now() - interval '35 days', 'agreed',  now() - interval '36 days', now() - interval '22 days', 0),
('61666666-0000-4000-8000-000000000006', 'consent_agreed',  now() - interval '15 days', 'agreed',  now() - interval '16 days', now() - interval '2 days',  0),
('61777777-0000-4000-8000-000000000007', 'consent_pending', NULL,                        'pending', now() - interval '3 days',  now() + interval '11 days', 0);

INSERT INTO supplier_risk_profiles (supplier_id, overall_risk_score, risk_level, self_reported_risk_level, is_high_risk_flag, last_risk_review_at) VALUES
('61111111-0000-4000-8000-000000000001', 8,  'low', 'low', FALSE, now() - interval '5 days'),
('61222222-0000-4000-8000-000000000002', 10, 'low', 'low', FALSE, now() - interval '5 days'),
('61333333-0000-4000-8000-000000000003', 12, 'low', 'low', FALSE, now() - interval '5 days'),
('61444444-0000-4000-8000-000000000004', 25, 'low', 'low', FALSE, now() - interval '5 days'),
('61555555-0000-4000-8000-000000000005', 9,  'low', 'low', FALSE, now() - interval '5 days'),
('61666666-0000-4000-8000-000000000006', 11, 'low', 'low', FALSE, now() - interval '5 days'),
('61777777-0000-4000-8000-000000000007', 20, 'low', 'low', FALSE, now() - interval '5 days');


-- ============================================================
-- 16-5. 2차 협력사 — 신규 6개 (CAM 양극재 제조사)
-- ============================================================
INSERT INTO suppliers (supplier_id, tenant_id, company_name, company_name_en, ceo_name, business_reg_no, provider_type, core_minerals, country, address, business_reg_doc_url, environmental_report_url, completeness_score, status, risk_level) VALUES
('62111111-0000-4000-8000-000000000001', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '에코양극재(주)', 'Eco Cathode Materials', 'Yoon BK CEO', '611-86-20001', 'manufacturer', '{"Ni":55.0,"Co":8.0,"Mn":7.0}'::jsonb,   'KR', '충청남도 천안시 서북구 성환읍 성환산단로 30', 's3://kira-docs/suppliers/62111111/biz_reg.pdf', 's3://kira-docs/suppliers/62111111/env_report.pdf', 82, 'supplier_verified',    'low'),
('62222222-0000-4000-8000-000000000002', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '포스피케미칼(주)', 'PospiChem Co.', 'Shin TH CEO', '622-86-20002', 'manufacturer', '{"Ni":53.0,"Co":8.5,"Mn":8.5}'::jsonb, 'KR', '경상북도 포항시 남구 오천읍 포항산단4로 10', 's3://kira-docs/suppliers/62222222/biz_reg.pdf', 's3://kira-docs/suppliers/62222222/env_report.pdf', 79, 'supplier_verified',    'low'),
('62333333-0000-4000-8000-000000000003', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '신진CAM(주)', 'Sinjin CAM Co.', 'Kwak MS CEO', '633-86-20003', 'manufacturer', '{"Ni":54.0,"Co":9.0,"Mn":8.0}'::jsonb,      'KR', '전라북도 군산시 소룡동 군산국가산단로 88', NULL,                                              NULL,                                              68, 'supplier_in_progress', 'low'),
('62444444-0000-4000-8000-000000000004', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '한라소재(주)', 'Halla Materials Co.', 'Kwon YS CEO', '644-86-20004', 'manufacturer', '{"Ni":52.0,"Co":9.5,"Mn":8.5}'::jsonb,   'KR', '경상남도 거제시 장승포동 거제산단로 15', 's3://kira-docs/suppliers/62444444/biz_reg.pdf', 's3://kira-docs/suppliers/62444444/env_report.pdf', 75, 'supplier_verified',    'low'),
('62555555-0000-4000-8000-000000000005', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '대한CAM(주)', 'Daehan CAM Co.', 'Baek JW CEO', '655-86-20005', 'manufacturer', '{"Ni":55.5,"Co":8.0,"Mn":6.5}'::jsonb,    'KR', '울산광역시 남구 매암동 울산산단로 210', 's3://kira-docs/suppliers/62555555/biz_reg.pdf', 's3://kira-docs/suppliers/62555555/env_report.pdf', 80, 'supplier_verified',    'low'),
('62666666-0000-4000-8000-000000000006', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '청우CAM(주)', 'Cheongwoo CAM Co.', 'Nam HJ CEO', '666-86-20006', 'manufacturer', '{"Ni":53.5,"Co":8.8,"Mn":7.7}'::jsonb,   'KR', '경기도 이천시 부발읍 이천산단로 44', NULL,                                              NULL,                                              60, 'supplier_in_progress', 'low');

INSERT INTO supplier_factories (factory_id, supplier_id, factory_name, factory_name_en, country, region, location, factory_role, destination, applicable_regulations, supply_ratio_percent) VALUES
('72111111-0000-4000-8000-000000000001', '62111111-0000-4000-8000-000000000001', '천안 양극재공장', 'Cheonan CAM Plant', 'KR', 'Cheonan', ST_SetSRID(ST_MakePoint(127.100, 36.810), 4326), 'processing', 'BOTH', '["EU_BATTERY","CRMA","EU_BATTERY_ART7"]'::jsonb, 100.00),
('72222222-0000-4000-8000-000000000002', '62222222-0000-4000-8000-000000000002', '포항 CAM공장',  'Pohang CAM Plant',  'KR', 'Pohang',  ST_SetSRID(ST_MakePoint(129.380, 36.010), 4326), 'processing', 'BOTH', '["EU_BATTERY","CRMA"]'::jsonb, 100.00),
('72333333-0000-4000-8000-000000000003', '62333333-0000-4000-8000-000000000003', '군산 CAM공장',  'Gunsan CAM Plant',  'KR', 'Gunsan',  ST_SetSRID(ST_MakePoint(126.711, 35.967), 4326), 'processing', 'EU',   '["EU_BATTERY"]'::jsonb, 100.00),
('72444444-0000-4000-8000-000000000004', '62444444-0000-4000-8000-000000000004', '거제 소재공장', 'Geoje Materials Plant','KR', 'Geoje', ST_SetSRID(ST_MakePoint(128.621, 34.879), 4326), 'processing', 'EU',   '["EU_BATTERY","CRMA"]'::jsonb, 100.00),
('72555555-0000-4000-8000-000000000005', '62555555-0000-4000-8000-000000000005', '울산 CAM공장',  'Ulsan CAM Plant',   'KR', 'Ulsan',   ST_SetSRID(ST_MakePoint(129.365, 35.520), 4326), 'processing', 'BOTH', '["EU_BATTERY","CRMA","EU_BATTERY_ART7"]'::jsonb, 100.00),
('72666666-0000-4000-8000-000000000006', '62666666-0000-4000-8000-000000000006', '이천 CAM공장',  'Icheon CAM Plant',  'KR', 'Icheon',  ST_SetSRID(ST_MakePoint(127.505, 37.246), 4326), 'processing', 'EU',   '["EU_BATTERY"]'::jsonb, 100.00);

INSERT INTO supplier_contacts (supplier_id, factory_id, name, name_en, role, department, email, phone, is_primary, language) VALUES
('62111111-0000-4000-8000-000000000001', '72111111-0000-4000-8000-000000000001', '윤병기', 'Yoon BK', 'ESG Manager', 'ESG팀', 'bk.yoon@ecocathode.demo', '+82-41-111-2001', TRUE, 'ko'),
('62222222-0000-4000-8000-000000000002', '72222222-0000-4000-8000-000000000002', '신태현', 'Shin TH', 'ESG Lead',    'ESG팀', 'th.shin@pospichem.demo',  '+82-54-222-2002', TRUE, 'ko'),
('62333333-0000-4000-8000-000000000003', '72333333-0000-4000-8000-000000000003', '곽민석', 'Kwak MS', 'ESG Officer', 'ESG팀', 'ms.kwak@sinjincam.demo',  '+82-63-333-2003', TRUE, 'ko'),
('62444444-0000-4000-8000-000000000004', '72444444-0000-4000-8000-000000000004', '권영수', 'Kwon YS', 'ESG Manager', 'ESG팀', 'ys.kwon@hallamat.demo',   '+82-55-444-2004', TRUE, 'ko'),
('62555555-0000-4000-8000-000000000005', '72555555-0000-4000-8000-000000000005', '백준우', 'Baek JW', 'ESG Manager', 'ESG팀', 'jw.baek@daehancam.demo',  '+82-52-555-2005', TRUE, 'ko'),
('62666666-0000-4000-8000-000000000006', '72666666-0000-4000-8000-000000000006', '남효진', 'Nam HJ',  'ESG Officer', 'ESG팀', 'hj.nam@cheongwoocam.demo','+82-31-666-2006', TRUE, 'ko');

INSERT INTO factory_carbon_declarations (factory_id, carbon_intensity, methodology, declared_at, valid_from, source) VALUES
('72111111-0000-4000-8000-000000000001', 3.0500, 'PEF', '2024-01-01', '2024-01-01', 'supplier_declared'),
('72222222-0000-4000-8000-000000000002', 3.2100, 'PEF', '2024-01-01', '2024-01-01', 'supplier_declared'),
('72444444-0000-4000-8000-000000000004', 3.1200, 'PEF', '2024-01-01', '2024-01-01', 'supplier_declared'),
('72555555-0000-4000-8000-000000000005', 2.9500, 'PEF', '2024-01-01', '2024-01-01', 'supplier_declared');
-- 72333333(신진CAM)·72666666(청우CAM): 선언 미제출 갭

INSERT INTO supplier_risk_profiles (supplier_id, overall_risk_score, risk_level, self_reported_risk_level, is_high_risk_flag, last_risk_review_at) VALUES
('62111111-0000-4000-8000-000000000001', 14, 'low', 'low', FALSE, now() - interval '7 days'),
('62222222-0000-4000-8000-000000000002', 18, 'low', 'low', FALSE, now() - interval '7 days'),
('62333333-0000-4000-8000-000000000003', 22, 'low', 'low', FALSE, now() - interval '7 days'),
('62444444-0000-4000-8000-000000000004', 19, 'low', 'low', FALSE, now() - interval '7 days'),
('62555555-0000-4000-8000-000000000005', 15, 'low', 'low', FALSE, now() - interval '7 days'),
('62666666-0000-4000-8000-000000000006', 24, 'low', 'low', FALSE, now() - interval '7 days');


-- ============================================================
-- 16-6. 3차 협력사 — 신규 6개 (전구체 제조사)
-- ============================================================
INSERT INTO suppliers (supplier_id, tenant_id, company_name, company_name_en, ceo_name, business_reg_no, provider_type, core_minerals, country, address, business_reg_doc_url, environmental_report_url, completeness_score, status, risk_level) VALUES
('63111111-0000-4000-8000-000000000001', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '동신전구체(주)', 'Dongsin Precursor Corp', 'Bae CW CEO', '611-86-30001', 'manufacturer', '{"Ni":50.0,"Co":10.0,"Mn":10.0}'::jsonb, 'KR', '전라북도 군산시 소룡동 군산국가산단로 90', 's3://kira-docs/suppliers/63111111/biz_reg.pdf', 's3://kira-docs/suppliers/63111111/env_report.pdf', 74, 'supplier_verified',    'low'),
('63222222-0000-4000-8000-000000000002', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '대성전구체(주)', 'Daesung Precursor Materials', 'Han SK CEO', '622-86-30002', 'manufacturer', '{"Ni":48.0,"Co":11.0,"Mn":11.0}'::jsonb, 'KR', '경기도 화성시 정남면 화성산단로 60', NULL, NULL, 55, 'supplier_review', 'medium'),
('63333333-0000-4000-8000-000000000003', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '한중정밀화학(주)', 'Hanjung Precision Chemical', 'Ma YL CEO', '633-86-30003', 'manufacturer', '{"Ni":51.0,"Co":9.5,"Mn":9.5}'::jsonb, 'KR', '경상북도 포항시 남구 포항산단로 200', 's3://kira-docs/suppliers/63333333/biz_reg.pdf', 's3://kira-docs/suppliers/63333333/env_report.pdf', 76, 'supplier_verified', 'low'),
('63444444-0000-4000-8000-000000000004', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '세종프리커서(주)', 'Sejong Precursor Co.', 'Cho HY CEO', '644-86-30004', 'manufacturer', '{"Ni":49.5,"Co":10.5,"Mn":10.0}'::jsonb, 'KR', '세종특별자치시 소정면 세종산단로 15', 's3://kira-docs/suppliers/63444444/biz_reg.pdf', 's3://kira-docs/suppliers/63444444/env_report.pdf', 71, 'supplier_verified', 'low'),
('63555555-0000-4000-8000-000000000005', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '남해케미칼(주)', 'Namhae Chemical Co.', 'Yang JS CEO', '655-86-30005', 'manufacturer', '{"Ni":50.5,"Co":9.8,"Mn":9.7}'::jsonb, 'KR', '경상남도 남해군 서면 남해산단로 8', NULL, NULL, 58, 'supplier_in_progress', 'low'),
('63666666-0000-4000-8000-000000000006', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '은성정밀소재(주)', 'Eunsung Precision Materials', 'Ko DW CEO', '666-86-30006', 'manufacturer', '{"Ni":52.5,"Co":9.0,"Mn":8.5}'::jsonb, 'KR', '충청남도 아산시 인주면 아산산단로 33', 's3://kira-docs/suppliers/63666666/biz_reg.pdf', 's3://kira-docs/suppliers/63666666/env_report.pdf', 73, 'supplier_verified', 'low');

INSERT INTO supplier_factories (factory_id, supplier_id, factory_name, factory_name_en, country, region, location, factory_role, destination, applicable_regulations, supply_ratio_percent) VALUES
('73111111-0000-4000-8000-000000000001', '63111111-0000-4000-8000-000000000001', '군산 전구체공장', 'Gunsan Precursor Plant', 'KR', 'Gunsan',   ST_SetSRID(ST_MakePoint(126.715, 35.965), 4326), 'processing', 'EU',   '["EU_BATTERY","CRMA"]'::jsonb, 100.00),
('73222222-0000-4000-8000-000000000002', '63222222-0000-4000-8000-000000000002', '화성 전구체공장', 'Hwaseong Precursor Plant', 'KR', 'Hwaseong', ST_SetSRID(ST_MakePoint(126.850, 37.170), 4326), 'processing', 'EU',   '["EU_BATTERY"]'::jsonb, 100.00),
('73333333-0000-4000-8000-000000000003', '63333333-0000-4000-8000-000000000003', '포항 정밀화학공장', 'Pohang Precision Chem Plant', 'KR', 'Pohang', ST_SetSRID(ST_MakePoint(129.375, 36.005), 4326), 'processing', 'BOTH', '["EU_BATTERY","CRMA"]'::jsonb, 100.00),
('73444444-0000-4000-8000-000000000004', '63444444-0000-4000-8000-000000000004', '세종 전구체공장', 'Sejong Precursor Plant', 'KR', 'Sejong',   ST_SetSRID(ST_MakePoint(127.290, 36.480), 4326), 'processing', 'EU',   '["EU_BATTERY"]'::jsonb, 100.00),
('73555555-0000-4000-8000-000000000005', '63555555-0000-4000-8000-000000000005', '남해 케미칼공장', 'Namhae Chemical Plant', 'KR', 'Namhae',   ST_SetSRID(ST_MakePoint(127.865, 34.870), 4326), 'processing', 'EU',   '["EU_BATTERY"]'::jsonb, 100.00),
('73666666-0000-4000-8000-000000000006', '63666666-0000-4000-8000-000000000006', '아산 정밀소재공장', 'Asan Precision Materials Plant', 'KR', 'Asan', ST_SetSRID(ST_MakePoint(126.960, 36.780), 4326), 'processing', 'BOTH', '["EU_BATTERY","CRMA"]'::jsonb, 100.00);

INSERT INTO supplier_contacts (supplier_id, factory_id, name, name_en, role, department, email, phone, is_primary, language) VALUES
('63111111-0000-4000-8000-000000000001', '73111111-0000-4000-8000-000000000001', '배창우', 'Bae CW', 'ESG Officer', 'ESG팀', 'cw.bae@dongsinpre.demo',  '+82-63-111-3001', TRUE, 'ko'),
('63222222-0000-4000-8000-000000000002', '73222222-0000-4000-8000-000000000002', '한상국', 'Han SK', 'ESG Officer', 'ESG팀', 'sk.han@daesungpre.demo',  '+82-31-222-3002', TRUE, 'ko'),
('63333333-0000-4000-8000-000000000003', '73333333-0000-4000-8000-000000000003', '마영림', 'Ma YL',  'ESG Officer', 'ESG팀', 'yl.ma@hanjungchem.demo',  '+82-54-333-3003', TRUE, 'ko'),
('63444444-0000-4000-8000-000000000004', '73444444-0000-4000-8000-000000000004', '조현영', 'Cho HY', 'ESG Manager', 'ESG팀', 'hy.cho@sejongpre.demo',   '+82-44-444-3004', TRUE, 'ko'),
('63555555-0000-4000-8000-000000000005', '73555555-0000-4000-8000-000000000005', '양지수', 'Yang JS','ESG Officer', 'ESG팀', 'js.yang@namhaechem.demo', '+82-55-555-3005', TRUE, 'ko'),
('63666666-0000-4000-8000-000000000006', '73666666-0000-4000-8000-000000000006', '고동욱', 'Ko DW',  'ESG Manager', 'ESG팀', 'dw.ko@eunsungmat.demo',   '+82-41-666-3006', TRUE, 'ko');

INSERT INTO supplier_risk_profiles (supplier_id, overall_risk_score, risk_level, self_reported_risk_level, is_high_risk_flag, last_risk_review_at) VALUES
('63111111-0000-4000-8000-000000000001', 16, 'low',    'low', FALSE, now() - interval '7 days'),
('63222222-0000-4000-8000-000000000002', 38, 'medium', 'low', FALSE, now() - interval '7 days'),
('63333333-0000-4000-8000-000000000003', 17, 'low',    'low', FALSE, now() - interval '7 days'),
('63444444-0000-4000-8000-000000000004', 19, 'low',    'low', FALSE, now() - interval '7 days'),
('63555555-0000-4000-8000-000000000005', 26, 'medium', 'low', FALSE, now() - interval '7 days'),
('63666666-0000-4000-8000-000000000006', 18, 'low',    'low', FALSE, now() - interval '7 days');


-- ============================================================
-- 16-7. 4차 협력사 — 신규 5개 (제련/정제, smelter)
-- ============================================================
INSERT INTO suppliers (supplier_id, tenant_id, company_name, company_name_en, ceo_name, provider_type, core_minerals, country, address, business_reg_doc_url, environmental_report_url, completeness_score, status, risk_level) VALUES
('64111111-0000-4000-8000-000000000001', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '고려제련(주)', 'Koryo Smelting Corp', 'Jang YB CEO', 'smelter', '{"Ni":99.5,"Co":99.8}'::jsonb, 'KR', '경상남도 울산시 울주군 온산읍 온산공단로 55', 's3://kira-docs/suppliers/64111111/biz_reg.pdf', NULL, 80, 'supplier_verified', 'low'),
('64222222-0000-4000-8000-000000000002', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'PT Indosel Nickel Refinery', 'PT Indosel Nickel Refinery', 'Budi Hartono CEO', 'smelter', '{"Ni":99.2}'::jsonb, 'ID', 'Sulawesi Tengah, Morowali Industrial Park', 's3://kira-docs/suppliers/64222222/biz_reg.pdf', NULL, 55, 'supplier_review', 'medium'),
('64333333-0000-4000-8000-000000000003', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'AusRef Processing Pty Ltd', 'AusRef Processing Pty Ltd', 'James Wilson CEO', 'smelter', '{"Ni":99.7}'::jsonb, 'AU', 'Western Australia, Kwinana Industrial Area', 's3://kira-docs/suppliers/64333333/biz_reg.pdf', 's3://kira-docs/suppliers/64333333/env_report.pdf', 78, 'supplier_verified', 'low'),
('64444444-0000-4000-8000-000000000004', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'Zambia Copper & Cobalt Refinery', 'Zambia Copper & Cobalt Refinery', 'Emmanuel Banda CEO', 'smelter', '{"Co":85.0,"Ni":10.0}'::jsonb, 'ZM', 'Copperbelt Province, Kitwe Industrial Area', 's3://kira-docs/suppliers/64444444/biz_reg.pdf', NULL, 50, 'supplier_review', 'medium'),
('64555555-0000-4000-8000-000000000005', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'Brazil Nickel Refining SA', 'Brazil Nickel Refining SA', 'Carlos Silva CEO', 'smelter', '{"Ni":99.4}'::jsonb, 'BR', 'Goiás State, Niquelândia Industrial Zone', 's3://kira-docs/suppliers/64555555/biz_reg.pdf', 's3://kira-docs/suppliers/64555555/env_report.pdf', 77, 'supplier_verified', 'low');

UPDATE suppliers SET smelter_type = 'rmi'     WHERE supplier_id IN ('64111111-0000-4000-8000-000000000001', '64333333-0000-4000-8000-000000000003', '64555555-0000-4000-8000-000000000005');
UPDATE suppliers SET smelter_type = 'private' WHERE supplier_id IN ('64222222-0000-4000-8000-000000000002', '64444444-0000-4000-8000-000000000004');

INSERT INTO supplier_factories (factory_id, supplier_id, factory_name, factory_name_en, country, region, location, factory_role, destination, applicable_regulations, supply_ratio_percent) VALUES
('74111111-0000-4000-8000-000000000001', '64111111-0000-4000-8000-000000000001', '온산 제련소 2호',   'Onsan Smelter No.2', 'KR', 'Onsan',    ST_SetSRID(ST_MakePoint(129.350, 35.430), 4326), 'processing', 'BOTH', '["CRMA","EU_BATTERY"]'::jsonb, 100.00),
('74222222-0000-4000-8000-000000000002', '64222222-0000-4000-8000-000000000002', 'Morowali Refinery', 'Morowali Refinery',   'ID', 'Sulawesi', ST_SetSRID(ST_MakePoint(121.640, -2.010), 4326), 'processing', 'BOTH', '["CRMA","CONFLICT_MINERALS","EUDR"]'::jsonb, 100.00),
('74333333-0000-4000-8000-000000000003', '64333333-0000-4000-8000-000000000003', 'Kwinana Ni Refinery','Kwinana Ni Refinery', 'AU', 'Western Australia', ST_SetSRID(ST_MakePoint(115.770, -32.230), 4326), 'processing', 'BOTH', '["CRMA"]'::jsonb, 100.00),
('74444444-0000-4000-8000-000000000004', '64444444-0000-4000-8000-000000000004', 'Kitwe Refinery',    'Kitwe Refinery',      'ZM', 'Copperbelt', ST_SetSRID(ST_MakePoint(28.213, -12.818), 4326), 'processing', 'BOTH', '["CONFLICT_MINERALS","CRMA"]'::jsonb, 100.00),
('74555555-0000-4000-8000-000000000005', '64555555-0000-4000-8000-000000000005', 'Niquelândia Refinery','Niquelandia Refinery','BR', 'Goias',   ST_SetSRID(ST_MakePoint(-48.460, -14.470), 4326), 'processing', 'BOTH', '["CRMA"]'::jsonb, 100.00);

INSERT INTO supplier_contacts (supplier_id, factory_id, name, name_en, role, department, email, phone, is_primary, language) VALUES
('64111111-0000-4000-8000-000000000001', '74111111-0000-4000-8000-000000000001', '장영범', '장영범', 'Compliance Manager', 'Compliance', 'jang@koryosmelt.demo', '+82-52-111-4001',  TRUE, 'ko'),
('64222222-0000-4000-8000-000000000002', '74222222-0000-4000-8000-000000000002', 'Budi Santoso', 'Budi Santoso', 'Compliance Manager', 'Compliance', 'budi@indosel.demo', '+62-21-222-4002', TRUE, 'en'),
('64333333-0000-4000-8000-000000000003', '74333333-0000-4000-8000-000000000003', 'James Wilson', 'James Wilson', 'ESG Manager', 'ESG', 'j.wilson@ausref.demo', '+61-8-333-4003',       TRUE, 'en'),
('64444444-0000-4000-8000-000000000004', '74444444-0000-4000-8000-000000000004', 'Emmanuel Banda', 'Emmanuel Banda', 'Compliance Manager', 'Compliance', 'e.banda@zamcopper.demo', '+260-212-444-4004', TRUE, 'en'),
('64555555-0000-4000-8000-000000000005', '74555555-0000-4000-8000-000000000005', 'Carlos Silva', 'Carlos Silva', 'ESG Manager', 'ESG', 'c.silva@brnickel.demo', '+55-62-555-4005',     TRUE, 'en');

INSERT INTO supplier_risk_profiles (supplier_id, overall_risk_score, risk_level, self_reported_risk_level, is_high_risk_flag, high_risk_reasons, last_risk_review_at) VALUES
('64111111-0000-4000-8000-000000000001', 12, 'low',    'low', FALSE, NULL, now() - interval '7 days'),
('64222222-0000-4000-8000-000000000002', 45, 'medium', 'low', TRUE,  '["인도네시아 니켈광 인접 산림훼손 리스크(EUDR)"]'::jsonb, now() - interval '7 days'),
('64333333-0000-4000-8000-000000000003', 10, 'low',    'low', FALSE, NULL, now() - interval '7 days'),
('64444444-0000-4000-8000-000000000004', 48, 'medium', 'low', TRUE,  '["잠비아 코퍼벨트 — DRC 인접 분쟁광물 리스크"]'::jsonb, now() - interval '7 days'),
('64555555-0000-4000-8000-000000000005', 11, 'low',    'low', FALSE, NULL, now() - interval '7 days');


-- ============================================================
-- 16-8. 5차 협력사 — 신규 5개 (광산, miner)
-- ============================================================
INSERT INTO suppliers (supplier_id, tenant_id, company_name, company_name_en, ceo_name, provider_type, country, address, business_reg_doc_url, completeness_score, status, risk_level) VALUES
('65111111-0000-4000-8000-000000000001', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'Sulawesi Nickel Mining Corp', 'Sulawesi Nickel Mining Corp', 'Andi Wijaya CEO', 'miner', 'ID', 'Sulawesi Tengah, Morowali Mining District', NULL, 40, 'supplier_in_progress', 'medium'),
('65222222-0000-4000-8000-000000000002', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'Weda Bay Nickel Mine', 'Weda Bay Nickel Mine', 'Liu Wei CEO', 'miner', 'ID', 'North Maluku, Weda Bay Industrial Park', NULL, 35, 'supplier_review', 'medium'),
('65333333-0000-4000-8000-000000000003', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'Western Australia Nickel Mine Pty Ltd', 'Western Australia Nickel Mine Pty Ltd', 'Sarah Thompson CEO', 'miner', 'AU', 'Western Australia, Kalgoorlie Mining District', 's3://kira-docs/suppliers/65333333/biz_reg.pdf', 68, 'supplier_verified', 'low'),
('65444444-0000-4000-8000-000000000004', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'Zambia Copperbelt Cobalt Mine', 'Zambia Copperbelt Cobalt Mine', 'Joseph Mwansa CEO', 'miner', 'ZM', 'Copperbelt Province, Chililabombwe Mining Zone', NULL, 30, 'supplier_in_progress', 'medium'),
('65555555-0000-4000-8000-000000000005', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'Brazil Nickel Laterite Mine', 'Brazil Nickel Laterite Mine', 'Paulo Almeida CEO', 'miner', 'BR', 'Goiás State, Niquelândia Mining District', 's3://kira-docs/suppliers/65555555/biz_reg.pdf', 65, 'supplier_verified', 'low');

INSERT INTO supplier_factories (factory_id, supplier_id, factory_name, factory_name_en, country, region, location, factory_role, destination, applicable_regulations, supply_ratio_percent) VALUES
('75111111-0000-4000-8000-000000000001', '65111111-0000-4000-8000-000000000001', 'Sulawesi Nickel Mine',      'Sulawesi Nickel Mine',      'ID', 'Sulawesi',   ST_SetSRID(ST_MakePoint(121.700, -1.950), 4326),  'mining', 'BOTH', '["CRMA","CONFLICT_MINERALS","EUDR"]'::jsonb, 100.00),
('75222222-0000-4000-8000-000000000002', '65222222-0000-4000-8000-000000000002', 'Weda Bay Mine',             'Weda Bay Mine',             'ID', 'Halmahera',  ST_SetSRID(ST_MakePoint(127.900, -0.420), 4326),  'mining', 'BOTH', '["CRMA","EUDR"]'::jsonb, 100.00),
('75333333-0000-4000-8000-000000000003', '65333333-0000-4000-8000-000000000003', 'Kalgoorlie Nickel Mine',    'Kalgoorlie Nickel Mine',    'AU', 'Western Australia', ST_SetSRID(ST_MakePoint(121.470, -30.750), 4326), 'mining', 'BOTH', '["CRMA"]'::jsonb, 100.00),
('75444444-0000-4000-8000-000000000004', '65444444-0000-4000-8000-000000000004', 'Chililabombwe Cobalt Mine', 'Chililabombwe Cobalt Mine', 'ZM', 'Copperbelt', ST_SetSRID(ST_MakePoint(27.827, -12.368), 4326),  'mining', 'BOTH', '["CONFLICT_MINERALS","CRMA"]'::jsonb, 100.00),
('75555555-0000-4000-8000-000000000005', '65555555-0000-4000-8000-000000000005', 'Niquelândia Laterite Mine', 'Niquelandia Laterite Mine', 'BR', 'Goias',      ST_SetSRID(ST_MakePoint(-48.480, -14.500), 4326), 'mining', 'BOTH', '["CRMA"]'::jsonb, 100.00);

INSERT INTO supplier_miner_details (supplier_id, mine_name, mining_method, extraction_volume, mine_coordinates, active_period_from) VALUES
('65111111-0000-4000-8000-000000000001', 'Sulawesi Nickel Mine A',    'open_pit', 45000.00, ST_SetSRID(ST_MakePoint(121.700, -1.950), 4326),  '2020-01-01'),
('65222222-0000-4000-8000-000000000002', 'Weda Bay Nickel Mine A',    'open_pit', 60000.00, ST_SetSRID(ST_MakePoint(127.900, -0.420), 4326),  '2019-01-01'),
('65333333-0000-4000-8000-000000000003', 'Kalgoorlie Nickel Mine',    'open_pit', 38000.00, ST_SetSRID(ST_MakePoint(121.470, -30.750), 4326), '2017-01-01'),
('65444444-0000-4000-8000-000000000004', 'Chililabombwe Cobalt Mine', 'open_pit', 20000.00, ST_SetSRID(ST_MakePoint(27.827, -12.368), 4326),  '2018-01-01'),
('65555555-0000-4000-8000-000000000005', 'Niquelândia Laterite Mine', 'open_pit', 42000.00, ST_SetSRID(ST_MakePoint(-48.480, -14.500), 4326), '2016-01-01');

INSERT INTO supplier_contacts (supplier_id, factory_id, name, name_en, role, department, email, phone, is_primary, language) VALUES
('65111111-0000-4000-8000-000000000001', '75111111-0000-4000-8000-000000000001', 'Andi Wijaya',    'Andi Wijaya',    'Mine Manager', 'Operations', 'a.wijaya@sulawesinickel.demo', '+62-451-111-5001', TRUE, 'en'),
('65222222-0000-4000-8000-000000000002', '75222222-0000-4000-8000-000000000002', 'Liu Wei',        'Liu Wei',        'Mine Manager', 'Operations', 'l.wei@wedabay.demo',           '+62-921-222-5002', TRUE, 'en'),
('65333333-0000-4000-8000-000000000003', '75333333-0000-4000-8000-000000000003', 'Sarah Thompson', 'Sarah Thompson', 'Mine Manager', 'Operations', 's.thompson@wamine.demo',       '+61-8-333-5003',   TRUE, 'en'),
('65444444-0000-4000-8000-000000000004', '75444444-0000-4000-8000-000000000004', 'Joseph Mwansa',  'Joseph Mwansa',  'Mine Manager', 'Operations', 'j.mwansa@zamcobaltmine.demo',  '+260-212-444-5004',TRUE, 'en'),
('65555555-0000-4000-8000-000000000005', '75555555-0000-4000-8000-000000000005', 'Paulo Almeida',  'Paulo Almeida',  'Mine Manager', 'Operations', 'p.almeida@brnickelmine.demo',  '+55-62-555-5005',  TRUE, 'en');

INSERT INTO supplier_risk_profiles (supplier_id, overall_risk_score, risk_level, self_reported_risk_level, is_high_risk_flag, high_risk_reasons, last_risk_review_at) VALUES
('65111111-0000-4000-8000-000000000001', 42, 'medium', 'low', TRUE,  '["산림훼손 인접 채굴 지역(EUDR)"]'::jsonb, now() - interval '7 days'),
('65222222-0000-4000-8000-000000000002', 44, 'medium', 'low', TRUE,  '["산림훼손 인접 채굴 지역(EUDR)"]'::jsonb, now() - interval '7 days'),
('65333333-0000-4000-8000-000000000003', 10, 'low',    'low', FALSE, NULL, now() - interval '7 days'),
('65444444-0000-4000-8000-000000000004', 46, 'medium', 'low', TRUE,  '["DRC 접경 분쟁광물 리스크 지역"]'::jsonb, now() - interval '7 days'),
('65555555-0000-4000-8000-000000000005', 12, 'low',    'low', FALSE, NULL, now() - interval '7 days');


-- ============================================================
-- 16-9. 공급망 맵 엣지 — 신규 6개 제품 (hop0~hop5, part_id는 기존
--        NCM811 트리 재사용: Module→CAM→PRE→REF-NI→MIN-NI)
-- ============================================================

-- ⑤ BMW iX [Happy + Dual-source] — 1차: 삼보배터리(주력)+신성배터리(보조,미검증)
INSERT INTO supply_chain_map (edge_id, bom_version_id, parent_supplier_id, child_supplier_id, part_id, hop_level, link_status, source_system, verification_status, supply_period_from, supply_period_to) VALUES
('85555555-0000-4000-8000-000000000001', 'e5555555-0000-4000-8000-000000000005', NULL,                                     'a0000000-0000-4000-8000-000000000000', 'b1111111-0000-4000-8000-000000000001', 0, 'supplychain_confirmed', 'ERP',              'verified',   '2024-07-01', '2025-06-30'),
('85555555-0000-4000-8000-000000000002', 'e5555555-0000-4000-8000-000000000005', 'a0000000-0000-4000-8000-000000000000', '61111111-0000-4000-8000-000000000001', 'b1111111-0000-4000-8000-000000000002', 1, 'supplychain_confirmed', 'ERP',              'verified',   '2024-07-01', '2025-06-30'),
('85555555-0000-4000-8000-000000000003', 'e5555555-0000-4000-8000-000000000005', 'a0000000-0000-4000-8000-000000000000', '61777777-0000-4000-8000-000000000007', 'b1111111-0000-4000-8000-000000000002', 1, 'supplychain_declared',  'SUPPLIER_DECLARED','unverified', '2024-07-01', '2025-06-30'),
('85555555-0000-4000-8000-000000000004', 'e5555555-0000-4000-8000-000000000005', '61111111-0000-4000-8000-000000000001', '62111111-0000-4000-8000-000000000001', 'b1111111-0000-4000-8000-000000000006', 2, 'supplychain_confirmed', 'SUPPLIER_DECLARED','verified',   '2024-07-01', '2025-06-30'),
('85555555-0000-4000-8000-000000000005', 'e5555555-0000-4000-8000-000000000005', '62111111-0000-4000-8000-000000000001', '63111111-0000-4000-8000-000000000001', 'b1111111-0000-4000-8000-000000000004', 3, 'supplychain_confirmed', 'SUPPLIER_DECLARED','verified',   '2024-07-01', '2025-06-30'),
('85555555-0000-4000-8000-000000000006', 'e5555555-0000-4000-8000-000000000005', '63111111-0000-4000-8000-000000000001', '64111111-0000-4000-8000-000000000001', 'b1111111-0000-4000-8000-000000000011', 4, 'supplychain_confirmed', 'SUPPLIER_DECLARED','verified',   '2024-07-01', '2025-06-30'),
('85555555-0000-4000-8000-000000000007', 'e5555555-0000-4000-8000-000000000005', '64111111-0000-4000-8000-000000000001', '65111111-0000-4000-8000-000000000001', 'b1111111-0000-4000-8000-000000000008', 5, 'supplychain_confirmed', 'SUPPLIER_DECLARED','verified',   '2024-07-01', '2025-06-30');

-- ⑥ Mercedes EQE [Happy]
INSERT INTO supply_chain_map (edge_id, bom_version_id, parent_supplier_id, child_supplier_id, part_id, hop_level, link_status, source_system, verification_status, supply_period_from, supply_period_to) VALUES
('86666666-0000-4000-8000-000000000001', 'e6666666-0000-4000-8000-000000000006', NULL,                                     'a0000000-0000-4000-8000-000000000000', 'b1111111-0000-4000-8000-000000000001', 0, 'supplychain_confirmed', 'ERP',              'verified',   '2024-04-01', '2025-03-31'),
('86666666-0000-4000-8000-000000000002', 'e6666666-0000-4000-8000-000000000006', 'a0000000-0000-4000-8000-000000000000', '61222222-0000-4000-8000-000000000002', 'b1111111-0000-4000-8000-000000000002', 1, 'supplychain_confirmed', 'ERP',              'verified',   '2024-04-01', '2025-03-31'),
('86666666-0000-4000-8000-000000000003', 'e6666666-0000-4000-8000-000000000006', '61222222-0000-4000-8000-000000000002', '62222222-0000-4000-8000-000000000002', 'b1111111-0000-4000-8000-000000000006', 2, 'supplychain_confirmed', 'SUPPLIER_DECLARED','verified',   '2024-04-01', '2025-03-31'),
('86666666-0000-4000-8000-000000000004', 'e6666666-0000-4000-8000-000000000006', '62222222-0000-4000-8000-000000000002', '63222222-0000-4000-8000-000000000002', 'b1111111-0000-4000-8000-000000000004', 3, 'supplychain_confirmed', 'SUPPLIER_DECLARED','verified',   '2024-04-01', '2025-03-31'),
('86666666-0000-4000-8000-000000000005', 'e6666666-0000-4000-8000-000000000006', '63222222-0000-4000-8000-000000000002', '64222222-0000-4000-8000-000000000002', 'b1111111-0000-4000-8000-000000000011', 4, 'supplychain_declared',  'SUPPLIER_DECLARED','unverified', '2024-04-01', '2025-03-31'),
('86666666-0000-4000-8000-000000000006', 'e6666666-0000-4000-8000-000000000006', '64222222-0000-4000-8000-000000000002', '65222222-0000-4000-8000-000000000002', 'b1111111-0000-4000-8000-000000000008', 5, 'supplychain_declared',  'SUPPLIER_DECLARED','unverified', '2024-04-01', '2025-03-31');

-- ⑦ Hyundai IONIQ 6 [Happy]
INSERT INTO supply_chain_map (edge_id, bom_version_id, parent_supplier_id, child_supplier_id, part_id, hop_level, link_status, source_system, verification_status, supply_period_from, supply_period_to) VALUES
('87777777-0000-4000-8000-000000000001', 'e7777777-0000-4000-8000-000000000007', NULL,                                     'a0000000-0000-4000-8000-000000000000', 'b1111111-0000-4000-8000-000000000001', 0, 'supplychain_confirmed', 'ERP',              'verified', '2024-01-01', '2024-12-31'),
('87777777-0000-4000-8000-000000000002', 'e7777777-0000-4000-8000-000000000007', 'a0000000-0000-4000-8000-000000000000', '61333333-0000-4000-8000-000000000003', 'b1111111-0000-4000-8000-000000000002', 1, 'supplychain_confirmed', 'ERP',              'verified', '2024-01-01', '2024-12-31'),
('87777777-0000-4000-8000-000000000003', 'e7777777-0000-4000-8000-000000000007', '61333333-0000-4000-8000-000000000003', '62333333-0000-4000-8000-000000000003', 'b1111111-0000-4000-8000-000000000006', 2, 'supplychain_confirmed', 'SUPPLIER_DECLARED','verified', '2024-01-01', '2024-12-31'),
('87777777-0000-4000-8000-000000000004', 'e7777777-0000-4000-8000-000000000007', '62333333-0000-4000-8000-000000000003', '63333333-0000-4000-8000-000000000003', 'b1111111-0000-4000-8000-000000000004', 3, 'supplychain_confirmed', 'SUPPLIER_DECLARED','verified', '2024-01-01', '2024-12-31'),
('87777777-0000-4000-8000-000000000005', 'e7777777-0000-4000-8000-000000000007', '63333333-0000-4000-8000-000000000003', '64333333-0000-4000-8000-000000000003', 'b1111111-0000-4000-8000-000000000011', 4, 'supplychain_confirmed', 'SUPPLIER_DECLARED','verified', '2024-01-01', '2024-12-31'),
('87777777-0000-4000-8000-000000000006', 'e7777777-0000-4000-8000-000000000007', '64333333-0000-4000-8000-000000000003', '65333333-0000-4000-8000-000000000003', 'b1111111-0000-4000-8000-000000000008', 5, 'supplychain_confirmed', 'SUPPLIER_DECLARED','verified', '2024-01-01', '2024-12-31');

-- ⑧ Hyundai IONIQ 5 [Sad — Zambia 코퍼벨트 분쟁광물 리스크]
INSERT INTO supply_chain_map (edge_id, bom_version_id, parent_supplier_id, child_supplier_id, part_id, hop_level, link_status, source_system, verification_status, supply_period_from, supply_period_to) VALUES
('88888888-0000-4000-8000-000000000001', 'e8888888-0000-4000-8000-000000000008', NULL,                                     'a0000000-0000-4000-8000-000000000000', 'b1111111-0000-4000-8000-000000000001', 0, 'supplychain_confirmed', 'ERP',              'verified',   '2024-03-01', '2025-02-28'),
('88888888-0000-4000-8000-000000000002', 'e8888888-0000-4000-8000-000000000008', 'a0000000-0000-4000-8000-000000000000', '61444444-0000-4000-8000-000000000004', 'b1111111-0000-4000-8000-000000000002', 1, 'supplychain_confirmed', 'ERP',              'verified',   '2024-03-01', '2025-02-28'),
('88888888-0000-4000-8000-000000000003', 'e8888888-0000-4000-8000-000000000008', '61444444-0000-4000-8000-000000000004', '62444444-0000-4000-8000-000000000004', 'b1111111-0000-4000-8000-000000000006', 2, 'supplychain_confirmed', 'SUPPLIER_DECLARED','verified',   '2024-03-01', '2025-02-28'),
('88888888-0000-4000-8000-000000000004', 'e8888888-0000-4000-8000-000000000008', '62444444-0000-4000-8000-000000000004', '63444444-0000-4000-8000-000000000004', 'b1111111-0000-4000-8000-000000000004', 3, 'supplychain_confirmed', 'SUPPLIER_DECLARED','verified',   '2024-03-01', '2025-02-28'),
('88888888-0000-4000-8000-000000000005', 'e8888888-0000-4000-8000-000000000008', '63444444-0000-4000-8000-000000000004', '64444444-0000-4000-8000-000000000004', 'b1111111-0000-4000-8000-000000000011', 4, 'supplychain_declared',  'SUPPLIER_DECLARED','unverified', '2024-03-01', '2025-02-28'),
('88888888-0000-4000-8000-000000000006', 'e8888888-0000-4000-8000-000000000008', '64444444-0000-4000-8000-000000000004', '65444444-0000-4000-8000-000000000004', 'b1111111-0000-4000-8000-000000000009', 5, 'supplychain_declared',  'SUPPLIER_DECLARED','unverified', '2024-03-01', '2025-02-28');

-- ⑨ Volkswagen ID.4 [Happy]
INSERT INTO supply_chain_map (edge_id, bom_version_id, parent_supplier_id, child_supplier_id, part_id, hop_level, link_status, source_system, verification_status, supply_period_from, supply_period_to) VALUES
('89999999-0000-4000-8000-000000000001', 'e9999999-0000-4000-8000-000000000009', NULL,                                     'a0000000-0000-4000-8000-000000000000', 'b1111111-0000-4000-8000-000000000001', 0, 'supplychain_confirmed', 'ERP',              'verified', '2024-06-01', '2024-12-31'),
('89999999-0000-4000-8000-000000000002', 'e9999999-0000-4000-8000-000000000009', 'a0000000-0000-4000-8000-000000000000', '61555555-0000-4000-8000-000000000005', 'b1111111-0000-4000-8000-000000000002', 1, 'supplychain_confirmed', 'ERP',              'verified', '2024-06-01', '2024-12-31'),
('89999999-0000-4000-8000-000000000003', 'e9999999-0000-4000-8000-000000000009', '61555555-0000-4000-8000-000000000005', '62555555-0000-4000-8000-000000000005', 'b1111111-0000-4000-8000-000000000006', 2, 'supplychain_confirmed', 'SUPPLIER_DECLARED','verified', '2024-06-01', '2024-12-31'),
('89999999-0000-4000-8000-000000000004', 'e9999999-0000-4000-8000-000000000009', '62555555-0000-4000-8000-000000000005', '63555555-0000-4000-8000-000000000005', 'b1111111-0000-4000-8000-000000000004', 3, 'supplychain_confirmed', 'SUPPLIER_DECLARED','verified', '2024-06-01', '2024-12-31'),
('89999999-0000-4000-8000-000000000005', 'e9999999-0000-4000-8000-000000000009', '63555555-0000-4000-8000-000000000005', '64555555-0000-4000-8000-000000000005', 'b1111111-0000-4000-8000-000000000011', 4, 'supplychain_confirmed', 'SUPPLIER_DECLARED','verified', '2024-06-01', '2024-12-31'),
('89999999-0000-4000-8000-000000000006', 'e9999999-0000-4000-8000-000000000009', '64555555-0000-4000-8000-000000000005', '65555555-0000-4000-8000-000000000005', 'b1111111-0000-4000-8000-000000000008', 5, 'supplychain_confirmed', 'SUPPLIER_DECLARED','verified', '2024-06-01', '2024-12-31');

-- ⑩ Volkswagen ID.7 [Gray — 4차에서 기존 Unverified Precursor Trading 재사용, 5차 없이 추적 단절]
INSERT INTO supply_chain_map (edge_id, bom_version_id, parent_supplier_id, child_supplier_id, part_id, hop_level, link_status, source_system, verification_status, supply_period_from, supply_period_to) VALUES
('8aaaaaaa-0000-4000-8000-000000000001', 'eaaaaaaa-0000-4000-8000-00000000000a', NULL,                                     'a0000000-0000-4000-8000-000000000000', 'b1111111-0000-4000-8000-000000000001', 0, 'supplychain_confirmed', 'ERP',              'verified',   '2025-01-01', '2025-12-31'),
('8aaaaaaa-0000-4000-8000-000000000002', 'eaaaaaaa-0000-4000-8000-00000000000a', 'a0000000-0000-4000-8000-000000000000', '61666666-0000-4000-8000-000000000006', 'b1111111-0000-4000-8000-000000000002', 1, 'supplychain_confirmed', 'ERP',              'verified',   '2025-01-01', '2025-12-31'),
('8aaaaaaa-0000-4000-8000-000000000003', 'eaaaaaaa-0000-4000-8000-00000000000a', '61666666-0000-4000-8000-000000000006', '62666666-0000-4000-8000-000000000006', 'b1111111-0000-4000-8000-000000000006', 2, 'supplychain_confirmed', 'SUPPLIER_DECLARED','verified',   '2025-01-01', '2025-12-31'),
('8aaaaaaa-0000-4000-8000-000000000004', 'eaaaaaaa-0000-4000-8000-00000000000a', '62666666-0000-4000-8000-000000000006', '63666666-0000-4000-8000-000000000006', 'b1111111-0000-4000-8000-000000000004', 3, 'supplychain_confirmed', 'SUPPLIER_DECLARED','verified',   '2025-01-01', '2025-12-31'),
('8aaaaaaa-0000-4000-8000-000000000005', 'eaaaaaaa-0000-4000-8000-00000000000a', '63666666-0000-4000-8000-000000000006', 'abababab-abab-4000-8000-0000000000ab', 'b1111111-0000-4000-8000-000000000011', 4, 'supplychain_declared',  'SUPPLIER_DECLARED','unverified', '2025-01-01', '2025-12-31');

-- 공급망 맵 헤더(supply_chain_maps) 백필 + map_id 백필 (신규 6개 bom_version)
INSERT INTO supply_chain_maps (map_id, bom_version_id, product_id, status)
SELECT gen_random_uuid(), bv.bom_version_id, bv.product_id, 'completed'
FROM bom_versions bv
WHERE EXISTS (SELECT 1 FROM supply_chain_map scm WHERE scm.bom_version_id = bv.bom_version_id)
  AND NOT EXISTS (SELECT 1 FROM supply_chain_maps h WHERE h.bom_version_id = bv.bom_version_id);

UPDATE supply_chain_map scm SET map_id = h.map_id
FROM supply_chain_maps h
WHERE h.bom_version_id = scm.bom_version_id AND scm.map_id IS NULL;

-- 신규 6개 제품 전 엣지 supply_ratio (분할납품 비율 — BMW iX는 삼보 90% + 신성 10% dual-source)
INSERT INTO supply_ratio (edge_id, factory_id, ratio_percentage, volume, unit) VALUES
('85555555-0000-4000-8000-000000000001', 'f0000000-0000-4000-8000-000000000000', 100.00,   500, 'ea'),
('85555555-0000-4000-8000-000000000002', '71111111-0000-4000-8000-000000000001',  90.00,  9450, 'ea'),
('85555555-0000-4000-8000-000000000003', '71777777-0000-4000-8000-000000000007',  10.00,  1050, 'ea'),
('85555555-0000-4000-8000-000000000004', '72111111-0000-4000-8000-000000000001', 100.00, 42000, 'kg'),
('85555555-0000-4000-8000-000000000005', '73111111-0000-4000-8000-000000000001', 100.00, 25000, 'kg'),
('85555555-0000-4000-8000-000000000006', '74111111-0000-4000-8000-000000000001', 100.00, 20000, 'kg'),
('85555555-0000-4000-8000-000000000007', '75111111-0000-4000-8000-000000000001', 100.00, 26000, 'kg'),
('86666666-0000-4000-8000-000000000001', 'f0000000-0000-4000-8000-000000000000', 100.00,   480, 'ea'),
('86666666-0000-4000-8000-000000000002', '71222222-0000-4000-8000-000000000002', 100.00,  9200, 'ea'),
('86666666-0000-4000-8000-000000000003', '72222222-0000-4000-8000-000000000002', 100.00, 38000, 'kg'),
('86666666-0000-4000-8000-000000000004', '73222222-0000-4000-8000-000000000002', 100.00, 23000, 'kg'),
('86666666-0000-4000-8000-000000000005', '74222222-0000-4000-8000-000000000002', 100.00, 18500, 'kg'),
('86666666-0000-4000-8000-000000000006', '75222222-0000-4000-8000-000000000002', 100.00, 24000, 'kg'),
('87777777-0000-4000-8000-000000000001', 'f0000000-0000-4000-8000-000000000000', 100.00,   460, 'ea'),
('87777777-0000-4000-8000-000000000002', '71333333-0000-4000-8000-000000000003', 100.00,  9500, 'ea'),
('87777777-0000-4000-8000-000000000003', '72333333-0000-4000-8000-000000000003', 100.00, 40000, 'kg'),
('87777777-0000-4000-8000-000000000004', '73333333-0000-4000-8000-000000000003', 100.00, 24000, 'kg'),
('87777777-0000-4000-8000-000000000005', '74333333-0000-4000-8000-000000000003', 100.00, 19000, 'kg'),
('87777777-0000-4000-8000-000000000006', '75333333-0000-4000-8000-000000000003', 100.00, 25000, 'kg'),
('88888888-0000-4000-8000-000000000001', 'f0000000-0000-4000-8000-000000000000', 100.00,   420, 'ea'),
('88888888-0000-4000-8000-000000000002', '71444444-0000-4000-8000-000000000004', 100.00,  8500, 'ea'),
('88888888-0000-4000-8000-000000000003', '72444444-0000-4000-8000-000000000004', 100.00, 36000, 'kg'),
('88888888-0000-4000-8000-000000000004', '73444444-0000-4000-8000-000000000004', 100.00, 21000, 'kg'),
('88888888-0000-4000-8000-000000000005', '74444444-0000-4000-8000-000000000004', 100.00, 17000, 'kg'),
('88888888-0000-4000-8000-000000000006', '75444444-0000-4000-8000-000000000004', 100.00, 22000, 'kg'),
('89999999-0000-4000-8000-000000000001', 'f0000000-0000-4000-8000-000000000000', 100.00,   410, 'ea'),
('89999999-0000-4000-8000-000000000002', '71555555-0000-4000-8000-000000000005', 100.00,  8800, 'ea'),
('89999999-0000-4000-8000-000000000003', '72555555-0000-4000-8000-000000000005', 100.00, 37000, 'kg'),
('89999999-0000-4000-8000-000000000004', '73555555-0000-4000-8000-000000000005', 100.00, 22000, 'kg'),
('89999999-0000-4000-8000-000000000005', '74555555-0000-4000-8000-000000000005', 100.00, 17500, 'kg'),
('89999999-0000-4000-8000-000000000006', '75555555-0000-4000-8000-000000000005', 100.00, 23000, 'kg'),
('8aaaaaaa-0000-4000-8000-000000000001', 'f0000000-0000-4000-8000-000000000000', 100.00,   490, 'ea'),
('8aaaaaaa-0000-4000-8000-000000000002', '71666666-0000-4000-8000-000000000006', 100.00, 10000, 'ea'),
('8aaaaaaa-0000-4000-8000-000000000003', '72666666-0000-4000-8000-000000000006', 100.00, 42000, 'kg'),
('8aaaaaaa-0000-4000-8000-000000000004', '73666666-0000-4000-8000-000000000006', 100.00, 25000, 'kg');
-- 8aaaaaaa-...005(hop4, Unverified Trader): factory 미확인 — supply_ratio 의도적 미입력 (Gray 시나리오)

-- ============================================================
-- 16-SUMMARY (검증용)
-- ============================================================
-- Ingest 묶음(bom_version):  기존 5 + 신규 6 = 11개
-- 1차 협력사(hop1 최소차수): 기존 3 + 신규 7 = 10개 (신성배터리=이중소싱 보조사)
-- 2차 협력사(hop2 최소차수): 기존 3 + 신규 6 =  9개
-- 3차 협력사(hop3 최소차수): 기존 2 + 신규 6 =  8개
-- 4차 협력사(hop4 최소차수): 기존 2 + 신규 5 =  7개 (VW ID.7은 기존 Unverified Trader 재사용)
-- 5차 협력사(hop5 최소차수): 기존 1 + 신규 5 =  6개 (VW ID.7은 Gray 시나리오로 5차 없음)


-- ============================================================
-- 17. VW ID.7 — 완료 상태 → 미완료(building)로 되돌림
-- ============================================================
-- 나머지 신규 5개 제품(BMW iX/EQE/IONIQ6/IONIQ5/ID.4)은 Happy 시나리오로 완료 유지.
-- VW ID.7만 STEP2~5를 직접 밟아 워크플로우를 검증할 수 있도록 미검증 상태로 되돌린다.
UPDATE supply_chain_maps SET status = 'building', completed_by = NULL, completed_at = NULL
WHERE bom_version_id = 'eaaaaaaa-0000-4000-8000-00000000000a';

UPDATE supply_chain_map SET verification_status = 'unverified'
WHERE bom_version_id = 'eaaaaaaa-0000-4000-8000-00000000000a';


-- ============================================================
-- 18. 신규 제품 6개 — bom_items (MBOM 트리 채우기)
-- ============================================================
-- 공급망 맵 엣지가 쓰는 part 체인(Module→CAM→PRE→REF-NI→MIN-NI)과 동일 품목으로 구성.
-- direct_material_cost는 parts.unit_price 값을 그대로 사용(기존 iX3/i4 패턴과 동일).
-- VW ID.7(eaaaaaaa)은 4차에서 Unverified Trader로 끊기는 Gray 시나리오라 MIN-NI(5차) 품목 제외.

-- ⑤ BMW iX (e5555555)
INSERT INTO bom_items (bom_version_id, part_id, required_quantity, required_quantity_unit, percentage, direct_material_cost, origin_country, source_system, external_id) VALUES
('e5555555-0000-4000-8000-000000000005', 'b1111111-0000-4000-8000-000000000002', 95, 'ea', 45.00, 400.0000, 'KR', 'ERP_PLM', 'ERP-BI-IX-MOD'),
('e5555555-0000-4000-8000-000000000005', 'b1111111-0000-4000-8000-000000000006', 38, 'kg', 20.00,  90.0000, 'KR', 'ERP_PLM', 'ERP-BI-IX-CAM'),
('e5555555-0000-4000-8000-000000000005', 'b1111111-0000-4000-8000-000000000004', 24, 'kg', 15.00,  40.0000, 'KR', 'ERP_PLM', 'ERP-BI-IX-PRE'),
('e5555555-0000-4000-8000-000000000005', 'b1111111-0000-4000-8000-000000000011', 18, 'kg', 12.00,  22.0000, 'KR', 'ERP_PLM', 'ERP-BI-IX-REFNI'),
('e5555555-0000-4000-8000-000000000005', 'b1111111-0000-4000-8000-000000000008', 22, 'kg',  8.00,  18.0000, 'ID', 'ERP_PLM', 'ERP-BI-IX-NI');

-- ⑥ Mercedes EQE (e6666666)
INSERT INTO bom_items (bom_version_id, part_id, required_quantity, required_quantity_unit, percentage, direct_material_cost, origin_country, source_system, external_id) VALUES
('e6666666-0000-4000-8000-000000000006', 'b1111111-0000-4000-8000-000000000002', 90, 'ea', 45.00, 400.0000, 'KR', 'ERP_PLM', 'ERP-BI-EQE-MOD'),
('e6666666-0000-4000-8000-000000000006', 'b1111111-0000-4000-8000-000000000006', 36, 'kg', 20.00,  90.0000, 'KR', 'ERP_PLM', 'ERP-BI-EQE-CAM'),
('e6666666-0000-4000-8000-000000000006', 'b1111111-0000-4000-8000-000000000004', 23, 'kg', 15.00,  40.0000, 'KR', 'ERP_PLM', 'ERP-BI-EQE-PRE'),
('e6666666-0000-4000-8000-000000000006', 'b1111111-0000-4000-8000-000000000011', 17, 'kg', 12.00,  22.0000, 'ID', 'ERP_PLM', 'ERP-BI-EQE-REFNI'),
('e6666666-0000-4000-8000-000000000006', 'b1111111-0000-4000-8000-000000000008', 21, 'kg',  8.00,  18.0000, 'ID', 'ERP_PLM', 'ERP-BI-EQE-NI');

-- ⑦ Hyundai IONIQ 6 (e7777777)
INSERT INTO bom_items (bom_version_id, part_id, required_quantity, required_quantity_unit, percentage, direct_material_cost, origin_country, source_system, external_id) VALUES
('e7777777-0000-4000-8000-000000000007', 'b1111111-0000-4000-8000-000000000002', 88, 'ea', 45.00, 400.0000, 'KR', 'ERP_PLM', 'ERP-BI-I6-MOD'),
('e7777777-0000-4000-8000-000000000007', 'b1111111-0000-4000-8000-000000000006', 35, 'kg', 20.00,  90.0000, 'KR', 'ERP_PLM', 'ERP-BI-I6-CAM'),
('e7777777-0000-4000-8000-000000000007', 'b1111111-0000-4000-8000-000000000004', 22, 'kg', 15.00,  40.0000, 'KR', 'ERP_PLM', 'ERP-BI-I6-PRE'),
('e7777777-0000-4000-8000-000000000007', 'b1111111-0000-4000-8000-000000000011', 17, 'kg', 12.00,  22.0000, 'AU', 'ERP_PLM', 'ERP-BI-I6-REFNI'),
('e7777777-0000-4000-8000-000000000007', 'b1111111-0000-4000-8000-000000000008', 21, 'kg',  8.00,  18.0000, 'AU', 'ERP_PLM', 'ERP-BI-I6-NI');

-- ⑧ Hyundai IONIQ 5 (e8888888)
INSERT INTO bom_items (bom_version_id, part_id, required_quantity, required_quantity_unit, percentage, direct_material_cost, origin_country, source_system, external_id) VALUES
('e8888888-0000-4000-8000-000000000008', 'b1111111-0000-4000-8000-000000000002', 78, 'ea', 45.00, 400.0000, 'KR', 'ERP_PLM', 'ERP-BI-I5-MOD'),
('e8888888-0000-4000-8000-000000000008', 'b1111111-0000-4000-8000-000000000006', 31, 'kg', 20.00,  90.0000, 'KR', 'ERP_PLM', 'ERP-BI-I5-CAM'),
('e8888888-0000-4000-8000-000000000008', 'b1111111-0000-4000-8000-000000000004', 20, 'kg', 15.00,  40.0000, 'KR', 'ERP_PLM', 'ERP-BI-I5-PRE'),
('e8888888-0000-4000-8000-000000000008', 'b1111111-0000-4000-8000-000000000011', 15, 'kg', 12.00,  22.0000, 'ZM', 'ERP_PLM', 'ERP-BI-I5-REFNI'),
('e8888888-0000-4000-8000-000000000008', 'b1111111-0000-4000-8000-000000000008', 18, 'kg',  8.00,  18.0000, 'ZM', 'ERP_PLM', 'ERP-BI-I5-NI');

-- ⑨ Volkswagen ID.4 (e9999999)
INSERT INTO bom_items (bom_version_id, part_id, required_quantity, required_quantity_unit, percentage, direct_material_cost, origin_country, source_system, external_id) VALUES
('e9999999-0000-4000-8000-000000000009', 'b1111111-0000-4000-8000-000000000002', 76, 'ea', 45.00, 400.0000, 'KR', 'ERP_PLM', 'ERP-BI-ID4-MOD'),
('e9999999-0000-4000-8000-000000000009', 'b1111111-0000-4000-8000-000000000006', 30, 'kg', 20.00,  90.0000, 'KR', 'ERP_PLM', 'ERP-BI-ID4-CAM'),
('e9999999-0000-4000-8000-000000000009', 'b1111111-0000-4000-8000-000000000004', 19, 'kg', 15.00,  40.0000, 'KR', 'ERP_PLM', 'ERP-BI-ID4-PRE'),
('e9999999-0000-4000-8000-000000000009', 'b1111111-0000-4000-8000-000000000011', 15, 'kg', 12.00,  22.0000, 'BR', 'ERP_PLM', 'ERP-BI-ID4-REFNI'),
('e9999999-0000-4000-8000-000000000009', 'b1111111-0000-4000-8000-000000000008', 18, 'kg',  8.00,  18.0000, 'BR', 'ERP_PLM', 'ERP-BI-ID4-NI');

-- ⑩ Volkswagen ID.7 (eaaaaaaa) — Gray: 4차(Unverified Trader)에서 끊김, MIN-NI(5차) 품목 없음
INSERT INTO bom_items (bom_version_id, part_id, required_quantity, required_quantity_unit, percentage, direct_material_cost, origin_country, source_system, external_id) VALUES
('eaaaaaaa-0000-4000-8000-00000000000a', 'b1111111-0000-4000-8000-000000000002', 102, 'ea', 50.00, 400.0000, 'KR', 'ERP_PLM', 'ERP-BI-ID7-MOD'),
('eaaaaaaa-0000-4000-8000-00000000000a', 'b1111111-0000-4000-8000-000000000006', 41,  'kg', 22.00,  90.0000, 'KR', 'ERP_PLM', 'ERP-BI-ID7-CAM'),
('eaaaaaaa-0000-4000-8000-00000000000a', 'b1111111-0000-4000-8000-000000000004', 26,  'kg', 16.00,  40.0000, 'KR', 'ERP_PLM', 'ERP-BI-ID7-PRE'),
('eaaaaaaa-0000-4000-8000-00000000000a', 'b1111111-0000-4000-8000-000000000011', 20,  'kg', 12.00,  22.0000, NULL, 'ERP_PLM', 'ERP-BI-ID7-REFNI');


-- ============================================================
-- 19. 기존(레거시) 협력사 11개 — 기준정보·맵정보 상세 백필
-- ============================================================
-- 대상: 4대 시나리오(iX3/i4/GLC/EQS)에 이미 쓰이던 원래 협력사들.
-- [의도적 예외 — 손대지 않음]
--   · Global Mining Corp(a5555555): 위반(violation) 노드. 서류·핵심광물 미제출 상태가
--     Sad 시나리오(위반 판정)의 서사 그 자체 — 채우면 오히려 시나리오가 깨짐.
--   · Unverified Precursor Trading(abababab): Gray 시나리오의 "미확인 트레이더".
--     원산지·서류 불명이 이름 그대로의 의미 — 공장·서류 미보유가 정상.
--   · 한양셀 core_minerals: 이전 세션에서 마스터폼으로 직접 수정한 값 — 덮어쓰지 않음.
-- ============================================================

-- 19-1. 기준정보 백필 (사업자등록번호/주소/서류 URL)
UPDATE suppliers SET business_reg_no = '101-86-40001', address = '경상북도 포항시 남구 오천읍 포항산단로 55',
  business_reg_doc_url = 's3://kira-docs/suppliers/a1111111/biz_reg.pdf',
  environmental_report_url = 's3://kira-docs/suppliers/a1111111/env_report.pdf',
  self_assessment_doc_url = 's3://kira-docs/suppliers/a1111111/self_assess.pdf'
WHERE supplier_id = 'a1111111-1111-4000-8000-000000000001';

UPDATE suppliers SET business_reg_no = '102-86-40002', address = '울산광역시 울주군 온산읍 산업로 700',
  core_minerals = '{"Li":7.0,"Ni":80.0,"Co":10.0,"Mn":10.0}'::jsonb,
  business_reg_doc_url = 's3://kira-docs/suppliers/a7777777/biz_reg.pdf',
  environmental_report_url = 's3://kira-docs/suppliers/a7777777/env_report.pdf',
  self_assessment_doc_url = 's3://kira-docs/suppliers/a7777777/self_assess.pdf'
WHERE supplier_id = 'a7777777-7777-4000-8000-000000000007';

UPDATE suppliers SET business_reg_no = '103-86-40003', address = '충청북도 청주시 흥덕구 오송읍 오송생명2로 25',
  core_minerals = '{"Li":7.0,"Ni":80.0,"Co":10.0,"Mn":10.0}'::jsonb,
  business_reg_doc_url = 's3://kira-docs/suppliers/a8888888/biz_reg.pdf',
  environmental_report_url = 's3://kira-docs/suppliers/a8888888/env_report.pdf',
  self_assessment_doc_url = 's3://kira-docs/suppliers/a8888888/self_assess.pdf'
WHERE supplier_id = 'a8888888-8888-4000-8000-000000000008';

UPDATE suppliers SET business_reg_no = '104-86-40004', address = '충청남도 천안시 서북구 성환읍 성환산단로 10',
  core_minerals = '{"Ni":56.0,"Co":8.0,"Mn":6.0}'::jsonb,
  business_reg_doc_url = 's3://kira-docs/suppliers/a2222222/biz_reg.pdf',
  environmental_report_url = 's3://kira-docs/suppliers/a2222222/env_report.pdf'
WHERE supplier_id = 'a2222222-2222-4000-8000-000000000002';

UPDATE suppliers SET business_reg_no = '105-86-40005', address = '전라남도 광양시 광양읍 광양산단로 18',
  core_minerals = '{"Ni":50.0,"Co":10.0,"Mn":10.0}'::jsonb,
  business_reg_doc_url = 's3://kira-docs/suppliers/a6666666/biz_reg.pdf',
  environmental_report_url = 's3://kira-docs/suppliers/a6666666/env_report.pdf'
WHERE supplier_id = 'a6666666-6666-4000-8000-000000000006';

UPDATE suppliers SET address = 'Urumqi, Xinjiang Uyghur Autonomous Region, China',
  core_minerals = '{"Ni":98.0}'::jsonb,
  business_reg_doc_url = 's3://kira-docs/suppliers/acacacac/biz_reg.pdf'
WHERE supplier_id = 'acacacac-acac-4000-8000-0000000000ac';

UPDATE suppliers SET business_reg_no = '106-86-40006', address = '경상남도 울산시 울주군 온산읍 온산공단로 60',
  core_minerals = '{"Ni":99.3,"Co":99.5}'::jsonb,
  business_reg_doc_url = 's3://kira-docs/suppliers/aaaaaaaa/biz_reg.pdf',
  environmental_report_url = 's3://kira-docs/suppliers/aaaaaaaa/env_report.pdf'
WHERE supplier_id = 'aaaaaaaa-aaaa-4000-8000-00000000000a';

UPDATE suppliers SET business_reg_no = 'CL-RUT-770511', address = 'Antofagasta Region, Salar de Atacama Industrial Zone, Chile',
  core_minerals = '{"Li":99.6}'::jsonb,
  business_reg_doc_url = 's3://kira-docs/suppliers/a9999999/biz_reg.pdf'
WHERE supplier_id = 'a9999999-9999-4000-8000-000000000009';

UPDATE suppliers SET business_reg_no = 'AU-ABN-51824753556', address = 'Western Australia, Greenbushes Mining District, Australia',
  core_minerals = '{"Li":99.8}'::jsonb,
  business_reg_doc_url = 's3://kira-docs/suppliers/a3333333/biz_reg.pdf'
WHERE supplier_id = 'a3333333-3333-4000-8000-000000000003';

-- 19-2. 1차 협력사 PIC 3명 채우기 (한양셀: 기존 1명 + 신규 2명 / 우진배터리·우진셀: 신규 3명씩)
INSERT INTO supplier_contacts (supplier_id, factory_id, name, name_en, role, department, email, phone, is_primary, language) VALUES
('a1111111-1111-4000-8000-000000000001', 'f1111111-0000-4000-8000-000000000001', '박서준', 'Park SJ', 'Quality Manager', '품질관리팀', 'sj.park@hanyang.demo', '+82-54-000-0002', FALSE, 'ko'),
('a1111111-1111-4000-8000-000000000001', 'f1111111-0000-4000-8000-000000000001', '최유리', 'Choi YR', 'Compliance Officer', '법무팀', 'yr.choi@hanyang.demo', '+82-54-000-0003', FALSE, 'ko'),
('a7777777-7777-4000-8000-000000000007', 'f7777777-0000-4000-8000-000000000007', '박준영', 'Park JY', 'ESG Manager', 'ESG팀', 'jy.park@woojinbattery.demo', '+82-52-000-0011', TRUE,  'ko'),
('a7777777-7777-4000-8000-000000000007', 'f7777777-0000-4000-8000-000000000007', '이하늘', 'Lee HN', 'Plant Manager', '생산기술팀', 'hn.lee@woojinbattery.demo', '+82-52-000-0012', FALSE, 'ko'),
('a7777777-7777-4000-8000-000000000007', 'f7777777-0000-4000-8000-000000000007', '정민수', 'Jung MS', 'Safety Manager', '안전환경팀', 'ms.jung@woojinbattery.demo', '+82-52-000-0013', FALSE, 'ko'),
('a8888888-8888-4000-8000-000000000008', 'f8888888-0000-4000-8000-000000000008', '한지원', 'Han JW', 'ESG Officer', 'ESG팀', 'jw.han@woojincell.demo', '+82-43-000-0021', TRUE,  'ko'),
('a8888888-8888-4000-8000-000000000008', 'f8888888-0000-4000-8000-000000000008', '오세훈', 'Oh SH', 'Purchasing Manager', '구매팀', 'sh.oh@woojincell.demo', '+82-43-000-0022', FALSE, 'ko'),
('a8888888-8888-4000-8000-000000000008', 'f8888888-0000-4000-8000-000000000008', '신아영', 'Shin AY', 'R&D Manager', '연구개발팀', 'ay.shin@woojincell.demo', '+82-43-000-0023', FALSE, 'ko'),
-- 19-3. 2차~5차 협력사 PIC 1명씩 (GlobalMining·Unverified Trader 제외)
('a2222222-2222-4000-8000-000000000002', 'f2222222-0000-4000-8000-000000000002', '최도윤', 'Choi DY', 'ESG Manager', 'ESG팀', 'dy.choi@dongsungmat.demo', '+82-41-000-0031', TRUE, 'ko'),
('a6666666-6666-4000-8000-000000000006', 'f6666666-0000-4000-8000-000000000006', '정하율', 'Jung HY', 'ESG Officer', 'ESG팀', 'hy.jung@cheongjeongpre.demo', '+82-61-000-0041', TRUE, 'ko'),
('acacacac-acac-4000-8000-0000000000ac', 'facacaca-0000-4000-8000-0000000000ac', 'Wei Chen', 'Wei Chen', 'Compliance', 'Compliance', 'w.chen@xjrefinery.demo', '+86-991-000-0051', TRUE, 'en'),
('aaaaaaaa-aaaa-4000-8000-00000000000a', 'faaaaaaa-0000-4000-8000-00000000000a', '윤성민', 'Yoon SM', 'Compliance Manager', 'Compliance', 'sm.yoon@hanjungref.demo', '+82-52-000-0061', TRUE, 'ko'),
('a9999999-9999-4000-8000-000000000009', 'f9999999-0000-4000-8000-000000000009', 'Diego Fernandez', 'Diego Fernandez', 'Mine Manager', 'Operations', 'd.fernandez@chilelithium.demo', '+56-55-000-0071', TRUE, 'en'),
('a3333333-3333-4000-8000-000000000003', 'f3333333-0000-4000-8000-000000000003', 'Emma Clarke', 'Emma Clarke', 'Mine Manager', 'Operations', 'e.clarke@auslithium.demo', '+61-8-000-0081', TRUE, 'en');

-- 19-4. 제조사 상세(탄소집약도/에너지원) — 누락분(우진셀·청정전구체)만 추가
INSERT INTO supplier_manufacturer_details (supplier_id, manufacturing_process, energy_source, capacity, carbon_intensity) VALUES
('a8888888-8888-4000-8000-000000000008', 'NCM811 Cell Assembly', 'mixed', '6GWh/yr', 2.6800),
('a6666666-6666-4000-8000-000000000006', 'NCM Precursor Co-precipitation', 'renewable', '10kt/yr', 3.4500);

-- 19-5. 공장 탄소발자국 선언 — 누락분(우진셀·청정전구체·한중제련·신장니켈제련) 추가
INSERT INTO factory_carbon_declarations (factory_id, carbon_intensity, methodology, declared_at, valid_from, source) VALUES
('f8888888-0000-4000-8000-000000000008', 2.6800, 'PEF', '2025-06-01', '2025-06-01', 'supplier_declared'),
('f6666666-0000-4000-8000-000000000006', 3.4500, 'PEF', '2025-01-01', '2025-01-01', 'supplier_declared'),
('faaaaaaa-0000-4000-8000-00000000000a', 2.9000, 'PEF', '2025-01-01', '2025-01-01', 'third_party_verified'),
('facacaca-0000-4000-8000-0000000000ac', 5.2000, 'PEF', '2024-06-01', '2024-06-01', 'supplier_declared');


-- ============================================================
-- 20. 공장 담당자(factory_manager_*) 백필
-- ============================================================
-- [의존] supplier_factories.factory_manager_name/role/phone/email —
--   feature/eunjin(65a00e2 "공장 단위 담당자 필드 추가")에서 신설된 컬럼.
--   이 seed는 그 스키마 병합을 전제로 한다 — 병합 전 단독 실행 시 컬럼 없음 에러 발생.
-- 이미 seed된 supplier_contacts(협력사 PIC) 중 공장에 연결된 대표(또는 최초) 담당자를
-- factory_manager_*에도 그대로 반영 — 새 인물을 만들지 않고 기존 데이터를 재사용.
UPDATE supplier_factories sf
SET factory_manager_name  = c.name,
    factory_manager_role  = c.role,
    factory_manager_phone = c.phone,
    factory_manager_email = c.email
FROM (
  SELECT DISTINCT ON (factory_id) factory_id, name, role, phone, email
  FROM supplier_contacts
  WHERE factory_id IS NOT NULL
  ORDER BY factory_id, is_primary DESC, created_at ASC
) c
WHERE sf.factory_id = c.factory_id
  AND sf.factory_manager_name IS NULL;


-- ============================================================
-- 21. 5차 협력사(광산) 5곳 — 핵심광물 누락분 채우기
-- ============================================================
-- 제련소(정제 후 99%대)와 구분해 원광 실제 품위에 가까운 수치로 반영
-- (니켈 라테라이트 1~2%대, 코발트광 2~3%대).
-- GlobalMining Corp·Unverified Precursor Trading·신성배터리(주)는 각각 위반/Gray/온보딩초기
-- 시나리오상 의도적으로 비워둔 것이라 대상에서 제외.
UPDATE suppliers SET core_minerals = '{"Ni":1.8}'::jsonb WHERE supplier_id = '65111111-0000-4000-8000-000000000001';
UPDATE suppliers SET core_minerals = '{"Ni":1.6}'::jsonb WHERE supplier_id = '65222222-0000-4000-8000-000000000002';
UPDATE suppliers SET core_minerals = '{"Ni":2.1}'::jsonb WHERE supplier_id = '65333333-0000-4000-8000-000000000003';
UPDATE suppliers SET core_minerals = '{"Co":2.5}'::jsonb WHERE supplier_id = '65444444-0000-4000-8000-000000000004';
UPDATE suppliers SET core_minerals = '{"Ni":1.4}'::jsonb WHERE supplier_id = '65555555-0000-4000-8000-000000000005';
