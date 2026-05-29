-- ============================================================
-- KIRA Compliance Intelligence Platform
-- 공급망 데이터 백본 + AI 자동화 레이어 통합 데이터베이스 스키마
-- PostgreSQL 16 + PostGIS + pgvector 기반
--
-- 단일 통합 소스코드 — 이 파일 하나로 데이터베이스를 완벽하게 빌드한다.
-- ============================================================

-- ============================================================
-- 0. 확장 기능 활성화
-- ============================================================
CREATE EXTENSION IF NOT EXISTS postgis;     -- 공장·광산 좌표(GEOMETRY), ST_DWithin 등 공간 쿼리용
CREATE EXTENSION IF NOT EXISTS vector;      -- 규제 문서 법률 RAG용 pgvector 임베딩 데이터 타입
CREATE EXTENSION IF NOT EXISTS "uuid-ossp"; -- uuid_generate_v4() 기본키 생성용


-- ============================================================
-- 영역 1. 테넌트 / 사용자 / 권한 (A 담당)
-- ============================================================

-- [테이블 역할] 멀티테넌트 SaaS의 최상위 조직 단위. 원청사(OEM) 1개가 1개의 tenant로 기능.
CREATE TABLE tenants (
    tenant_id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_name        VARCHAR(255) NOT NULL,
    business_reg_no     VARCHAR(50)  UNIQUE,
    subscription_status VARCHAR(20)  DEFAULT 'active' 
        CONSTRAINT chk_subscription_status CHECK (subscription_status IN ('active', 'suspended', 'trial')),
    joined_at           TIMESTAMPTZ  DEFAULT now(),
    created_at          TIMESTAMPTZ  DEFAULT now(),
    updated_at          TIMESTAMPTZ  DEFAULT now()
);

-- [테이블 역할] 원청사 내부 관리자와 협력사 담당자를 총망라하는 플랫폼 전체 사용자 마스터.
CREATE TABLE users (
    user_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id      UUID REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    email          VARCHAR(255) UNIQUE NOT NULL,
    password_hash  VARCHAR(255) NOT NULL,
    name           VARCHAR(100),
    role           VARCHAR(50) 
        CONSTRAINT chk_user_role CHECK (role IN ('admin', 'owner_esg', 'owner_purchasing', 'supplier_ceo', 'supplier_esg')),
    is_active      BOOLEAN DEFAULT TRUE,
    last_login_at  TIMESTAMPTZ,
    created_at     TIMESTAMPTZ DEFAULT now()
);

-- [테이블 역할] 옆 라인 정보 차단(기본값 FALSE) 및 3차수 이내 등 사용자별 세밀한 공급망 열람 제어 매트릭스.
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


-- ============================================================
-- 영역 2. 협력사 마스터 (B 담당)
-- ============================================================

-- [테이블 역할] 공급망 내 모든 협력사 마스터. CTI 구조 분기의 부모 테이블 역할.
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
    supplier_type       VARCHAR(30) NOT NULL
        CONSTRAINT chk_supplier_type CHECK (supplier_type IN ('manufacturer', 'recycler', 'trader', 'miner')),
    parent_supplier_id  UUID REFERENCES suppliers(supplier_id),
    established_year    INT,
    employee_count      INT,
    completeness_score  INT DEFAULT 0,
    
    -- [A-1 상태] supplier_status 접두어 일괄 동기화
    status              VARCHAR(30) DEFAULT 'supplier_pending'
        CONSTRAINT chk_supplier_status CHECK (
            status IN ('supplier_pending', 'supplier_requested', 'supplier_in_progress', 'supplier_review', 'supplier_verified', 'supplier_violation', 'supplier_suspended')
        ),
        
    -- [B 속성 상태] 리스크 점수 가산식 스케일 업 대역 매칭
    risk_level          VARCHAR(20) DEFAULT 'low'
        CONSTRAINT chk_supplier_risk CHECK (risk_level IN ('low', 'medium', 'high', 'critical')),
        
    -- [B 속성 상태] FEOC 적격 여부
    feoc_status         VARCHAR(20) DEFAULT 'unknown'
        CONSTRAINT chk_supplier_feoc CHECK (feoc_status IN ('eligible', 'ineligible', 'under_review', 'unknown')),
        
    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now()
);

-- [테이블 역할] 협력사 공장/광산/본사 상세 사업장 정보. (공장 단위 원산지 추적의 불변 핵심 기준점)
CREATE TABLE supplier_factories (
    factory_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id   UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    factory_name     VARCHAR(255),
    factory_name_en  VARCHAR(255),
    address       TEXT,
    country       VARCHAR(2), -- ISO 3166-1 alpha-2
    region        VARCHAR(100),
    location      GEOMETRY(POINT, 4326), -- PostGIS 지리정보
    factory_role  VARCHAR(30)
        CONSTRAINT chk_factory_role CHECK (factory_role IN ('headquarters', 'production', 'outsourcing', 'processing', 'mining')),
    is_active     BOOLEAN DEFAULT TRUE,
    operating_period_from DATE,
    operating_period_to   DATE,
    monthly_capacity      VARCHAR(100),
    destination           VARCHAR(10) CONSTRAINT chk_factory_destination CHECK (destination IN ('EU', 'US', 'KR', 'BOTH')),
    destination_detail    TEXT,
    applicable_regulations JSONB, -- 공장별 차등 적용 규제 JSON 배열
    hidden_regulations    JSONB,
    supply_ratio_percent  NUMERIC(5,2),
    supply_quantity       VARCHAR(100),
    created_at    TIMESTAMPTZ DEFAULT now()
);

-- [테이블 역할] 연락망 및 리마인드 타겟 담당자 정보.
CREATE TABLE supplier_contacts (
    contact_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id   UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    factory_id    UUID REFERENCES supplier_factories(factory_id) ON DELETE SET NULL,
    name          VARCHAR(100),
    name_en       VARCHAR(100),
    role          VARCHAR(50),
    department    VARCHAR(100),
    email         VARCHAR(255),
    phone         VARCHAR(50),
    mobile        VARCHAR(50),
    is_primary    BOOLEAN DEFAULT FALSE,
    language      VARCHAR(50),
    created_at    TIMESTAMPTZ DEFAULT now()
);

-- [테이블 역할] 협력사 동의 단계 및 2주 Onboarding SLA 독촉 추적.
CREATE TABLE supplier_onboarding (
    onboarding_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id         UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    
    -- [A-5 상태] 동의 어휘 접두어 및 제약조건
    consent_status      VARCHAR(20) DEFAULT 'consent_pending'
        CONSTRAINT chk_consent_status CHECK (consent_status IN ('consent_pending', 'consent_agreed', 'consent_rejected')),
        
    consent_signed_at   TIMESTAMPTZ,
    agreement_status    VARCHAR(20) DEFAULT 'pending'
    CONSTRAINT chk_agreement_status CHECK (agreement_status IN ('pending', 'agreed', 'rejected')),
    agreement_signed_at TIMESTAMPTZ,
    last_invited_at     TIMESTAMPTZ,
    last_reminded_at    TIMESTAMPTZ,
    sla_due_date        TIMESTAMPTZ,
    reminder_count      INT DEFAULT 0
);

-- [테이블 역할] ISO 14001, Bettercoal 등 일반 품질/환경/안전 인증서 마스터.
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


-- ============================================================
-- 영역 3. Provider Type별 CTI 상세 (B 담당)
-- ============================================================

-- [테이블 역할] 제조기업 탄소 집약도(kgCO2eq/kg) 등 상세. (EU 배터리법 Art.7 입력)
CREATE TABLE supplier_manufacturer_details (
    detail_id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id           UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    manufacturing_process TEXT,
    energy_source         VARCHAR(100),
    capacity              VARCHAR(100),
    carbon_intensity      NUMERIC(10,4)
);

-- [테이블 역할] 재활용 비율(Co/Ni/Li 분량) 및 소스 상세. (EU 배터리법 재활용 요구 증빙)
CREATE TABLE supplier_recycler_details (
    detail_id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id             UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    recycled_materials      JSONB,
    recycling_certification VARCHAR(255),
    input_source            VARCHAR(50),
    recycled_content_ratio  NUMERIC(5,2)
);

-- [테이블 역할] 중개 트레이더 및 상위 공급망 원산지 자율 공개율.
CREATE TABLE supplier_trader_details (
    detail_id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id             UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    trading_license         VARCHAR(100),
    broker_certification    VARCHAR(255),
    disclosure_completeness NUMERIC(5,2) DEFAULT 0
);

-- [테이블 역할] 원료 광산 상세 정보. (Geo Audit Agent의 신장 및 DRC 위험 검증의 기준점)
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

-- [테이블 역할] 트레이더의 상위 협력사별 공개율 의무 상태 관리. (FEOC 우회 추적용 정보)
CREATE TABLE trader_disclosure_obligation (
    obligation_id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    trader_supplier_id      UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    upstream_supplier_id    UUID REFERENCES suppliers(supplier_id),
    disclosure_completeness NUMERIC(5,2),
    last_audited_at         TIMESTAMPTZ
);


-- ============================================================
-- 영역 4. 리스크 프로필 (B 담당)
-- ============================================================

-- [테이블 역할] 협력사별 종합 위험 평점 관리 대장. (가점식 스케일업 모델 반영)
CREATE TABLE supplier_risk_profiles (
    profile_id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id             UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    overall_risk_score      INT DEFAULT 0, -- 가점식 0 ~ 100점 점수계 (↑위험)
    risk_level              VARCHAR(20) DEFAULT 'low' CONSTRAINT chk_profile_risk CHECK (risk_level IN ('low', 'medium', 'high', 'critical')),
    feoc_status             VARCHAR(20) DEFAULT 'unknown' CONSTRAINT chk_profile_feoc CHECK (feoc_status IN ('eligible', 'ineligible', 'under_review', 'unknown')),
    feoc_direct_ownership   NUMERIC(5,2),
    feoc_indirect_ownership NUMERIC(5,2),
    feoc_last_assessed_at   TIMESTAMPTZ,
    feoc_cert_expiry        DATE,
    is_high_risk_flag       BOOLEAN DEFAULT FALSE,
    high_risk_reasons       JSONB, -- 고위험 유발 원인들의 텍스트 설명 배열
    last_risk_review_at     TIMESTAMPTZ,
    created_at              TIMESTAMPTZ DEFAULT now(),
    updated_at              TIMESTAMPTZ DEFAULT now(),
    UNIQUE(supplier_id)
);

-- [테이블 역할] 공급망 실사(Due Diligence)의 법적 수행 실적 관리 대장. (CSDDD 대응)
CREATE TABLE supplier_audit_records (
    audit_record_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id        UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    audit_date         DATE NOT NULL,
    audit_type         VARCHAR(30) CONSTRAINT chk_audit_type CHECK (audit_type IN ('on_site', 'remote', 'document_review', 'third_party')),
    auditor            VARCHAR(255),

    -- [v_action_items 정합] 실사 워크플로우 진행 상태(audit_status)와 담당 검사관(inspector_id).
    -- result(최종 판정: pass/fail)와는 의미가 다른 별개 축이다.
    -- result = 실사 '결과', audit_status = 실사 '진행 단계'.
    audit_status       VARCHAR(20) DEFAULT 'requested'
        CONSTRAINT chk_audit_status CHECK (audit_status IN ('requested', 'assigned', 'in_progress', 'completed', 'failed')),
    inspector_id       UUID REFERENCES users(user_id),

    audit_scope        TEXT,
    result             VARCHAR(30) CONSTRAINT chk_audit_result CHECK (result IN ('pass', 'conditional_pass', 'fail', 'pending')),
    findings           JSONB,
    corrective_actions JSONB,
    next_audit_due     DATE,
    report_url         VARCHAR(500),
    created_at         TIMESTAMPTZ DEFAULT now()
);

-- [테이블 역할] 광산 등 고위험 노드의 아동 노동 및 인권 실사 리포트 이슈 목록.
CREATE TABLE supplier_human_rights_issues (
    issue_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    factory_id  UUID REFERENCES supplier_factories(factory_id) ON DELETE SET NULL,
    issue_type  VARCHAR(50) CONSTRAINT chk_issue_type CHECK (issue_type IN ('forced_labor', 'child_labor', 'freedom_of_association', 'discrimination', 'harassment', 'wages', 'working_hours', 'other')),
    severity    VARCHAR(20) CONSTRAINT chk_issue_severity CHECK (severity IN ('critical', 'major', 'minor')),
    description TEXT,
    detected_at TIMESTAMPTZ,
    status      VARCHAR(30) CONSTRAINT chk_issue_status CHECK (status IN ('open', 'in_remediation', 'resolved', 'monitoring')),
    source      VARCHAR(255),
    resolved_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- [테이블 역할] 사업장 사고 재해율 및 소송 이력 관리 대장.
CREATE TABLE supplier_industrial_accidents (
    accident_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id       UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    factory_id        UUID REFERENCES supplier_factories(factory_id) ON DELETE SET NULL,
    accident_date     DATE NOT NULL,
    accident_type     VARCHAR(30) CONSTRAINT chk_accident_type CHECK (accident_type IN ('fatality', 'serious_injury', 'minor_injury', 'near_miss', 'environmental')),
    description       TEXT,
    casualties        INT DEFAULT 0,
    ltifr             NUMERIC(6,2),
    status            VARCHAR(20) CONSTRAINT chk_accident_status CHECK (status IN ('reported', 'investigating', 'closed')),
    corrective_action TEXT,
    created_at        TIMESTAMPTZ DEFAULT now()
);


-- ============================================================
-- 영역 5. 원산지 증명서 수집 전용 대장 (B 담당)
-- ============================================================

-- [테이블 역할] 포괄원산지확인서 등 협력사가 업로드한 증빙 서류의 수집 및 만료 검증을 위한 테이블. (KIRA는 증명서를 발급하지 않음)
CREATE TABLE origin_certificates (
    cert_id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id       UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    factory_id        UUID REFERENCES supplier_factories(factory_id) ON DELETE SET NULL,
    cert_type         VARCHAR(30) NOT NULL CONSTRAINT chk_cert_type CHECK (cert_type IN ('FTA', 'GSP', 'UFLPA_REBUTTAL', 'IRA_ORIGIN', 'CONFLICT_FREE', 'GENERAL')),
    cert_number       VARCHAR(100),
    issuing_authority VARCHAR(255),
    issued_at         DATE,
    expires_at        DATE NOT NULL, -- 원산지포괄확인서 기준 12개월 만료 자동 검증
    origin_country    VARCHAR(2),
    covered_minerals  JSONB,
    status            VARCHAR(20) DEFAULT 'valid' CONSTRAINT chk_cert_status CHECK (status IN ('valid', 'expiring_soon', 'expired', 'under_review')),
    document_url      VARCHAR(500),
    created_at        TIMESTAMPTZ DEFAULT now(),
    updated_at        TIMESTAMPTZ DEFAULT now()
);


-- ============================================================
-- 영역 6. 교육 관리 (B 담당)
-- ============================================================

-- [테이블 역할] 법적 의무 공급망 이수 교육 마스터 카탈로그.
CREATE TABLE training_materials (
    material_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title            VARCHAR(255) NOT NULL,
    title_en         VARCHAR(255),
    category         VARCHAR(50) CONSTRAINT chk_training_category CHECK (category IN ('human_rights', 'safety', 'environmental', 'anti_corruption', 'conflict_minerals', 'data_protection', 'esg_general')),
    description      TEXT,
    format           VARCHAR(20) CONSTRAINT chk_training_format CHECK (format IN ('pdf', 'video', 'online', 'onsite')),
    duration_minutes INT,
    required_for     JSONB, -- 예: ["CSDDD"] 의무화 규제 리스트
    version          VARCHAR(20),
    url              VARCHAR(500),
    updated_at       TIMESTAMPTZ DEFAULT now()
);

-- [테이블 역할] 협력사별/사업장별 연간 이수 증빙 및 진척도 추적.
CREATE TABLE training_records (
    record_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id     UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    factory_id      UUID REFERENCES supplier_factories(factory_id) ON DELETE SET NULL,
    material_id     UUID REFERENCES training_materials(material_id),
    trainee_count   INT DEFAULT 0,
    total_eligible  INT DEFAULT 0,
    completion_rate NUMERIC(5,2) DEFAULT 0,
    completed_at    TIMESTAMPTZ,
    due_date        DATE NOT NULL,
    status            VARCHAR(20) DEFAULT 'not_started' CONSTRAINT chk_training_status CHECK (status IN ('completed', 'in_progress', 'overdue', 'not_started')),
    instructor      VARCHAR(255),
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);


-- ============================================================
-- 영역 7. 제품 / BOM / 부품 (C 담당 - Ingest 컬럼 전수 동기화)
-- ============================================================

-- [테이블 역할] 원청사의 복사본 제품 마스터. (결정 #1 ERP Ingest 일치)
CREATE TABLE products (
    product_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    product_code    VARCHAR(50) UNIQUE NOT NULL,
    product_name    VARCHAR(255),
    manufacturer_id UUID REFERENCES suppliers(supplier_id),
    type            VARCHAR(50),
    specs           JSONB,
    
    -- [결정 #1] 외부 원천시스템 연동 마크
    source_system   VARCHAR(100) DEFAULT 'ERP_PLM',
    external_id     VARCHAR(255),
    synced_at       TIMESTAMPTZ DEFAULT now(),
    
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

-- [테이블 역할] 동일 제품의 배치/Lot별 버전 이력. (결정 #1 ERP Ingest 일치)
CREATE TABLE bom_versions (
    bom_version_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    product_id     UUID REFERENCES products(product_id) ON DELETE CASCADE,
    version_number VARCHAR(20) NOT NULL,
    effective_from DATE,
    effective_to   DATE,
    status         VARCHAR(20) DEFAULT 'draft' CONSTRAINT chk_bom_status CHECK (status IN ('draft', 'active', 'deprecated')),
    approved_by    UUID REFERENCES users(user_id),
    approved_at    TIMESTAMPTZ,
    
    -- [결정 #1 누락 정형화] 외부 원천시스템 연동 마크 주입
    source_system   VARCHAR(100) DEFAULT 'ERP_PLM',
    external_id     VARCHAR(255),
    synced_at       TIMESTAMPTZ DEFAULT now(),
    
    created_at     TIMESTAMPTZ DEFAULT now()
);

-- [테이블 역할] 5계층 부품 마스터 트리. (Pack-Module-Cell-전구체-광산, 결정 #1 ERP Ingest 일치)
CREATE TABLE parts (
    part_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    part_code        VARCHAR(50) UNIQUE NOT NULL,
    part_name        VARCHAR(255),
    tier_level       INT, -- 1(Pack) 2(Module) 3(Cell) 4(활물질/CAM) 5(전구체) 6(제련·정제) 7(광산). 분리막/전해질은 material_type
    parent_part_id   UUID REFERENCES parts(part_id),
    
    -- [위상 조정] 세번변경 FTA 계산용이 아닌, 단순 통관 및 특정 HS코드 규제 필터링용으로 용도 변경
    hs_code          VARCHAR(15), 
    
    material_type    VARCHAR(100),
    function_purpose TEXT,
    
    -- [위상 조정] FTA RVC 부가가치 판정용이 아닌, 원청사의 단순 보조용 자재 단가로 용도 변경
    unit_price       NUMERIC(15,4), 
    
    purchase_unit    VARCHAR(20),
    specs            JSONB,
    
    -- [결정 #1 누락 정형화] 외부 원천시스템 연동 마크 주입
    source_system   VARCHAR(100) DEFAULT 'ERP_PLM',
    external_id     VARCHAR(255),
    synced_at       TIMESTAMPTZ DEFAULT now(),
    
    created_at       TIMESTAMPTZ DEFAULT now()
);

-- [테이블 역할] 개별 자재 소요량 및 제조 원가 대장. (결정 #1 ERP Ingest 일치)
CREATE TABLE bom_items (
    bom_item_id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    bom_version_id         UUID REFERENCES bom_versions(bom_version_id) ON DELETE CASCADE,
    part_id                UUID REFERENCES parts(part_id),
    required_quantity      NUMERIC(15,4),
    required_quantity_unit VARCHAR(20),
    percentage             NUMERIC(5,2),
    direct_material_cost   NUMERIC(15,4), -- [위상 조정] 단순 가중치 비중용 보조 단가
    origin_country         VARCHAR(2),
    
    -- [결정 #1 누락 정형화] 외부 원천시스템 연동 마크 주입
    source_system   VARCHAR(100) DEFAULT 'ERP_PLM',
    external_id     VARCHAR(255),
    synced_at       TIMESTAMPTZ DEFAULT now()
);

-- [테이블 역할] 원청 자재 코드와 협력사 내부 고유 품번 간의 양방향 매핑 대장.
CREATE TABLE part_code_mapping (
    mapping_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    part_id             UUID REFERENCES parts(part_id) ON DELETE CASCADE,
    supplier_id         UUID REFERENCES suppliers(supplier_id),
    supplier_part_code  VARCHAR(50),
    original_part_code  VARCHAR(50)
);

-- [테이블 역할] 공정 신뢰도 및 CSDDD 감사 추적용 공정 매뉴얼 매핑 테이블.
CREATE TABLE manufacturing_process (
    process_id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    part_id                   UUID REFERENCES parts(part_id) ON DELETE CASCADE,
    sequence_no               INT,
    process_name              VARCHAR(255),
    process_description       TEXT,
    is_outsourced             BOOLEAN DEFAULT FALSE,
    outsourced_to_supplier_id UUID REFERENCES suppliers(supplier_id),
    process_image_url         VARCHAR(500)
);


-- ============================================================
-- 영역 8. 공급망 맵 (D 담당)
-- ============================================================

-- [테이블 역할] N차 전방 공급망 흐름의 그래프 연결 대장. (결정 #2 / #9-여파4 반영)
CREATE TABLE supply_chain_map (
    map_id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    bom_version_id     UUID REFERENCES bom_versions(bom_version_id),
    parent_supplier_id UUID REFERENCES suppliers(supplier_id),
    child_supplier_id  UUID REFERENCES suppliers(supplier_id), -- 미발견 시 NULL 허용
    part_id            UUID REFERENCES parts(part_id),
    hop_level          INT,  -- 차수 SSOT: 원청(parent NULL)=1 기준 hop. (구 suppliers.tier 대체)
    po_number          VARCHAR(50),
    invoice_number     VARCHAR(50),
    supply_period_from DATE,
    supply_period_to   DATE,
    
    -- [결정 #2 / #9-여파4] 발견 및 정합성 컬럼 추가
    link_status        VARCHAR(30) DEFAULT 'supplychain_declared'
        CONSTRAINT chk_link_status CHECK (link_status IN ('supplychain_declared', 'supplychain_confirmed')),
    discovered_via     UUID REFERENCES suppliers(supplier_id), -- 상위 협력사 대리 신고 시 FK
    source_system      VARCHAR(50) DEFAULT 'ERP' CONSTRAINT chk_map_source CHECK (source_system IN ('ERP', 'SUPPLIER_DECLARED')),
    verification_status VARCHAR(20) DEFAULT 'unverified' CONSTRAINT chk_map_verification CHECK (verification_status IN ('unverified', 'verified')),
    
    created_at         TIMESTAMPTZ DEFAULT now()
);

-- [테이블 역할] 공동 납품 시 공장별 분할 기여도 관리 대장.
CREATE TABLE supply_ratio (
    ratio_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    map_id           UUID REFERENCES supply_chain_map(map_id) ON DELETE CASCADE,
    factory_id       UUID REFERENCES supplier_factories(factory_id),
    ratio_percentage NUMERIC(5,2),
    volume           NUMERIC(15,4),
    unit             VARCHAR(20)
);


-- ============================================================
-- 영역 9. 운영 / 배치 / DPP (A, E 담당)
-- ============================================================

-- [테이블 역할] LangGraph 에이전트 실행 배치의 스냅샷 상태 저장소. (결정 #1 Ingest 보완 완료)
CREATE TABLE batches (
    batch_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    product_id       UUID REFERENCES products(product_id),
    bom_version_id   UUID REFERENCES bom_versions(bom_version_id),
    tenant_id        UUID REFERENCES tenants(tenant_id),
    received_at      TIMESTAMPTZ DEFAULT now(),
    destination      VARCHAR(2) CONSTRAINT chk_batch_destination CHECK (destination IN ('US', 'EU', 'KR')),
    
    -- [A-7 상태] batch_stage 접두어 일괄 적용
    current_stage    VARCHAR(50) DEFAULT 'stage_queued'
        CONSTRAINT chk_batch_stage CHECK (
            current_stage IN ('stage_queued', 'stage_extraction', 'stage_verification', 'stage_geo', 'stage_compliance', 'stage_risk', 'stage_readiness', 'stage_issuance')
        ),
        
    -- [A-6 상태] batch_status 접두어 일괄 적용
    status           VARCHAR(30) DEFAULT 'batch_processing'
        CONSTRAINT chk_batch_status CHECK (
            status IN ('batch_processing', 'batch_hitl_wait', 'batch_completed', 'batch_rejected')
        ),
        
    confidence_score NUMERIC(5,4),
    
    -- [결정 #1 정교화] 외부 원천시스템 연동 마크 주입 (생산 배치는 MES 동기화)
    source_system   VARCHAR(100) DEFAULT 'MES',
    external_id     VARCHAR(255),
    synced_at       TIMESTAMPTZ DEFAULT now()
);

-- [테이블 역할] 발행이 완료된 디지털 여권 기록 대장. (issued 전이 시 UPDATE 불능화)
CREATE TABLE dpp_records (
    dpp_id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    batch_id         UUID REFERENCES batches(batch_id),
    product_id       UUID REFERENCES products(product_id),
    issued_at        TIMESTAMPTZ,
    
    -- [A-10 상태] dpp_status 접두어 일괄 적용
    status           VARCHAR(20) DEFAULT 'dpp_issued'
        CONSTRAINT chk_dpp_status CHECK (status IN ('dpp_issued', 'dpp_revoked')),
        
    carbon_footprint NUMERIC(10,4),
    recycled_content JSONB,
    qr_code_url      VARCHAR(500),
    payload          JSONB, -- Annex XIII 80개 법적 연동 규격 전체
    approved_by      UUID REFERENCES users(user_id)
);


-- ============================================================
-- 영역 10. 규제 / 컴플라이언스 (C 담당 — 최종 10대 규제화)
-- ============================================================

-- [테이블 역할] 적용 규제 마스터. (LkSG 제거 및 EUDR_FSC 통합으로 최종 10개 레코드로 수렴)
CREATE TABLE regulations (
    regulation_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name             VARCHAR(100),
    regulation_code  VARCHAR(50) UNIQUE, -- C-1 매핑 키
    region           VARCHAR(10) CONSTRAINT chk_regulation_region CHECK (region IN ('EU', 'US', 'BOTH')),
    description      TEXT,
    version          VARCHAR(20),
    effective_from   DATE,
    document_s3_url  VARCHAR(500),
    embedding_status VARCHAR(20) DEFAULT 'pending' CONSTRAINT chk_reg_embedding_status CHECK (embedding_status IN ('pending', 'indexed')),
    embedding        vector(1536) -- openai text-embedding-3-small 대응
);

-- [테이블 역할] 검증 결과 대장. (verdict 4종 + 회색지대needs_human_review 플래그 적용 완료)
CREATE TABLE compliance_results (
    result_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    batch_id         UUID REFERENCES batches(batch_id) ON DELETE CASCADE,
    regulation_id    UUID REFERENCES regulations(regulation_id),
    supplier_id      UUID REFERENCES suppliers(supplier_id),
    
    -- [A-8 상태] compliance_verdict 접두어 일괄 적용 및 gray_zone 분리 완료
    verdict          VARCHAR(30) DEFAULT 'compliance_passed'
        CONSTRAINT chk_compliance_verdict CHECK (
            verdict IN ('compliance_passed', 'compliance_violation', 'compliance_warning', 'compliance_reject')
        ),
        
    -- [결정 #4 / #8-B] 회색지대 독립 채널 플래그 (needs_human_review)
    needs_human_review BOOLEAN DEFAULT FALSE,
    
    cited_clauses    JSONB,
    confidence_score NUMERIC(5,4),
    reasoning_text   TEXT,
    created_at       TIMESTAMPTZ DEFAULT now()
);

-- [테이블 역할] 법률의 적용 대상 차수 및 업종 정의 매트릭스.
CREATE TABLE regulation_applicability (
    applicability_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    regulation_id            UUID REFERENCES regulations(regulation_id),
    applicable_provider_type VARCHAR(30),
    applicable_tier          INT,
    severity                 VARCHAR(20) CONSTRAINT chk_app_severity CHECK (severity IN ('mandatory', 'recommended'))
);

-- [테이블 역할] 업종 마스터별 필수 제출 서류 및 필수 키-값 쌍 스키마 사양서.
CREATE TABLE onboarding_data_requirements (
    requirement_id   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    provider_type    VARCHAR(30),
    required_fields  JSONB,
    required_documents JSONB
);


-- ============================================================
-- 영역 11. 데이터 흐름 추적 / Submission 상태머신 (E 담당)
-- ============================================================

-- [테이블 역할] 데이터 요청 및 수령 SLA 추적 관리 대장. (결정 #10-5 Rework 반영)
CREATE TABLE data_request_log (
    request_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    requester_user_id   UUID REFERENCES users(user_id),
    target_supplier_id  UUID REFERENCES suppliers(supplier_id),
    requested_data_type VARCHAR(100),
    requested_at        TIMESTAMPTZ DEFAULT now(),
    due_date            TIMESTAMPTZ,
    
    -- [A-3 상태] response_status 접두어 일괄 적용 및 제약조건
    response_status     VARCHAR(30) DEFAULT 'response_pending'
        CONSTRAINT chk_response_status CHECK (
            response_status IN ('response_pending', 'response_responded', 'response_overdue', 'response_escalated')
        ),
        
    reminder_count      INT DEFAULT 0,
    last_reminder_at    TIMESTAMPTZ,
    responded_at        TIMESTAMPTZ,
    
    -- [A-2 상태] submission_status 접두어 일괄 적용, rework 추가, 제약조건
    submission_status   VARCHAR(30) DEFAULT 'submission_requested'
        CONSTRAINT chk_submission_status CHECK (
            submission_status IN (
                'submission_requested', 'submission_in_progress', 'submission_submitted', 
                'submission_review', 'submission_approved', 'submission_rework', 'submission_rejected'
            )
        ),
        
    -- [결정 #10-5] 보관 전이 분리 플래그
    is_archived         BOOLEAN DEFAULT FALSE,
    
    created_at          TIMESTAMPTZ DEFAULT now()
);

-- [테이블 역할] 협력사가 포털에서 제출 건(data_request)에 업로드한 증빙 파일 원본 메타 대장.
-- (스펙: POST /data-requests/{id}/submit 의 file_urls[] 배열 수신처, parse_document(file_url) 입력 원천,
--  HITL context의 '업로드 증빙 서류 URL 목록' 소스, document_integrity_rule의 원본-폼 대조 기준점)
CREATE TABLE submission_documents (
    document_id   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    request_id    UUID REFERENCES data_request_log(request_id) ON DELETE CASCADE,
    supplier_id   UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    file_url      VARCHAR(500) NOT NULL,
    file_name     VARCHAR(255),
    file_type     VARCHAR(30)
        CONSTRAINT chk_doc_file_type CHECK (file_type IN ('pdf', 'xlsx', 'csv', 'image', 'docx', 'other')),
    -- 업로드 서류의 업무상 분류 (원산지/공장/FEOC 증빙/인증서/기타)
    doc_category  VARCHAR(50)
        CONSTRAINT chk_doc_category CHECK (doc_category IN ('origin_cert', 'factory_doc', 'feoc_proof', 'certification', 'audit_report', 'carbon_data', 'other')),
    file_hash     VARCHAR(64), -- SHA-256, document_integrity_rule(서류-폼 불일치) 대조용
    uploaded_by   UUID REFERENCES users(user_id),
    uploaded_at   TIMESTAMPTZ DEFAULT now()
);

-- [테이블 역할] AI 문서 추출(Parsing) 가공 전 결과와 신뢰도 보관 임시 저장소. (구멍 ① 보완 완료)
CREATE TABLE document_extraction_results (
    extraction_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    request_id          UUID REFERENCES data_request_log(request_id) ON DELETE CASCADE,
    document_id         UUID REFERENCES submission_documents(document_id) ON DELETE CASCADE, -- 파싱 대상 원본 파일 연결
    parsed_fields       JSONB, -- AI가 추론한 Key-Value 구조체
    confidence_map      JSONB, -- 필드별 추출 신뢰도 점수 (0.0 ~ 1.0)
    unparsed_fields     JSONB, -- 파싱 실패 필드 리스트
    supplier_confirmed  BOOLEAN DEFAULT FALSE, -- 협력사가 눈으로 검토하고 확인 버튼 눌렀는지 여부
    confirmed_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT now()
);

-- [테이블 역할] 제출 상태 전이 완벽 감사 이력 추적용 이력 대장. (Timeline 탭 연동)
CREATE TABLE submission_status_history (
    history_id  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    request_id  UUID REFERENCES data_request_log(request_id) ON DELETE CASCADE,
    from_status VARCHAR(30),
    to_status   VARCHAR(30) NOT NULL,
    actor_id    UUID REFERENCES users(user_id),
    reason      TEXT,
    changed_at  TIMESTAMPTZ DEFAULT now()
);

-- [테이블 역할] 입력된 공급망 정보 누락도 실시간 카운트 테이블. (완성도 40점 계산 원천)
CREATE TABLE data_completeness_status (
    status_id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_type          VARCHAR(30) CONSTRAINT chk_completeness_entity CHECK (entity_type IN ('supplier', 'part', 'bom', 'factory')),
    entity_id            UUID,
    required_field_count INT,
    filled_field_count   INT,
    completion_rate      NUMERIC(5,2),
    missing_fields       JSONB,
    last_updated_by      UUID REFERENCES users(user_id),
    last_updated_at      TIMESTAMPTZ DEFAULT now()
);

-- [테이블 역할] SMS/이메일/In-App 발송 알림 대장.
CREATE TABLE notifications (
    notification_id   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id           UUID REFERENCES users(user_id),
    channel           VARCHAR(20) CONSTRAINT chk_notification_channel CHECK (channel IN ('email', 'slack', 'in-app')),
    notification_type VARCHAR(50) CONSTRAINT chk_notification_type CHECK (notification_type IN ('reminder', 'violation', 'approval_needed', 'sla_warning', 'training_overdue')),
    subject           VARCHAR(255),
    body              TEXT,
    sent_at           TIMESTAMPTZ,
    read_at           TIMESTAMPTZ,
    status            VARCHAR(20) CONSTRAINT chk_notification_status CHECK (status IN ('pending', 'sent', 'failed', 'read')),
    -- [멱등성] 같은 트리거(예: 동일 SLA 리마인드)가 중복 발송되지 않도록 하는 중복 차단 키.
    -- 예: 'sla_reminder:{request_id}:{date}'. UNIQUE로 중복 INSERT 차단.
    dedup_key         VARCHAR(255) UNIQUE,
    created_at        TIMESTAMPTZ DEFAULT now()
);

-- [테이블 역할] ARQ 큐 작업의 멱등성(Idempotency) 보장용 처리 이력 영속 저장소.
-- (스펙 1-3/5-4/PR 체크리스트: '같은 이벤트가 두 번 들어와도 한 번만 처리'. Redis는 휘발성이라
--  컨테이너 재기동 시 멱등성이 깨지므로 처리 키를 DB에 영속화한다. 워커는 작업 시작 전 이 키를 조회/선점.)
CREATE TABLE processed_jobs (
    idempotency_key  VARCHAR(255) PRIMARY KEY, -- 예: '{event_name}:{batch_id}:{rule}' 등 작업 고유 키
    queue_name       VARCHAR(50)
        CONSTRAINT chk_processed_queue CHECK (queue_name IN (
            'document_parse_queue', 'verification_queue', 'risk_queue',
            'hitl_queue', 'notification_queue', 'dpp_publish_queue', 'dead_letter_queue'
        )),
    job_id           VARCHAR(100), -- ARQ가 반환한 job_id
    status           VARCHAR(20) DEFAULT 'processing'
        CONSTRAINT chk_processed_status CHECK (status IN ('processing', 'done', 'failed')),
    retry_count      INT DEFAULT 0, -- 지수 백오프 재시도 횟수 (3회 초과 시 dead_letter_queue)
    result           JSONB,        -- 처리 결과 캐시 (재호출 시 재실행 없이 반환)
    error_text       TEXT,         -- 실패 사유 (DLQ 디버깅용)
    processed_at     TIMESTAMPTZ DEFAULT now(),
    updated_at       TIMESTAMPTZ DEFAULT now()
);


-- ============================================================
-- 영역 12. 감사 추적 / HITL (A 담당)
-- ============================================================

-- [테이블 역할] AI 판정 보류 사유별 실시간 관리 이력대장. (구멍 ② 보완 완료, 전사 작업 큐 원천)
CREATE TABLE hitl_reviews (
    review_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    batch_id          UUID REFERENCES batches(batch_id) ON DELETE CASCADE,
    reason            VARCHAR(100) NOT NULL, -- 'gray_zone' | 'risk_escalated' (low_confidence는 협력사 reverify 경로, 미기입)
    trigger_stage     VARCHAR(50) NOT NULL,
    assigned_to       UUID REFERENCES users(user_id),
    
    -- [A-9 상태] hitl_status 접두어 일괄 적용 및 resolution 필드 분리
    status            VARCHAR(30) DEFAULT 'hitl_pending'
        CONSTRAINT chk_hitl_status CHECK (status IN ('hitl_pending', 'hitl_in_review', 'hitl_resolved')),
    resolution        VARCHAR(20)
        CONSTRAINT chk_hitl_resolution CHECK (resolution IN ('approve', 'reject', 'escalate')),
        
    decision_text     TEXT,
    decided_by        UUID REFERENCES users(user_id),
    decided_at        TIMESTAMPTZ,
    created_at        TIMESTAMPTZ DEFAULT now()
);

-- [테이블 역할] 영구 해시체인 원청 감사 로그. (@trace_node 자동 기록지)
CREATE TABLE audit_trail (
    audit_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    batch_id       UUID REFERENCES batches(batch_id),
    step_number    INT,
    timestamp      TIMESTAMPTZ DEFAULT now(),
    node_type      VARCHAR(20) CONSTRAINT chk_audit_node_type CHECK (node_type IN ('agent', 'tool', 'human')),
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

-- [테이블 역할] 법령 개정 영향 범위 분석 결과서.
CREATE TABLE gap_analysis_results (
    analysis_id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    regulation_id         UUID REFERENCES regulations(regulation_id),
    previous_version_id   UUID REFERENCES regulations(regulation_id),
    affected_supplier_ids JSONB,
    newly_required_fields JSONB,
    gray_zone_items       JSONB,
    analyzed_at           TIMESTAMPTZ DEFAULT now(),
    reviewed_by           UUID REFERENCES users(user_id),
    reviewed_at           TIMESTAMPTZ
);


-- ============================================================
-- 트리거 및 함수 정의 (Immutable Issued Lock)
-- ============================================================

-- [함수 역할] dpp_records가 UPDATE될 때 status가 'dpp_issued' 상태이면 예외를 발생시킨다.
CREATE OR REPLACE FUNCTION prevent_issued_dpp_update()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.status = 'dpp_issued' THEN
        RAISE EXCEPTION
          'DPP record % is already issued and is strictly immutable. Modifying issued DPP is forbidden.',
          OLD.dpp_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- [트리거 연결] BEFORE UPDATE trigger 장착
CREATE TRIGGER trg_dpp_immutable
    BEFORE UPDATE ON dpp_records
    FOR EACH ROW EXECUTE FUNCTION prevent_issued_dpp_update();


-- ============================================================
-- 뷰 (Views) 정의
-- ============================================================

-- [뷰 역할] 공급망 허브 중앙 지도 컬러링 지원 뷰. (수정된 접두어 반영)
CREATE OR REPLACE VIEW v_supply_chain_node_status AS
SELECT
    scm.map_id,
    scm.parent_supplier_id,
    scm.child_supplier_id,
    scm.part_id,
    scm.link_status,
    s.company_name,
    s.company_name_en,
    s.supplier_type,
    scm.hop_level,
    p.tier_level        AS bom_depth,
    s.status            AS supplier_status,
    s.risk_level,
    s.feoc_status,
    s.completeness_score,
    sf.country,
    sf.location,
    sf.applicable_regulations,
    drl.submission_status,
    drl.due_date,
    drl.response_status,
    CASE
        -- 위험 대역 1순위 (위반 또는 High/Critical 리스크 발생 시 적색 표출)
        WHEN s.status = 'supplier_violation'                         THEN 'red'
        WHEN s.risk_level IN ('high', 'critical')                    THEN 'red'
        -- 완결 및 완료 상태
        WHEN drl.submission_status = 'submission_approved'           THEN 'green'
        -- 원청 검토 및 제출 대기
        WHEN drl.submission_status IN ('submission_submitted', 'submission_review') THEN 'yellow'
        -- 독촉 및 입력 진행 중
        WHEN drl.submission_status IN ('submission_requested', 'submission_in_progress', 'submission_rework') THEN 'blue'
        ELSE 'gray'
    END AS node_color
FROM supply_chain_map scm
JOIN suppliers s
    ON s.supplier_id = scm.child_supplier_id
LEFT JOIN parts p
    ON p.part_id = scm.part_id
LEFT JOIN supplier_factories sf
    ON sf.supplier_id = s.supplier_id AND sf.is_active = TRUE
LEFT JOIN data_request_log drl
    ON drl.target_supplier_id = s.supplier_id
   AND drl.response_status != 'response_responded'
   AND drl.requested_at = (
         SELECT MAX(d2.requested_at)
         FROM data_request_log d2
         WHERE d2.target_supplier_id = s.supplier_id
       );

-- [뷰 역할] 원청 ESG/구매팀 작업 큐 관제용 통합 실시간 가상 뷰. (결정 #10-3 연동 완료)
CREATE OR REPLACE VIEW v_action_items AS
-- 1) 데이터 제출 검토 건 (Submission Review)
SELECT 
    request_id::text AS action_id,
    'SUB' AS source_type,
    '제출 자료 검토: ' || requested_data_type AS title,
    target_supplier_id AS supplier_id,
    requester_user_id AS assigned_to,
    due_date AS due_date,
    CASE 
        WHEN submission_status IN ('submission_requested', 'submission_in_progress') THEN 'sent'
        WHEN submission_status IN ('submission_submitted', 'submission_review') THEN 'review'
        WHEN submission_status = 'submission_approved' THEN 'resolved'
        WHEN submission_status = 'submission_rework' THEN 'review'
        WHEN submission_status = 'submission_rejected' THEN 'open'
        ELSE 'open'
    END AS action_status
FROM data_request_log

UNION ALL

-- 2) 원격/현장 실사 개선 조치 건 (Due Diligence Actions)
SELECT 
    audit_record_id::text AS action_id,
    'DD' AS source_type,
    '실사 보완 조치 필요' AS title,
    supplier_id AS supplier_id,
    inspector_id AS assigned_to,
    next_audit_due::timestamptz AS due_date,
    CASE 
        WHEN audit_status IN ('requested', 'assigned') THEN 'sent'
        WHEN audit_status = 'in_progress' THEN 'review'
        WHEN audit_status = 'completed' THEN 'resolved'
        WHEN audit_status = 'failed' THEN 'blocked'
        ELSE 'open'
    END AS action_status
FROM supplier_audit_records

UNION ALL

-- 3) AI 판정 보류 사람 검토 건 (HITL Reviews)
SELECT 
    review_id::text AS action_id,
    'HITL' AS source_type,
    'AI 판정 보류 검토: ' || reason AS title,
    NULL::uuid AS supplier_id,
    assigned_to AS assigned_to,
    created_at + INTERVAL '3 days' AS due_date,
    CASE 
        WHEN status = 'hitl_pending' THEN 'open'
        WHEN status = 'hitl_in_review' THEN 'review'
        WHEN status = 'hitl_resolved' THEN 'resolved'
        ELSE 'open'
    END AS action_status
FROM hitl_reviews;


-- ============================================================
-- 물리 인덱스 (Indexes) 정의
-- ============================================================

-- 1) 협력사 및 지리 쿼리 인덱스
CREATE INDEX idx_suppliers_type          ON suppliers(supplier_type);
CREATE INDEX idx_scm_hop_level           ON supply_chain_map(hop_level);
CREATE INDEX idx_suppliers_parent        ON suppliers(parent_supplier_id);
CREATE INDEX idx_suppliers_status        ON suppliers(status);
CREATE INDEX idx_suppliers_risk_level    ON suppliers(risk_level);
CREATE INDEX idx_suppliers_feoc_status   ON suppliers(feoc_status);
CREATE INDEX idx_factories_location      ON supplier_factories USING GIST(location);
CREATE INDEX idx_miner_coords            ON supplier_miner_details USING GIST(mine_coordinates);

-- 2) 원산지 및 자재 트리 인덱스
CREATE INDEX idx_origin_certs_supplier   ON origin_certificates(supplier_id);
CREATE INDEX idx_origin_certs_expiry     ON origin_certificates(expires_at) WHERE status IN ('valid', 'expiring_soon');
CREATE INDEX idx_training_records_supplier ON training_records(supplier_id);
CREATE INDEX idx_training_records_due    ON training_records(due_date) WHERE status IN ('in_progress', 'not_started');
CREATE INDEX idx_parts_parent            ON parts(parent_part_id);
CREATE INDEX idx_parts_hs_code           ON parts(hs_code);

-- 3) 배치 및 벡터 RAG 코사인 인덱스
CREATE INDEX idx_batches_status          ON batches(status);
CREATE INDEX idx_batches_tenant_status   ON batches(tenant_id, status);
CREATE INDEX idx_dpp_product             ON dpp_records(product_id);
CREATE INDEX idx_regulations_embedding   ON regulations USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_compliance_supplier     ON compliance_results(supplier_id);

-- 4) 워크플로우 추적 및 파싱 임시 인덱스
CREATE INDEX idx_data_request_due        ON data_request_log(due_date) WHERE response_status = 'response_pending';
CREATE INDEX idx_data_request_submission ON data_request_log(submission_status);
CREATE INDEX idx_submission_history      ON submission_status_history(request_id, changed_at);
CREATE INDEX idx_audit_batch             ON audit_trail(batch_id, step_number);
CREATE INDEX idx_doc_extraction_request  ON document_extraction_results(request_id); -- 구멍 ① 최적화 추가 완료
CREATE INDEX idx_doc_extraction_document ON document_extraction_results(document_id); -- submission_documents 연결 조회

-- [신설] submission_documents 인덱스
CREATE INDEX idx_submission_docs_request  ON submission_documents(request_id);
CREATE INDEX idx_submission_docs_supplier ON submission_documents(supplier_id);
CREATE INDEX idx_submission_docs_hash     ON submission_documents(file_hash); -- document_integrity_rule 대조용

-- [신설] supplier_audit_records 워크플로우 컬럼 인덱스 (v_action_items 큐 조회 최적화)
CREATE INDEX idx_audit_records_status     ON supplier_audit_records(audit_status) WHERE audit_status IN ('requested', 'assigned', 'in_progress');
CREATE INDEX idx_audit_records_inspector  ON supplier_audit_records(inspector_id) WHERE inspector_id IS NOT NULL;

-- [신설] notifications dedup_key는 UNIQUE로 자동 인덱싱됨. 미발송 큐 조회용 부분 인덱스.
CREATE INDEX idx_notifications_pending    ON notifications(status, created_at) WHERE status = 'pending';

-- [신설] processed_jobs는 PK(idempotency_key)로 자동 인덱싱됨. DLQ 모니터링/재시도용 부분 인덱스.
CREATE INDEX idx_processed_jobs_failed    ON processed_jobs(queue_name, processed_at) WHERE status = 'failed';


-- ============================================================
-- 마스터 데이터 시드 (Regulations - 최종 10개)
-- ============================================================

INSERT INTO regulations (name, regulation_code, region, version, effective_from, description, embedding_status)
VALUES
  ('EU Deforestation Regulation',          'EUDR',              'EU', '2023/1115', '2024-12-30', 'EU 산림파괴방지법 (FSC 인증서 하위 검증 포함)', 'pending'),
  ('Corporate Sustainability Due Diligence','CSDDD',            'EU', '2024/1760', '2027-01-01', 'EU 공급망 실사지침', 'pending'),
  ('Uyghur Forced Labor Prevention Act',   'UFLPA',             'US', '2021',      '2022-06-21', '미국 위구르 강제노동방지법', 'pending'),
  ('Inflation Reduction Act (FEOC)',        'IRA',               'US', '2022',      '2023-01-01', '미국 인플레이션감축법 FEOC', 'pending'),
  ('EU Battery Regulation',                'EU_BATTERY',        'EU', '2023/1542', '2025-02-18', 'EU 배터리법 전체', 'pending'),
  ('EU Battery Regulation Art.7',          'EU_BATTERY_ART7',   'EU', '2023/1542', '2025-02-18', '탄소발자국 신고 의무', 'pending'),
  ('EU Battery Regulation Art.47',         'EU_BATTERY_ART47',  'EU', '2023/1542', '2027-08-18', '공급망 실사 DDP 수립', 'pending'),
  ('Carbon Border Adjustment Mechanism',   'CBAM',              'EU', '2023/956',  '2026-01-01', 'EU 탄소국경조정제도', 'pending'),
  ('EU Conflict Minerals Regulation',      'CONFLICT_MINERALS', 'EU', '2017/821',  '2021-01-01', 'EU 분쟁광물 규정', 'pending'),
  ('Critical Raw Materials Act',           'CRMA',              'EU', '2024/1252', '2024-05-23', 'EU 핵심원자재법', 'pending');