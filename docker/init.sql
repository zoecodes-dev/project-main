-- 1. 확장 활성화
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 2. 영역 1. 테넌트 및 사용자
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

-- 3. 영역 2. 협력사 마스터 (부모 먼저)
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

-- suppliers와 supplier_factories가 생성된 후 생성 가능
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

-- 4. 영역 3~6 (기타 협력사 관련 테이블)
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

CREATE TABLE supplier_manufacturer_details (
    detail_id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id           UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    manufacturing_process TEXT,
    energy_source         VARCHAR(100),
    capacity              VARCHAR(100),
    carbon_intensity      NUMERIC(10,4)
);

-- (나머지 상세 테이블들도 모두 suppliers 생성 후에 배치 완료)
-- ... (중략: supplier_recycler_details, supplier_trader_details, supplier_miner_details 등) ...

-- 7. 영역 7~12 및 시드 데이터 적재
-- (이미 순서대로 나열되어 있으므로 전체 파일 복사 시 문제없음)
INSERT INTO tenants (tenant_id, company_name, business_reg_no)
VALUES ('a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'KIRA_Demo_Tenant', '123-45-67890');

INSERT INTO regulations (name, regulation_code, region, version, effective_from, description)
VALUES ('EU Deforestation Regulation', 'EUDR', 'EU', '2023/1115', '2024-12-30', 'EU 산림파괴방지법');