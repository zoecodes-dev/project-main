-- ============================================================
-- KIRA 플랫폼 통합 시드 데이터 (02_seed_data.sql)
-- ============================================================
-- [통합 내역]
--   구 02_seed_data.sql + 03_seed_regulations.sql + 04_seed_regulations_index.sql
--   → 본 파일 하나로 통합. (03·04는 폐기)
--
-- [regulations 제외]
--   regulations 10종과 pgvector 인덱스(hnsw)는 01_schema.sql이 이미 적재한다.
--   중복 INSERT는 regulation_code UNIQUE 위반을 일으키므로 seed에서는 다루지 않는다.
--   (결정: regulations는 schema가 단일 소스, seed는 시나리오 데이터만)
--
-- [상태값]
--   schema.sql CHECK 제약에 맞춰 전부 접두어 표기.
--   (구 'verified'/'review'/'pending' → 'supplier_verified'/'supplier_review'/...)
--
-- [시연 3종 시나리오 — PROJECT_CORE 10장 / Happy·Sad·Gray]
--   Happy = 한양셀(EU向) 정상 흐름 → DPP 발행 완료
--   Sad   = Global Mining(US向, FEOC 위반·신장 인접) → risk 70+ → HITL 반려 → 발행 차단
--   Gray  = 대성정밀(저신뢰 파싱 + needs_human_review) → HITL 검토 대기
--
-- 실행 전제: 01_schema.sql 이후 적재(파일명 알파벳 순 자동 실행).
-- ============================================================


-- ============================================================
-- 1. 테넌트 / 사용자 / 권한 (영역 1)
-- ============================================================
INSERT INTO tenants (tenant_id, company_name, business_reg_no, subscription_status)
VALUES ('a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'KIRA Demo OEM', '123-45-67890', 'active');

-- 원청 관리자 + ESG/구매 담당자 + 협력사 사용자
INSERT INTO users (user_id, tenant_id, email, password_hash, name, role) VALUES
('11111111-0000-4000-8000-000000000001', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'admin@kira.demo',      'hashed_pw', 'Admin User',      'admin'),
('11111111-0000-4000-8000-000000000002', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'esg@kira.demo',        'hashed_pw', 'ESG Manager',     'owner_esg'),
('11111111-0000-4000-8000-000000000003', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'buyer@kira.demo',      'hashed_pw', 'Purchasing Lead', 'owner_purchasing'),
('11111111-0000-4000-8000-000000000004', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'ceo@hanyang.demo',     'hashed_pw', 'Hanyang CEO',     'supplier_ceo'),
('11111111-0000-4000-8000-000000000005', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'esg@globalmining.demo','hashed_pw', 'GMC ESG',         'supplier_esg');
-- (view_permissions는 참조 협력사가 먼저 존재해야 하므로 섹션 2 뒤로 이동)


-- ============================================================
-- 2. 협력사 마스터 (영역 2) — 3종 시나리오 주체
-- ============================================================
-- [Happy] 한양셀 제조(주) — 1차 제조사, EU向, 검증 완료
INSERT INTO suppliers (supplier_id, tenant_id, company_name, company_name_en, company_name_ko, ceo_name, supplier_type, tier, completeness_score, status, risk_level, feoc_status) VALUES
('c1111111-0000-4000-8000-000000000001', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '한양셀 제조(주)', 'Hanyang Cell Mfg', '한양셀 제조(주)', 'Kim CEO', 'manufacturer', 1, 92, 'supplier_verified', 'low', 'eligible');

-- [Sad] Global Mining Corp — 2차 광산, US向, FEOC 위반·신장 인접·고위험
INSERT INTO suppliers (supplier_id, tenant_id, company_name, company_name_en, ceo_name, supplier_type, tier, completeness_score, status, risk_level, feoc_status) VALUES
('c2222222-0000-4000-8000-000000000002', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'Global Mining Corp', 'Global Mining Corp', 'Zhang CEO', 'miner', 2, 70, 'supplier_violation', 'critical', 'ineligible');

-- [Gray] 대성정밀(주) — 1차 부품, 저신뢰 파싱·검토 대기
INSERT INTO suppliers (supplier_id, tenant_id, company_name, company_name_en, company_name_ko, ceo_name, supplier_type, tier, completeness_score, status, risk_level, feoc_status) VALUES
('c3333333-0000-4000-8000-000000000003', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '대성정밀(주)', 'Daesung Precision', '대성정밀(주)', 'Lee CEO', 'manufacturer', 1, 55, 'supplier_review', 'medium', 'under_review');

-- 보조: 전구체 트레이더 (공급망 깊이용)
INSERT INTO suppliers (supplier_id, tenant_id, company_name, company_name_en, supplier_type, tier, completeness_score, status, risk_level, feoc_status) VALUES
('c4444444-0000-4000-8000-000000000004', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'Precursor Trading Ltd', 'Precursor Trading Ltd', 'trader', 2, 60, 'supplier_in_progress', 'medium', 'under_review');

-- view_permissions: ESG 담당자가 1차 협력사(한양셀) 하위까지 열람 (권한 토글 시연용)
-- (협력사가 모두 정의된 뒤에 둔다 — viewable_supplier_id는 FK는 아니나 정합성 위해 순서 유지)
INSERT INTO view_permissions (user_id, viewable_supplier_id, can_view_parent, can_view_children, can_view_siblings, depth_limit, granted_by) VALUES
('11111111-0000-4000-8000-000000000002', 'c1111111-0000-4000-8000-000000000001', FALSE, TRUE, FALSE, 3, '11111111-0000-4000-8000-000000000001');

-- 공장 (PostGIS 좌표 포함 — Geo Audit 핵심)
--   포항(KR, EU向), 광양(KR, US向), 신장 인접 광산(86.0 41.0 = 신장 폴리곤 내부), 대성 화성공장
INSERT INTO supplier_factories (factory_id, supplier_id, factory_name, factory_name_en, country, region, location, factory_role, destination, applicable_regulations, supply_ratio_percent) VALUES
('f1111111-0000-4000-8000-000000000001', 'c1111111-0000-4000-8000-000000000001', '포항 제1공장', 'Pohang Plant 1', 'KR', 'Pohang', ST_SetSRID(ST_MakePoint(129.343, 36.019), 4326), 'production', 'EU', '["EU_BATTERY","EU_BATTERY_ART7","EU_BATTERY_ART47","EUDR","CSDDD"]'::jsonb, 100.00),
('f1111111-0000-4000-8000-000000000002', 'c1111111-0000-4000-8000-000000000001', '광양 제2공장', 'Gwangyang Plant 2', 'KR', 'Gwangyang', ST_SetSRID(ST_MakePoint(127.700, 34.940), 4326), 'production', 'US', '["UFLPA","IRA"]'::jsonb, 100.00),
('f2222222-0000-4000-8000-000000000001', 'c2222222-0000-4000-8000-000000000002', 'Xinjiang Mine A', 'Xinjiang Mine A', 'CN', 'Xinjiang', ST_SetSRID(ST_MakePoint(86.000, 41.000), 4326), 'mining', 'US', '["UFLPA","IRA"]'::jsonb, 100.00),
('f3333333-0000-4000-8000-000000000001', 'c3333333-0000-4000-8000-000000000003', '화성 공장', 'Hwaseong Plant', 'KR', 'Hwaseong', ST_SetSRID(ST_MakePoint(126.831, 37.199), 4326), 'production', 'EU', '["EU_BATTERY","CSDDD"]'::jsonb, 100.00);

-- 연락 담당자
INSERT INTO supplier_contacts (supplier_id, factory_id, name, name_en, role, department, email, phone, is_primary, language) VALUES
('c1111111-0000-4000-8000-000000000001', 'f1111111-0000-4000-8000-000000000001', '김담당', 'Mr. Kim', 'ESG Manager', 'Sustainability', 'kim@hanyang.demo', '+82-54-000-0001', TRUE, 'ko'),
('c2222222-0000-4000-8000-000000000002', 'f2222222-0000-4000-8000-000000000001', 'Li Manager', 'Li Manager', 'Compliance', 'Compliance', 'li@globalmining.demo', '+86-991-000-0002', TRUE, 'en'),
('c3333333-0000-4000-8000-000000000003', 'f3333333-0000-4000-8000-000000000001', '이담당', 'Ms. Lee', 'Quality', 'QA', 'lee@daesung.demo', '+82-31-000-0003', TRUE, 'ko');

-- 온보딩 / SLA (한양=동의완료, 대성=진행중 SLA 임박, 보조=미응답 escalation 대상)
INSERT INTO supplier_onboarding (supplier_id, consent_status, consent_signed_at, agreement_status, last_invited_at, sla_due_date, reminder_count) VALUES
('c1111111-0000-4000-8000-000000000001', 'consent_agreed',   now() - interval '20 days', 'agreed',  now() - interval '21 days', now() - interval '7 days',  0),
('c3333333-0000-4000-8000-000000000003', 'consent_agreed',   now() - interval '5 days',  'agreed',  now() - interval '6 days',  now() + interval '8 days',  1),
('c4444444-0000-4000-8000-000000000004', 'consent_pending',  NULL,                        'pending', now() - interval '22 days', now() - interval '8 days',  3);

-- 일반 인증서 (ISO 등) + 만료 임박 1건
INSERT INTO supplier_certifications (supplier_id, certification_type, certification_no, issued_at, expires_at, issuing_body) VALUES
('c1111111-0000-4000-8000-000000000001', 'ISO 14001', 'ISO-14001-HY-2023', '2023-01-01', '2026-12-31', 'KAB'),
('c2222222-0000-4000-8000-000000000002', 'Bettercoal',  'BC-GMC-2022',       '2022-06-01', now()::date + 20, 'Bettercoal');


-- ============================================================
-- 3. Provider Type CTI 상세 (영역 3)
-- ============================================================
-- 한양셀: 제조 탄소집약도 (EU 배터리법 Art.7) — 정상값
INSERT INTO supplier_manufacturer_details (supplier_id, manufacturing_process, energy_source, capacity, carbon_intensity) VALUES
('c1111111-0000-4000-8000-000000000001', 'NCM811 Cell Assembly', 'renewable', '10GWh/yr', 2.3400);
-- 대성정밀: 일부 필드 누락(저신뢰 파싱 시나리오의 원인) — energy_source NULL
INSERT INTO supplier_manufacturer_details (supplier_id, manufacturing_process, energy_source, capacity, carbon_intensity) VALUES
('c3333333-0000-4000-8000-000000000003', 'Module Casing', NULL, '2GWh/yr', NULL);

-- Global Mining: 광산 상세 + 신장 좌표
INSERT INTO supplier_miner_details (supplier_id, mine_name, mining_method, extraction_volume, mine_coordinates, active_period_from) VALUES
('c2222222-0000-4000-8000-000000000002', 'Xinjiang Lithium Mine A', 'open_pit', 50000.00, ST_SetSRID(ST_MakePoint(86.000, 41.000), 4326), '2020-01-01');

-- 트레이더: 공개율 낮음 (DPP 발행 차단 트리거 가능)
INSERT INTO supplier_trader_details (supplier_id, trading_license, broker_certification, disclosure_completeness) VALUES
('c4444444-0000-4000-8000-000000000004', 'TR-LIC-2023', NULL, 45.00);

INSERT INTO trader_disclosure_obligation (trader_supplier_id, upstream_supplier_id, disclosure_completeness, last_audited_at) VALUES
('c4444444-0000-4000-8000-000000000004', 'c2222222-0000-4000-8000-000000000002', 45.00, now() - interval '10 days');


-- ============================================================
-- 4. 리스크 프로필 (영역 4)
-- ============================================================
-- 한양: 저위험 / Global Mining: critical(FEOC ineligible) / 대성: medium
INSERT INTO supplier_risk_profiles (supplier_id, overall_risk_score, risk_level, feoc_status, feoc_direct_ownership, is_high_risk_flag, high_risk_reasons, last_risk_review_at) VALUES
('c1111111-0000-4000-8000-000000000001', 10, 'low',      'eligible',   0.00,  FALSE, NULL, now() - interval '7 days'),
('c2222222-0000-4000-8000-000000000002', 80, 'critical', 'ineligible', 28.50, TRUE,  '["FEOC 우려국 지분 28.5%","신장 인접 광산"]'::jsonb, now() - interval '2 days'),
('c3333333-0000-4000-8000-000000000003', 35, 'medium',   'under_review', 0.00, FALSE, '["자료 완성도 미흡"]'::jsonb, now() - interval '3 days');

-- 실사 기록 (v_action_items 'DD' 소스) — Global Mining 보완 필요
INSERT INTO supplier_audit_records (supplier_id, audit_date, audit_type, auditor, audit_status, inspector_id, result, next_audit_due) VALUES
('c2222222-0000-4000-8000-000000000002', now()::date - 30, 'on_site', 'Third Party Auditor', 'in_progress', '11111111-0000-4000-8000-000000000002', 'pending', now()::date + 30);

-- 인권 이슈 (Global Mining 강제노동 의혹 — UFLPA 위반 근거)
INSERT INTO supplier_human_rights_issues (supplier_id, factory_id, issue_type, severity, description, detected_at, status, source) VALUES
('c2222222-0000-4000-8000-000000000002', 'f2222222-0000-4000-8000-000000000001', 'forced_labor', 'critical', '신장 지역 강제노동 의혹', now() - interval '40 days', 'open', 'NGO Report');

-- 산재 (조사중 1건 — Readiness 차단 항목)
INSERT INTO supplier_industrial_accidents (supplier_id, factory_id, accident_date, accident_type, description, casualties, status) VALUES
('c2222222-0000-4000-8000-000000000002', 'f2222222-0000-4000-8000-000000000001', now()::date - 15, 'serious_injury', '광산 붕괴 사고', 2, 'investigating');


-- ============================================================
-- 5. 원산지 증명서 수집 (영역 5)
-- ============================================================
-- 한양: 유효 / Global Mining: 만료 임박(Readiness 감점) / 대성: 검토중
INSERT INTO origin_certificates (supplier_id, factory_id, cert_type, cert_number, issuing_authority, issued_at, expires_at, origin_country, status) VALUES
('c1111111-0000-4000-8000-000000000001', 'f1111111-0000-4000-8000-000000000001', 'CONFLICT_FREE', 'CF-HY-2024', 'RMI', '2024-06-01', now()::date + 200, 'KR', 'valid'),
('c2222222-0000-4000-8000-000000000002', 'f2222222-0000-4000-8000-000000000001', 'UFLPA_REBUTTAL', 'UF-GMC-2024', 'Self', '2024-01-01', now()::date + 15, 'CN', 'expiring_soon'),
('c3333333-0000-4000-8000-000000000003', 'f3333333-0000-4000-8000-000000000001', 'GENERAL', 'GEN-DS-2024', 'KCCI', '2024-03-01', now()::date + 100, 'KR', 'under_review');


-- ============================================================
-- 6. 교육 관리 (영역 6)
-- ============================================================
INSERT INTO training_materials (material_id, title, title_en, category, format, duration_minutes, required_for, version) VALUES
('a1111111-0000-4000-8000-0000000000a1', '인권 실사 교육', 'Human Rights DD', 'human_rights', 'online', 60, '["CSDDD"]'::jsonb, 'v1'),
('a1111111-0000-4000-8000-0000000000a2', '분쟁광물 교육',  'Conflict Minerals', 'conflict_minerals', 'video', 30, '["CONFLICT_MINERALS"]'::jsonb, 'v1');

-- 한양: 이수완료 / Global Mining: 기한초과(미이수 — Readiness 차단)
INSERT INTO training_records (supplier_id, factory_id, material_id, trainee_count, total_eligible, completion_rate, completed_at, due_date, status) VALUES
('c1111111-0000-4000-8000-000000000001', 'f1111111-0000-4000-8000-000000000001', 'a1111111-0000-4000-8000-0000000000a1', 50, 50, 100.00, now() - interval '10 days', now()::date - 5, 'completed'),
('c2222222-0000-4000-8000-000000000002', 'f2222222-0000-4000-8000-000000000001', 'a1111111-0000-4000-8000-0000000000a1', 5, 40, 12.50, NULL, now()::date - 10, 'overdue');


-- ============================================================
-- 7. 제품 / BOM / 부품 (영역 7) — Ingest 복사본 (source_system 필수)
-- ============================================================
INSERT INTO products (product_id, product_code, product_name, manufacturer_id, type, source_system, external_id) VALUES
('d1111111-0000-4000-8000-000000000001', 'BAT-NCM811-100Ah', 'NCM811 High Capacity Battery', 'c1111111-0000-4000-8000-000000000001', 'battery_pack', 'ERP_PLM', 'ERP-PROD-0001');

INSERT INTO bom_versions (bom_version_id, product_id, version_number, status, source_system, external_id) VALUES
('e1111111-0000-4000-8000-000000000001', 'd1111111-0000-4000-8000-000000000001', '1.0', 'active', 'ERP_PLM', 'ERP-BOM-0001');

-- 5계층 부품 트리 (Pack→Module→Cell→전구체→광물)
INSERT INTO parts (part_id, part_code, part_name, tier_level, parent_part_id, hs_code, material_type, unit_price, source_system, external_id) VALUES
('b1111111-0000-4000-8000-000000000001', 'PACK-NCM811', 'Battery Pack',   1, NULL,                                     '850760', 'assembly',  1000.0000, 'ERP_PLM', 'ERP-PART-PACK'),
('b1111111-0000-4000-8000-000000000002', 'MOD-NCM811',  'Module',         2, 'b1111111-0000-4000-8000-000000000001', '850760', 'assembly',   400.0000, 'ERP_PLM', 'ERP-PART-MOD'),
('b1111111-0000-4000-8000-000000000003', 'CELL-NCM811', 'Battery Cell',   3, 'b1111111-0000-4000-8000-000000000002', '850760', 'cell',       150.0000, 'ERP_PLM', 'ERP-PART-CELL'),
('b1111111-0000-4000-8000-000000000004', 'PRE-NCM',     'NCM Precursor',  4, 'b1111111-0000-4000-8000-000000000003', '382490', 'precursor',   40.0000, 'ERP_PLM', 'ERP-PART-PRE'),
('b1111111-0000-4000-8000-000000000005', 'MIN-LITHIUM', 'Raw Lithium',    5, 'b1111111-0000-4000-8000-000000000004', '283691', 'mineral',     20.0000, 'ERP_PLM', 'ERP-PART-LI');

INSERT INTO bom_items (bom_version_id, part_id, required_quantity, required_quantity_unit, percentage, direct_material_cost, origin_country, source_system, external_id) VALUES
('e1111111-0000-4000-8000-000000000001', 'b1111111-0000-4000-8000-000000000003', 100, 'ea', 60.00, 150.0000, 'KR', 'ERP_PLM', 'ERP-BI-CELL'),
('e1111111-0000-4000-8000-000000000001', 'b1111111-0000-4000-8000-000000000005', 50,  'kg', 40.00,  20.0000, 'CN', 'ERP_PLM', 'ERP-BI-LI');

-- 협력사↔원청 코드 매핑
INSERT INTO part_code_mapping (part_id, supplier_id, supplier_part_code, original_part_code) VALUES
('b1111111-0000-4000-8000-000000000003', 'c1111111-0000-4000-8000-000000000001', 'HY-CELL-001', 'CELL-NCM811'),
('b1111111-0000-4000-8000-000000000005', 'c2222222-0000-4000-8000-000000000002', 'GMC-LI-001',  'MIN-LITHIUM');

-- 공정 (CSDDD 추적)
INSERT INTO manufacturing_process (part_id, sequence_no, process_name, is_outsourced) VALUES
('b1111111-0000-4000-8000-000000000003', 1, 'Cell Coating', FALSE),
('b1111111-0000-4000-8000-000000000003', 2, 'Cell Assembly', FALSE);


-- ============================================================
-- 8. 공급망 맵 (영역 8) — 원청→한양→Global Mining, 한양→대성/트레이더
-- ============================================================
INSERT INTO supply_chain_map (map_id, bom_version_id, parent_supplier_id, child_supplier_id, part_id, link_status, source_system, verification_status) VALUES
-- 원청 → 한양셀 (셀 납품, 확정)
('5c111111-0000-4000-8000-000000000001', 'e1111111-0000-4000-8000-000000000001', NULL,                                     'c1111111-0000-4000-8000-000000000001', 'b1111111-0000-4000-8000-000000000003', 'supplychain_confirmed', 'ERP',             'verified'),
-- 한양셀 → Global Mining (리튬 납품, 확정)
('5c222222-0000-4000-8000-000000000002', 'e1111111-0000-4000-8000-000000000001', 'c1111111-0000-4000-8000-000000000001', 'c2222222-0000-4000-8000-000000000002', 'b1111111-0000-4000-8000-000000000005', 'supplychain_confirmed', 'SUPPLIER_DECLARED', 'verified'),
-- 한양셀 → 대성정밀 (모듈, 선언만 — Gray)
('5c333333-0000-4000-8000-000000000003', 'e1111111-0000-4000-8000-000000000001', 'c1111111-0000-4000-8000-000000000001', 'c3333333-0000-4000-8000-000000000003', 'b1111111-0000-4000-8000-000000000002', 'supplychain_declared',  'SUPPLIER_DECLARED', 'unverified'),
-- 한양셀 → 트레이더 (전구체, 선언만)
('5c444444-0000-4000-8000-000000000004', 'e1111111-0000-4000-8000-000000000001', 'c1111111-0000-4000-8000-000000000001', 'c4444444-0000-4000-8000-000000000004', 'b1111111-0000-4000-8000-000000000004', 'supplychain_declared',  'SUPPLIER_DECLARED', 'unverified');

-- 분할 납품 비율
INSERT INTO supply_ratio (map_id, factory_id, ratio_percentage, volume, unit) VALUES
('5c111111-0000-4000-8000-000000000001', 'f1111111-0000-4000-8000-000000000001', 70.00, 7000, 'ea'),
('5c111111-0000-4000-8000-000000000001', 'f1111111-0000-4000-8000-000000000002', 30.00, 3000, 'ea');


-- ============================================================
-- 9. 운영 / 배치 / DPP (영역 9) — 3종 배치
-- ============================================================
-- [Happy] EU向 배치 — readiness 단계 통과 → 발행 완료
INSERT INTO batches (batch_id, product_id, bom_version_id, tenant_id, destination, current_stage, status, confidence_score, source_system, external_id) VALUES
('ba111111-0000-4000-8000-000000000001', 'd1111111-0000-4000-8000-000000000001', 'e1111111-0000-4000-8000-000000000001', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'EU', 'stage_issuance', 'batch_completed', 0.9600, 'MES', 'MES-LOT-HAPPY');
-- [Sad] US向 배치 — risk 70+ → HITL 대기(반려 예정)
INSERT INTO batches (batch_id, product_id, bom_version_id, tenant_id, destination, current_stage, status, confidence_score, source_system, external_id) VALUES
('ba222222-0000-4000-8000-000000000002', 'd1111111-0000-4000-8000-000000000001', 'e1111111-0000-4000-8000-000000000001', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'US', 'stage_risk', 'batch_hitl_wait', 0.9100, 'MES', 'MES-LOT-SAD');
-- [Gray] EU向 배치 — 저신뢰 + needs_human_review → HITL 대기
INSERT INTO batches (batch_id, product_id, bom_version_id, tenant_id, destination, current_stage, status, confidence_score, source_system, external_id) VALUES
('ba333333-0000-4000-8000-000000000003', 'd1111111-0000-4000-8000-000000000001', 'e1111111-0000-4000-8000-000000000001', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'EU', 'stage_compliance', 'batch_hitl_wait', 0.7200, 'MES', 'MES-LOT-GRAY');

-- DPP (Happy만 발행 완료 — issued/immutable)
INSERT INTO dpp_records (dpp_id, batch_id, product_id, issued_at, status, carbon_footprint, recycled_content, qr_code_url, payload, approved_by) VALUES
('dccc1111-0000-4000-8000-000000000001', 'ba111111-0000-4000-8000-000000000001', 'd1111111-0000-4000-8000-000000000001', now() - interval '1 day', 'dpp_issued', 12.3400, '{"Co":15,"Ni":12,"Li":8}'::jsonb, 'https://dpp.kira.demo/qr/happy', '{"readiness_breakdown":{"all_tier_completion":true,"no_violation":true,"trader_disclosure":true}}'::jsonb, '11111111-0000-4000-8000-000000000002');


-- ============================================================
-- 10. 규제 / 컴플라이언스 (영역 10) — compliance_results (verdict)
-- ============================================================
-- regulations 마스터는 schema가 적재. 여기서는 배치별 판정 결과만.
-- regulation_id는 코드로 조회(schema 적재분과 조인).
-- [Happy] 한양 EU 규제 통과
INSERT INTO compliance_results (batch_id, regulation_id, supplier_id, verdict, needs_human_review, cited_clauses, confidence_score, reasoning_text)
SELECT 'ba111111-0000-4000-8000-000000000001', regulation_id, 'c1111111-0000-4000-8000-000000000001', 'compliance_passed', FALSE, '["EU 2023/1542 Art.7"]'::jsonb, 0.96, '탄소발자국 신고 정상'
FROM regulations WHERE regulation_code = 'EU_BATTERY_ART7';

-- [Sad] Global Mining UFLPA 위반
INSERT INTO compliance_results (batch_id, regulation_id, supplier_id, verdict, needs_human_review, cited_clauses, confidence_score, reasoning_text)
SELECT 'ba222222-0000-4000-8000-000000000002', regulation_id, 'c2222222-0000-4000-8000-000000000002', 'compliance_violation', FALSE, '["UFLPA Sec.3"]'::jsonb, 0.93, '신장 강제노동 의혹 — 위반'
FROM regulations WHERE regulation_code = 'UFLPA';

-- [Gray] 대성정밀 EU_BATTERY 회색지대 (needs_human_review=TRUE)
INSERT INTO compliance_results (batch_id, regulation_id, supplier_id, verdict, needs_human_review, cited_clauses, confidence_score, reasoning_text)
SELECT 'ba333333-0000-4000-8000-000000000003', regulation_id, 'c3333333-0000-4000-8000-000000000003', 'compliance_warning', TRUE, '["EU 2023/1542"]'::jsonb, 0.72, '자료 미비로 판단 보류 — 사람 검토 필요'
FROM regulations WHERE regulation_code = 'EU_BATTERY';


-- ============================================================
-- 11. 데이터 흐름 / Submission (영역 11)
-- ============================================================
-- 데이터 요청 (한양=승인, 대성=재작업, 트레이더=미응답 escalation)
INSERT INTO data_request_log (request_id, requester_user_id, target_supplier_id, requested_data_type, requested_at, due_date, response_status, submission_status) VALUES
('da111111-0000-4000-8000-000000000001', '11111111-0000-4000-8000-000000000002', 'c1111111-0000-4000-8000-000000000001', '탄소발자국 증빙', now() - interval '15 days', now() - interval '1 day', 'response_responded', 'submission_approved'),
('da333333-0000-4000-8000-000000000003', '11111111-0000-4000-8000-000000000002', 'c3333333-0000-4000-8000-000000000003', '공장 정보',       now() - interval '6 days',  now() + interval '8 days', 'response_responded', 'submission_rework'),
('da444444-0000-4000-8000-000000000004', '11111111-0000-4000-8000-000000000002', 'c4444444-0000-4000-8000-000000000004', '원산지 증빙',     now() - interval '22 days', now() - interval '8 days', 'response_escalated', 'submission_requested');

-- 업로드 문서 (한양 1건, 대성 1건 — 파싱 대상)
INSERT INTO submission_documents (document_id, request_id, supplier_id, file_url, file_name, file_type, doc_category, file_hash, uploaded_by) VALUES
('d0c11111-0000-4000-8000-000000000001', 'da111111-0000-4000-8000-000000000001', 'c1111111-0000-4000-8000-000000000001', 'https://files.kira.demo/hy_carbon.pdf', 'hy_carbon.pdf', 'pdf', 'carbon_data', 'a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90', '11111111-0000-4000-8000-000000000004'),
('d0c33333-0000-4000-8000-000000000003', 'da333333-0000-4000-8000-000000000003', 'c3333333-0000-4000-8000-000000000003', 'https://files.kira.demo/ds_factory.xlsx', 'ds_factory.xlsx', 'xlsx', 'factory_doc', 'b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90a1', '11111111-0000-4000-8000-000000000005');

-- AI 파싱 결과 (한양=고신뢰 확정, 대성=저신뢰 미확정 → Gray 확인 루프)
INSERT INTO document_extraction_results (request_id, document_id, parsed_fields, confidence_map, unparsed_fields, supplier_confirmed, confirmed_at) VALUES
('da111111-0000-4000-8000-000000000001', 'd0c11111-0000-4000-8000-000000000001', '{"carbon_intensity":2.34,"energy_source":"renewable"}'::jsonb, '{"carbon_intensity":0.96,"energy_source":0.91}'::jsonb, '[]'::jsonb, TRUE, now() - interval '2 days'),
('da333333-0000-4000-8000-000000000003', 'd0c33333-0000-4000-8000-000000000003', '{"factory_name":"화성 공장","capacity":"2GWh"}'::jsonb, '{"factory_name":0.95,"capacity":0.62}'::jsonb, '["energy_source"]'::jsonb, FALSE, NULL);

-- 제출 상태 이력 (Timeline)
INSERT INTO submission_status_history (request_id, from_status, to_status, actor_id, reason) VALUES
('da111111-0000-4000-8000-000000000001', 'submission_submitted', 'submission_approved', '11111111-0000-4000-8000-000000000002', '검토 통과'),
('da333333-0000-4000-8000-000000000003', 'submission_review',    'submission_rework',  '11111111-0000-4000-8000-000000000002', '자료 보완 요청');

-- 완성도 카운트
INSERT INTO data_completeness_status (entity_type, entity_id, required_field_count, filled_field_count, completion_rate, missing_fields, last_updated_by) VALUES
('supplier', 'c1111111-0000-4000-8000-000000000001', 12, 11, 91.67, '[]'::jsonb, '11111111-0000-4000-8000-000000000002'),
('supplier', 'c3333333-0000-4000-8000-000000000003', 12, 7,  58.33, '["energy_source","cert"]'::jsonb, '11111111-0000-4000-8000-000000000002');

-- 알림 (멱등 dedup_key)
INSERT INTO notifications (user_id, channel, notification_type, subject, body, status, dedup_key) VALUES
('11111111-0000-4000-8000-000000000005', 'email', 'sla_warning', 'SLA 임박', '원산지 증빙 제출 기한이 지났습니다', 'pending', 'sla_reminder:da444444:2026-05-29');


-- ============================================================
-- 12. 감사 추적 / HITL (영역 12)
-- ============================================================
-- HITL 검토 (Sad=risk_escalated 반려 예정, Gray=gray_zone 검토 대기)
INSERT INTO hitl_reviews (review_id, batch_id, reason, trigger_stage, assigned_to, status) VALUES
('41111111-0000-4000-8000-000000000001', 'ba222222-0000-4000-8000-000000000002', 'risk_escalated', 'stage_risk', '11111111-0000-4000-8000-000000000002', 'hitl_pending'),
('41111111-0000-4000-8000-000000000002', 'ba333333-0000-4000-8000-000000000003', 'gray_zone',      'stage_compliance', '11111111-0000-4000-8000-000000000002', 'hitl_pending');

-- 감사 추적 해시 체인 (Happy 배치 — 발행까지 단계 기록 최소 예시)
INSERT INTO audit_trail (batch_id, step_number, node_type, node_name, input_hash, output_hash, prev_hash, duration_ms) VALUES
('ba111111-0000-4000-8000-000000000001', 1, 'agent', 'data_gateway',  '0000000000000000000000000000000000000000000000000000000000000001', '0000000000000000000000000000000000000000000000000000000000000002', NULL, 120),
('ba111111-0000-4000-8000-000000000001', 2, 'agent', 'compliance',    '0000000000000000000000000000000000000000000000000000000000000002', '0000000000000000000000000000000000000000000000000000000000000003', '0000000000000000000000000000000000000000000000000000000000000002', 340);
