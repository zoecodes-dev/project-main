-- KIRA 프로젝트 필수 확장 모듈 활성화
-- ============================================================
-- KIRA Compliance Intelligence Platform
-- 통합 초기화 스크립트 (확장 + 스키마 + 시드 데이터)
-- ============================================================

-- 1. 확장 활성화
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 2. 영역 1. 테넌트 / 사용자 / 권한
CREATE TABLE tenants (
    tenant_id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_name        VARCHAR(255) NOT NULL,
    business_reg_no     VARCHAR(50)  UNIQUE,
    subscription_status VARCHAR(20)  DEFAULT 'active',
    joined_at           TIMESTAMPTZ  DEFAULT now(),
    created_at          TIMESTAMPTZ  DEFAULT now(),
    updated_at          TIMESTAMPTZ  DEFAULT now()
);

CREATE TABLE users (
    user_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id      UUID REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    email          VARCHAR(255) UNIQUE NOT NULL,
    password_hash  VARCHAR(255) NOT NULL,
    name           VARCHAR(100),
    role           VARCHAR(50),
    is_active      BOOLEAN DEFAULT TRUE,
    last_login_at  TIMESTAMPTZ,
    created_at     TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE view_permissions (
    permission_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id              UUID REFERENCES users(user_id) ON DELETE CASCADE,
    viewable_supplier_id UUID,
    can_view_parent      BOOLEAN DEFAULT FALSE,
    can_view_children    BOOLEAN DEFAULT FALSE,
    can_view_siblings    BOOLEAN DEFAULT FALSE,
    depth_limit          INT DEFAULT 1,
    granted_by           UUID REFERENCES users(user_id),
    granted_at           TIMESTAMPTZ DEFAULT now()
);

-- 3. 영역 2. 협력사 마스터
CREATE TABLE suppliers (
    supplier_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id           UUID REFERENCES tenants(tenant_id),
    company_name        VARCHAR(255) NOT NULL,
    company_name_en     VARCHAR(255),
    company_name_ko     VARCHAR(255),
    short_name_en       VARCHAR(100),
    short_name_ko       VARCHAR(100),
    ceo_name            VARCHAR(100),
    business_reg_no     VARCHAR(50),
    corporate_reg_no    VARCHAR(50),
    duns_number         VARCHAR(20),
    tax_number          VARCHAR(50),
    website             VARCHAR(255),
    supplier_type       VARCHAR(30) NOT NULL,
    tier                INT,
    parent_supplier_id  UUID REFERENCES suppliers(supplier_id),
    established_year    INT,
    employee_count      INT,
    completeness_score  INT DEFAULT 0,
    status              VARCHAR(20) DEFAULT 'pending',
    risk_level          VARCHAR(20) DEFAULT 'low',
    feoc_status         VARCHAR(20) DEFAULT 'unknown',
    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE supplier_factories (
    factory_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id   UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    factory_name     VARCHAR(255),
    factory_name_en  VARCHAR(255),
    address       TEXT,
    country       VARCHAR(2),
    region        VARCHAR(100),
    location      GEOMETRY(POINT, 4326),
    factory_role  VARCHAR(30),
    is_active     BOOLEAN DEFAULT TRUE,
    operating_period_from DATE,
    operating_period_to   DATE,
    monthly_capacity      VARCHAR(100),
    destination           VARCHAR(10),
    destination_detail    TEXT,
    applicable_regulations JSONB,
    hidden_regulations    JSONB,
    supply_ratio_percent  NUMERIC(5,2),
    supply_quantity       VARCHAR(100),
    created_at    TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE supplier_contacts (
    contact_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id   UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    factory_id    UUID REFERENCES supplier_factories(factory_id),
    name          VARCHAR(100),
    name_en       VARCHAR(100),
    role          VARCHAR(50),
    department    VARCHAR(100),
    email         VARCHAR(255),
    phone         VARCHAR(50),
    mobile        VARCHAR(50),
    is_primary    BOOLEAN DEFAULT FALSE,
    language      VARCHAR(50)
);

CREATE TABLE supplier_onboarding (
    onboarding_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id         UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    consent_status      VARCHAR(20) DEFAULT 'pending',
    consent_signed_at   TIMESTAMPTZ,
    agreement_status    VARCHAR(20) DEFAULT 'pending',
    agreement_signed_at TIMESTAMPTZ,
    last_invited_at     TIMESTAMPTZ,
    last_reminded_at    TIMESTAMPTZ,
    sla_due_date        TIMESTAMPTZ,
    reminder_count      INT DEFAULT 0
);

CREATE TABLE supplier_certifications (
    cert_id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id        UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    certification_type VARCHAR(100),
    certification_no   VARCHAR(100),
    issued_at          DATE,
    expires_at         DATE,
    issuing_body       VARCHAR(255),
    document_url       VARCHAR(500)
);

-- 4. 영역 3. Provider Type별 상세 (CTI)
CREATE TABLE supplier_manufacturer_details (
    detail_id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id           UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    manufacturing_process TEXT,
    energy_source         VARCHAR(100),
    capacity              VARCHAR(100),
    carbon_intensity      NUMERIC(10,4)
);

CREATE TABLE supplier_recycler_details (
    detail_id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id             UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    recycled_materials      JSONB,
    recycling_certification VARCHAR(255),
    input_source            VARCHAR(50),
    recycled_content_ratio  NUMERIC(5,2)
);

CREATE TABLE supplier_trader_details (
    detail_id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id             UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    trading_license         VARCHAR(100),
    broker_certification    VARCHAR(255),
    disclosure_completeness NUMERIC(5,2) DEFAULT 0
);

CREATE TABLE supplier_miner_details (
    detail_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id        UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    mine_name          VARCHAR(255),
    mining_method      VARCHAR(50),
    extraction_volume  NUMERIC(15,2),
    mine_coordinates   GEOMETRY(POINT, 4326),
    active_period_from DATE,
    active_period_to   DATE
);

CREATE TABLE trader_disclosure_obligation (
    obligation_id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    trader_supplier_id      UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    upstream_supplier_id    UUID REFERENCES suppliers(supplier_id),
    disclosure_completeness NUMERIC(5,2),
    last_audited_at         TIMESTAMPTZ
);

-- 5. 영역 4. 협력사 리스크 프로필
CREATE TABLE supplier_risk_profiles (
    profile_id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id             UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    overall_risk_score      INT DEFAULT 0,
    risk_level              VARCHAR(20) DEFAULT 'low',
    feoc_status             VARCHAR(20) DEFAULT 'unknown',
    feoc_direct_ownership   NUMERIC(5,2),
    feoc_indirect_ownership NUMERIC(5,2),
    feoc_last_assessed_at   TIMESTAMPTZ,
    feoc_cert_expiry        DATE,
    is_high_risk_flag       BOOLEAN DEFAULT FALSE,
    high_risk_reasons       JSONB,
    last_risk_review_at     TIMESTAMPTZ,
    created_at              TIMESTAMPTZ DEFAULT now(),
    updated_at              TIMESTAMPTZ DEFAULT now(),
    UNIQUE(supplier_id)
);

CREATE TABLE supplier_audit_records (
    audit_record_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id        UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    audit_date         DATE NOT NULL,
    audit_type         VARCHAR(30),
    auditor            VARCHAR(255),
    audit_scope        TEXT,
    result             VARCHAR(30),
    findings           JSONB,
    corrective_actions JSONB,
    next_audit_due     DATE,
    report_url         VARCHAR(500),
    created_at         TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE supplier_human_rights_issues (
    issue_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    factory_id  UUID REFERENCES supplier_factories(factory_id),
    issue_type  VARCHAR(50),
    severity    VARCHAR(20),
    description TEXT,
    detected_at TIMESTAMPTZ,
    status      VARCHAR(30),
    source      VARCHAR(255),
    resolved_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE supplier_industrial_accidents (
    accident_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id       UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    factory_id        UUID REFERENCES supplier_factories(factory_id),
    accident_date     DATE NOT NULL,
    accident_type     VARCHAR(30),
    description       TEXT,
    casualties        INT DEFAULT 0,
    ltifr             NUMERIC(6,2),
    status            VARCHAR(20),
    corrective_action TEXT,
    created_at        TIMESTAMPTZ DEFAULT now()
);

-- 6. 영역 5. 원산지 증명서
CREATE TABLE origin_certificates (
    cert_id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id       UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    factory_id        UUID REFERENCES supplier_factories(factory_id),
    cert_type         VARCHAR(30) NOT NULL,
    cert_number       VARCHAR(100),
    issuing_authority VARCHAR(255),
    issued_at         DATE,
    expires_at        DATE NOT NULL,
    origin_country    VARCHAR(2),
    covered_minerals  JSONB,
    status            VARCHAR(20) DEFAULT 'valid',
    document_url      VARCHAR(500),
    created_at        TIMESTAMPTZ DEFAULT now(),
    updated_at        TIMESTAMPTZ DEFAULT now()
);

-- 7. 영역 7. 제품 / BOM / 부품
CREATE TABLE products (
    product_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    product_code    VARCHAR(50) UNIQUE NOT NULL,
    product_name    VARCHAR(255),
    manufacturer_id UUID REFERENCES suppliers(supplier_id),
    type            VARCHAR(50),
    specs           JSONB,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE bom_versions (
    bom_version_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    product_id     UUID REFERENCES products(product_id) ON DELETE CASCADE,
    version_number VARCHAR(20) NOT NULL,
    effective_from DATE,
    effective_to   DATE,
    status         VARCHAR(20) DEFAULT 'draft',
    approved_by    UUID REFERENCES users(user_id),
    approved_at    TIMESTAMPTZ,
    created_at     TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE parts (
    part_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    part_code        VARCHAR(50) UNIQUE NOT NULL,
    part_name        VARCHAR(255),
    tier_level       INT,
    parent_part_id   UUID REFERENCES parts(part_id),
    hs_code          VARCHAR(15),
    material_type    VARCHAR(100),
    function_purpose TEXT,
    unit_price       NUMERIC(15,4),
    purchase_unit    VARCHAR(20),
    specs            JSONB,
    created_at       TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE bom_items (
    bom_item_id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    bom_version_id         UUID REFERENCES bom_versions(bom_version_id) ON DELETE CASCADE,
    part_id                UUID REFERENCES parts(part_id),
    required_quantity      NUMERIC(15,4),
    required_quantity_unit VARCHAR(20),
    percentage             NUMERIC(5,2),
    direct_material_cost   NUMERIC(15,4),
    origin_country         VARCHAR(2)
);

-- 8. 영역 8. 공급망 맵
CREATE TABLE supply_chain_map (
    map_id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    bom_version_id     UUID REFERENCES bom_versions(bom_version_id),
    parent_supplier_id UUID REFERENCES suppliers(supplier_id),
    child_supplier_id  UUID REFERENCES suppliers(supplier_id),
    part_id            UUID REFERENCES parts(part_id),
    po_number          VARCHAR(50),
    invoice_number     VARCHAR(50),
    supply_period_from DATE,
    supply_period_to   DATE,
    created_at         TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE supply_ratio (
    ratio_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    map_id           UUID REFERENCES supply_chain_map(map_id) ON DELETE CASCADE,
    factory_id       UUID REFERENCES supplier_factories(factory_id),
    ratio_percentage NUMERIC(5,2),
    volume           NUMERIC(15,4),
    unit             VARCHAR(20)
);

-- 9. 영역 9. 운영 / 배치 / DPP
CREATE TABLE batches (
    batch_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    product_id       UUID REFERENCES products(product_id),
    bom_version_id   UUID REFERENCES bom_versions(bom_version_id),
    tenant_id        UUID REFERENCES tenants(tenant_id),
    received_at      TIMESTAMPTZ DEFAULT now(),
    destination      VARCHAR(2),
    current_stage    VARCHAR(50),
    status           VARCHAR(20),
    confidence_score NUMERIC(5,4)
);

CREATE TABLE dpp_records (
    dpp_id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    batch_id         UUID REFERENCES batches(batch_id),
    product_id       UUID REFERENCES products(product_id),
    issued_at        TIMESTAMPTZ,
    status           VARCHAR(20),
    carbon_footprint NUMERIC(10,4),
    recycled_content JSONB,
    qr_code_url      VARCHAR(500),
    payload          JSONB,
    approved_by      UUID REFERENCES users(user_id)
);

-- 10. 영역 10. 규제 / 컴플라이언스
CREATE TABLE regulations (
    regulation_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name             VARCHAR(100),
    regulation_code  VARCHAR(50) UNIQUE,
    region           VARCHAR(10),
    description      TEXT,
    version          VARCHAR(20),
    effective_from   DATE,
    document_s3_url  VARCHAR(500),
    embedding_status VARCHAR(20) DEFAULT 'pending',
    embedding        vector(1536)
);

CREATE TABLE compliance_results (
    result_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    batch_id         UUID REFERENCES batches(batch_id) ON DELETE CASCADE,
    regulation_id    UUID REFERENCES regulations(regulation_id),
    supplier_id      UUID REFERENCES suppliers(supplier_id),
    verdict          VARCHAR(20),
    cited_clauses    JSONB,
    confidence_score NUMERIC(5,4),
    reasoning_text   TEXT,
    created_at       TIMESTAMPTZ DEFAULT now()
);

-- 11. 영역 11. 데이터 흐름 추적 / Submission
CREATE TABLE data_request_log (
    request_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    requester_user_id   UUID REFERENCES users(user_id),
    target_supplier_id  UUID REFERENCES suppliers(supplier_id),
    requested_data_type VARCHAR(100),
    requested_at        TIMESTAMPTZ DEFAULT now(),
    due_date            TIMESTAMPTZ,
    response_status     VARCHAR(20) DEFAULT 'pending',
    reminder_count      INT DEFAULT 0,
    last_reminder_at    TIMESTAMPTZ,
    responded_at        TIMESTAMPTZ,
    submission_status   VARCHAR(20) DEFAULT 'pending'
);

CREATE TABLE submission_status_history (
    history_id  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    request_id  UUID REFERENCES data_request_log(request_id) ON DELETE CASCADE,
    from_status VARCHAR(20),
    to_status   VARCHAR(20) NOT NULL,
    actor_id    UUID REFERENCES users(user_id),
    reason      TEXT,
    changed_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE audit_trail (
    audit_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    batch_id       UUID REFERENCES batches(batch_id),
    step_number    INT,
    timestamp      TIMESTAMPTZ DEFAULT now(),
    node_type      VARCHAR(20),
    node_name      VARCHAR(100),
    model_version  VARCHAR(50),
    prompt_version VARCHAR(20),
    duration_ms    INT,
    input_hash     VARCHAR(64),
    output_hash    VARCHAR(64),
    prev_hash      VARCHAR(64),
    decision_text  TEXT,
    citations      JSONB
);

-- 12. 트리거 및 뷰
CREATE OR REPLACE FUNCTION prevent_issued_dpp_update()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.status = 'issued' THEN
        RAISE EXCEPTION 'DPP record % is already issued and cannot be modified.', OLD.dpp_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_dpp_immutable
    BEFORE UPDATE ON dpp_records
    FOR EACH ROW EXECUTE FUNCTION prevent_issued_dpp_update();

CREATE VIEW v_supply_chain_node_status AS
SELECT
    scm.map_id, scm.parent_supplier_id, scm.child_supplier_id, scm.part_id,
    s.company_name, s.company_name_en, s.supplier_type, s.tier, s.status AS supplier_status,
    s.risk_level, s.feoc_status, s.completeness_score, sf.country, sf.location,
    sf.applicable_regulations, drl.submission_status, drl.due_date, drl.response_status,
    CASE
        WHEN s.status = 'violation'                                THEN 'red'
        WHEN s.risk_level IN ('high', 'critical')                  THEN 'red'
        WHEN drl.submission_status = 'approved'                    THEN 'green'
        WHEN drl.submission_status IN ('submitted', 'review')      THEN 'yellow'
        WHEN drl.submission_status IN ('requested', 'in_progress') THEN 'blue'
        ELSE 'gray'
    END AS node_color
FROM supply_chain_map scm
JOIN suppliers s ON s.supplier_id = scm.child_supplier_id
LEFT JOIN supplier_factories sf ON sf.supplier_id = s.supplier_id AND sf.is_active = TRUE
LEFT JOIN data_request_log drl ON drl.target_supplier_id = s.supplier_id
   AND drl.response_status != 'responded'
   AND drl.requested_at = (SELECT MAX(d2.requested_at) FROM data_request_log d2 WHERE d2.target_supplier_id = s.supplier_id);

-- 13. 초기 마스터 데이터 적재 (테넌트 및 사용자 2명)
INSERT INTO tenants (tenant_id, company_name, business_reg_no)
VALUES ('a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'KIRA_Demo_Tenant', '123-45-67890');

INSERT INTO users (user_id, tenant_id, email, password_hash, name, role)
VALUES 
  ('b1feca00-9c0b-4ef8-bb6d-6bb9bd380a22', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'admin@kira.com', '$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjIQ68YIgW', 'Admin User', 'admin'),
  ('c2feda11-9c0b-4ef8-bb6d-6bb9bd380a33', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'supplier@kira.com', '$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjIQ68YIgW', 'Supplier User', 'supplier');

-- 14. 초기 시드 데이터 적재 (규제 12종)
INSERT INTO regulations (name, regulation_code, region, version, effective_from, description)
VALUES
  ('EU Deforestation Regulation',          'EUDR',              'EU', '2023/1115', '2024-12-30', 'EU 산림파괴방지법'),
  ('EUDR — FSC Certification',             'EUDR_FSC',          'EU', '2023/1115', '2024-12-30', 'EUDR 부속 FSC 인증'),
  ('Corporate Sustainability Due Diligence','CSDDD',            'EU', '2024/1760', '2027-01-01', 'EU 공급망 실사지침'),
  ('Uyghur Forced Labor Prevention Act',   'UFLPA',             'US', '2021',      '2022-06-21', '미국 위구르 강제노동방지법'),
  ('Inflation Reduction Act (FEOC)',        'IRA',               'US', '2022',      '2023-01-01', '미국 인플레이션감축법 FEOC'),
  ('EU Battery Regulation',                'EU_BATTERY',        'EU', '2023/1542', '2025-02-18', 'EU 배터리법 전체'),
  ('EU Battery Regulation Art.7',          'EU_BATTERY_ART7',   'EU', '2023/1542', '2025-02-18', '탄소발자국 신고 의무'),
  ('EU Battery Regulation Art.47',         'EU_BATTERY_ART47',  'EU', '2023/1542', '2027-08-18', '공급망 실사 DDP 수립'),
  ('Carbon Border Adjustment Mechanism',   'CBAM',              'EU', '2023/956',  '2026-01-01', 'EU 탄소국경조정제도'),
  ('EU Conflict Minerals Regulation',      'CONFLICT_MINERALS', 'EU', '2017/821',  '2021-01-01', 'EU 분쟁광물 규정'),
  ('Critical Raw Materials Act',           'CRMA',              'EU', '2024/1252', '2024-05-23', 'EU 핵심원자재법'),
  ('Lieferkettensorgfaltspflichtengesetz', 'LKSG',              'DE', '2021',      '2023-01-01', '독일 공급망실사법');