-- ==========================================
-- KIRA 플랫폼 통합 테스트용 초기 시드 데이터
-- 반드시 schema.sql 실행 후 적재해야 합니다.
-- ==========================================

-- 1. 테넌트 및 사용자 (영역 1)
INSERT INTO tenants (tenant_id, company_name, business_reg_no, subscription_status)
VALUES ('a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'KIRA_Demo_Tenant', '123-45-67890', 'active');

INSERT INTO users (tenant_id, email, password_hash, name, role)
VALUES ('a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'admin@kira.demo', 'hashed_pw', 'Admin User', 'admin');

-- 2. 규제 마스터 (영역 10) - Compliance Agent 테스트용
INSERT INTO regulations (regulation_id, name, regulation_code, region, version, effective_from, description)
VALUES 
('b1eebc99-1111-4ef8-bb6d-6bb9bd380a22', 'EU Deforestation Regulation', 'EUDR', 'EU', '2023/1115', '2024-12-30', 'EU 산림파괴방지법'),
('b2eebc99-2222-4ef8-bb6d-6bb9bd380a33', 'Uyghur Forced Labor Prevention Act', 'UFLPA', 'US', 'v1', '2022-06-21', '미국 위구르 강제노동 방지법');

-- 3. 협력사 및 공장 마스터 (영역 2)
-- 1차 협력사 (배터리 셀 제조사)
INSERT INTO suppliers (supplier_id, tenant_id, company_name, supplier_type, tier, status)
VALUES ('c1eebc99-3333-4ef8-bb6d-6bb9bd380a44', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '한양셀 제조(주)', 'manufacturer', 1, 'verified');

INSERT INTO supplier_factories (factory_id, supplier_id, factory_name, country, factory_role)
VALUES ('f1eebc99-4444-4ef8-bb6d-6bb9bd380a55', 'c1eebc99-3333-4ef8-bb6d-6bb9bd380a44', '포항 제1공장', 'KR', 'production');

-- 2차 협력사 (광산)
INSERT INTO suppliers (supplier_id, tenant_id, company_name, supplier_type, tier, status, risk_level)
VALUES ('c2eebc99-5555-4ef8-bb6d-6bb9bd380a66', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'Global Mining Corp', 'miner', 2, 'review', 'high');

-- 4. 제품 및 부품 (영역 7)
INSERT INTO products (product_id, product_code, product_name)
VALUES ('d1eebc99-6666-4ef8-bb6d-6bb9bd380a77', 'BAT-NCM811-100Ah', 'NCM811 High Capacity Battery');

INSERT INTO bom_versions (bom_version_id, product_id, version_number, status)
VALUES ('e1eebc99-7777-4ef8-bb6d-6bb9bd380a88', 'd1eebc99-6666-4ef8-bb6d-6bb9bd380a77', '1.0', 'active');

INSERT INTO parts (part_id, part_code, part_name, tier_level, hs_code)
VALUES 
('f1eebc99-8888-4ef8-bb6d-6bb9bd380a99', 'CELL-NCM811', 'Battery Cell', 1, '850760'),
('f2eebc99-9999-4ef8-bb6d-6bb9bd380aaa', 'MIN-LITHIUM', 'Raw Lithium', 2, '283691');

-- 5. 공급망 맵 (영역 8)
INSERT INTO supply_chain_map (map_id, bom_version_id, parent_supplier_id, child_supplier_id, part_id)
VALUES 
-- 원청사 -> 1차 협력사 (셀 납품)
('a1eebc99-aaaa-4ef8-bb6d-6bb9bd380abb', 'e1eebc99-7777-4ef8-bb6d-6bb9bd380a88', NULL, 'c1eebc99-3333-4ef8-bb6d-6bb9bd380a44', 'f1eebc99-8888-4ef8-bb6d-6bb9bd380a99'),
-- 1차 협력사 -> 2차 협력사 (리튬 납품)
('a2eebc99-bbbb-4ef8-bb6d-6bb9bd380acc', 'e1eebc99-7777-4ef8-bb6d-6bb9bd380a88', 'c1eebc99-3333-4ef8-bb6d-6bb9bd380a44', 'c2eebc99-5555-4ef8-bb6d-6bb9bd380a66', 'f2eebc99-9999-4ef8-bb6d-6bb9bd380aaa');