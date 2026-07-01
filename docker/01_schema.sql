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
    manager_id     UUID REFERENCES users(user_id) ON DELETE SET NULL, -- [다단계 결재] 상급자 자기참조 (결재선 자동 구성)
    supplier_id    UUID,  -- [협력사 본인 식별 §0.5] 협력사 계정이 대표하는 supplier. 로그인 supplier_id 클레임/포털 스코프 소스. OEM 계정은 NULL.
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

-- [테이블 역할 §0.8] 공통 파일 업로드 저장소. 첨부 화면(자료 제출·실사/시정 보고서·온보딩)이
-- 공통으로 쓰는 POST/GET/DELETE /files 의 메타 대장. 실제 바이트는 S3, 여기엔 메타 + s3_key 만.
CREATE TABLE files (
    file_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id    UUID REFERENCES tenants(tenant_id),
    file_name    VARCHAR(255) NOT NULL,
    content_type VARCHAR(100),
    size_bytes   BIGINT,
    s3_key       VARCHAR(500) NOT NULL,
    context      VARCHAR(100),
    uploaded_by  UUID REFERENCES users(user_id),
    created_at   TIMESTAMPTZ DEFAULT now()
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
    provider_type       VARCHAR(30) NOT NULL
        CONSTRAINT chk_provider_type CHECK (provider_type IN ('manufacturer', 'recycler', 'trader', 'miner', 'smelter')),
    smelter_type        VARCHAR(20) CONSTRAINT chk_smelter_type CHECK (smelter_type IN ('rmi', 'private')),  -- smelter 세부 구분(RMI/private)
    core_minerals       JSONB,  -- 소재 구성: 핵심광물 함량(%) {"Li":12.5,"Co":8.0,"Ni":60.0}
    country             VARCHAR(2),  -- 기본정보: 소재 국가(ISO 3166-1 alpha-2)
    address             TEXT,  -- 기본정보: 회사 주소(전체 주소 문자열). 공장 주소(supplier_factories.address)와 별개 — 회사 소재지
    business_reg_doc_url    VARCHAR(500),  -- 필요문서: 사업자등록증(기업정보 서류) 업로드 URL
    environmental_report_url VARCHAR(500),  -- 필요문서: 환경성적서(회원가입 시 수집) 업로드 URL
    self_assessment_doc_url VARCHAR(500),  -- 규제: 실사 자가진단 보고서 업로드 URL(내 기업 정보에서 제출·확인)
    is_unverified       BOOLEAN DEFAULT false,  -- 회원가입: 사업자등록증 미보유로 '미확인 상태' 등록(원청/상위가 검증)
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

-- [테이블 역할] 제3자 정보제공 동의서 = 데이터 계약(Data Contract). [담당: 은지/supplier]
--   원청(consumer)이 협력사(provider)에게 동의서를 메일 발송 → 일정 양식으로 회신 → DB 영속.
--   Catena-X 데이터 주권 모델 정렬: 데이터 계약은 (1) 어떤 데이터(data_scope), (2) 어떤 목적
--   (purpose=ODRL), (3) 누구에게 재공유 가능(third_party_sharing/allowed_recipients=usage policy),
--   (4) 기간/철회(valid_from·to/revocable), (5) 협상 상태(status=계약 협상 로그),
--   (6) 회신 양식 데이터(form_data) + 서명 증빙(document_file_id) + 무결성(agreement_hash)을 담는다.
--   ※ supplier_onboarding.consent_status(단순 동의 플래그)와 달리, 계약 '내용'과 회신 데이터를 보존.
CREATE TABLE data_provision_consents (
    consent_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id         UUID NOT NULL REFERENCES suppliers(supplier_id) ON DELETE CASCADE,  -- 데이터 제공자(협력사)
    tenant_id           UUID REFERENCES tenants(tenant_id),                                  -- 데이터 소비자(원청)

    -- 데이터 계약 조건 (ODRL usage policy 유사) ----------------------------------------
    data_scope          JSONB NOT NULL,        -- 동의 데이터 자산: ["company","contacts","factories","carbon_epd","origin","sub_suppliers"]
    purpose             VARCHAR(50) NOT NULL,  -- 사용 목적(use case): EU_BATTERY / SUPPLY_CHAIN_DD / CSDDD / CONFLICT_MINERALS
    third_party_sharing BOOLEAN DEFAULT FALSE, -- 원청이 제3자(고객사·규제기관)에 재공유 허용 여부
    allowed_recipients  JSONB,                 -- 재공유 허용 대상(고객사/규제기관 식별자 배열)
    valid_from          DATE,
    valid_to            DATE,
    revocable           BOOLEAN DEFAULT TRUE,

    -- 라이프사이클 (Catena-X 계약 협상 상태 = agreement log) ----------------------------
    status              VARCHAR(20) NOT NULL DEFAULT 'requested'
        CONSTRAINT chk_data_consent_status CHECK (status IN ('requested','returned','agreed','rejected','revoked','expired')),
    requested_at        TIMESTAMPTZ,    -- 동의서 메일 발송
    returned_at         TIMESTAMPTZ,    -- 양식 회신 수신
    agreed_at           TIMESTAMPTZ,    -- 서명/동의 체결
    revoked_at          TIMESTAMPTZ,

    -- 서명자(협력사 측) -----------------------------------------------------------------
    signer_name         VARCHAR(100),
    signer_title        VARCHAR(100),
    signer_email        VARCHAR(255),
    signature_method    VARCHAR(20)
        CONSTRAINT chk_consent_sig_method CHECK (signature_method IN ('email_form','e_sign','wet_signature')),

    -- 증빙 + 무결성 ---------------------------------------------------------------------
    form_version        VARCHAR(20),    -- 동의서 양식(데이터 계약) 버전
    form_data           JSONB,          -- 회신받은 구조화 양식 데이터(SSOT 저장)
    document_file_id    UUID REFERENCES files(file_id),  -- 서명된 동의서 PDF
    agreement_hash      VARCHAR(64),    -- 합의 무결성 해시(Catena-X agreement log 유사)
    requested_by        UUID REFERENCES users(user_id),

    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_data_consent_supplier ON data_provision_consents(supplier_id);
CREATE INDEX idx_data_consent_status   ON data_provision_consents(status);


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


-- ============================================================
-- 영역 4. 리스크 프로필 (B 담당)
-- ============================================================

-- [테이블 역할] 협력사별 종합 위험 평점 관리 대장. (가점식 스케일업 모델 반영)
CREATE TABLE supplier_risk_profiles (
    profile_id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id             UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    overall_risk_score      INT DEFAULT 0, -- 가점식 0 ~ 100점 점수계 (↑위험)
    risk_level              VARCHAR(20) DEFAULT 'low' CONSTRAINT chk_profile_risk CHECK (risk_level IN ('low', 'medium', 'high', 'critical')),
    -- [B 속성 상태] 협력사 자가평가 리스크 레벨 (Reliability Score 계산 시 시스템 risk_level과 비교)
    self_reported_risk_level VARCHAR(20) DEFAULT 'unknown' CONSTRAINT chk_self_risk CHECK (self_reported_risk_level IN ('low', 'medium', 'high', 'critical', 'unknown')),

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
    audit_date         DATE DEFAULT CURRENT_DATE,  -- [§5.3] POST 요청에 날짜 없음 → 자동 set (NOT NULL 완화)
    audit_type         VARCHAR(30) CONSTRAINT chk_audit_type CHECK (audit_type IN ('on_site', 'remote', 'document_review', 'third_party')),
    auditor            VARCHAR(255),

    -- [§5 due_diligence 도메인] 실사명·대상 공장·점수·보고서 파일 연결
    audit_name         VARCHAR(255),
    factory_id         UUID REFERENCES supplier_factories(factory_id),
    score              NUMERIC(5,2),
    report_file_id     UUID REFERENCES files(file_id),

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


-- ============================================================
-- 영역 7. 제품 / BOM / 부품 (C 담당 - Ingest 컬럼 전수 동기화)
-- ============================================================

-- [테이블 역할] 완성차 OEM 고객사 마스터. (BMW / Mercedes 등 — products.customer_id 부모)
-- 결정: 제품 3축(고객사·생산기간·조성비) 중 '고객사' 축. ERP_PLM ingest 패턴 일치.
CREATE TABLE customers (
    customer_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    customer_code   VARCHAR(50) UNIQUE NOT NULL,   -- 예: 'BMW', 'MERCEDES'
    customer_name   VARCHAR(255) NOT NULL,
    country         VARCHAR(2),                    -- ISO 3166-1 alpha-2 (예: DE)

    -- [결정 #1] 외부 원천시스템 연동 마크
    source_system   VARCHAR(100) DEFAULT 'ERP_PLM',
    external_id     VARCHAR(255),
    synced_at       TIMESTAMPTZ DEFAULT now(),

    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

-- [테이블 역할] 원청사의 복사본 제품 마스터. (결정 #1 ERP Ingest 일치)
-- 제품 3축 확장: customer_id(고객사) + model_name(차종) + amperage_ah(셀 용량, 단위 Ah).
CREATE TABLE products (
    product_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    product_code    VARCHAR(50) UNIQUE NOT NULL,
    product_name    VARCHAR(255),
    manufacturer_id UUID REFERENCES suppliers(supplier_id),

    -- [테넌트 격리 §0.2] 원청 멀티테넌트 격리를 products까지 확장 (nullable — suppliers/batches.tenant_id 와 동일 정책)
    tenant_id       UUID REFERENCES tenants(tenant_id),

    -- [3축 확장] 고객사(OEM)별 사양 분리 + 차종 + 용량(Ah, kWh 아님)
    customer_id     UUID REFERENCES customers(customer_id),
    model_name      VARCHAR(100),
    amperage_ah     NUMERIC(10,2),

    type            VARCHAR(50),
    specs           JSONB,
    
    -- [결정 #1] 외부 원천시스템 연동 마크
    source_system   VARCHAR(100) DEFAULT 'ERP_PLM',
    external_id     VARCHAR(255),
    synced_at       TIMESTAMPTZ DEFAULT now(),
    
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

-- [테이블 역할] 동일 제품의 생산 Lot/배치 유통 기간별 BOM 버전 이력.
-- [개명] effective_from/to(규제 발효일 성격) → production_from/to(제조·유통 기간 식별).
--        설계 변경이 아닌 'Lot 추적' 목적임을 컬럼명으로 명확화.
CREATE TABLE bom_versions (
    bom_version_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    product_id     UUID REFERENCES products(product_id) ON DELETE CASCADE,
    version_number VARCHAR(20) NOT NULL,
    production_from DATE,
    production_to   DATE,
    status         VARCHAR(20) DEFAULT 'draft' CONSTRAINT chk_bom_status CHECK (status IN ('draft', 'active', 'deprecated')),
    approved_by    UUID REFERENCES users(user_id),
    approved_at    TIMESTAMPTZ,
    
    -- [결정 #1 누락 정형화] 외부 원천시스템 연동 마크 주입
    source_system   VARCHAR(100) DEFAULT 'ERP_PLM',
    external_id     VARCHAR(255),
    synced_at       TIMESTAMPTZ DEFAULT now(),
    
    created_at     TIMESTAMPTZ DEFAULT now()
);

-- [테이블 역할] 7계층 부품 마스터 트리. (Pack-Module-Cell-활물질-전구체/제련-광산, 결정 #1 ERP Ingest 일치)
CREATE TABLE parts (
    part_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    part_code        VARCHAR(50) UNIQUE NOT NULL,
    part_name        VARCHAR(255),
    tier_level       INT, -- 0(Pack) 1(Module) 2(Cell) 3(활물질/CAM) 4(전구체) 5(제련·정제) 6(광산) 분리막/전해질은 material_type
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

-- [테이블 역할] 공급망 맵 그 자체(헤더). 엣지(supply_chain_map)들을 묶는 1급 엔티티.
--   맵 1개 = map_id 1개 = bom_version(제품×Lot) 1개. 완료/전송 상태를 여기서 관리.
CREATE TABLE supply_chain_maps (
    map_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    bom_version_id UUID REFERENCES bom_versions(bom_version_id),
    product_id     UUID REFERENCES products(product_id),
    status         VARCHAR(20) DEFAULT 'building'
        CONSTRAINT chk_scmap_status CHECK (status IN ('building', 'completed')),
    completed_by   UUID REFERENCES users(user_id),
    completed_at   TIMESTAMPTZ,
    created_at     TIMESTAMPTZ DEFAULT now(),
    UNIQUE(bom_version_id)   -- 1 Lot = 1 맵
);

-- [테이블 역할] N차 전방 공급망 흐름의 그래프 '연결(엣지)' 대장. 한 줄 = 1 엣지 = 1 hop.
--   edge_id = 엣지(연결선) PK. map_id = 소속 맵 헤더(supply_chain_maps) FK.
CREATE TABLE supply_chain_map (
    edge_id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    map_id             UUID REFERENCES supply_chain_maps(map_id),
    bom_version_id     UUID REFERENCES bom_versions(bom_version_id),
    parent_supplier_id UUID REFERENCES suppliers(supplier_id),
    child_supplier_id  UUID REFERENCES suppliers(supplier_id), -- 미발견 시 NULL 허용
    part_id            UUID REFERENCES parts(part_id),
    hop_level          INT,  -- 차수 SSOT: 원청(parent NULL)=0 기준 경로 순번(+1 연속). (구 suppliers.tier 대체)
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
    edge_id          UUID REFERENCES supply_chain_map(edge_id) ON DELETE CASCADE,
    factory_id       UUID REFERENCES supplier_factories(factory_id),
    ratio_percentage NUMERIC(5,2),
    volume           NUMERIC(15,4),
    unit             VARCHAR(20)
);

-- [테이블 역할] 공장별 탄소발자국 선언 (EU 배터리법 ART7) [담당: 은지-C]
-- ART7은 공장 단위 탄소집약도 선언 + 산정 방법론 명시 + 검증을 요구한다.
-- 공급사 단위(supplier_manufacturer_details.carbon_intensity)로는 다공장
-- 협력사를 분해할 수 없어, 공장 단위 선언을 1급 엔티티로 둔다.
-- 배치 판정: batches.bom_version_id → supply_chain_map → supply_ratio(공장별 기여%)
--           → 이 테이블의 carbon_intensity 가중평균.
-- 선언 누락 공장이 있으면 ART7상 미충족 → compliance 에서 needs_human_review 처리.
CREATE TABLE factory_carbon_declarations (
    declaration_id   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    factory_id       UUID NOT NULL REFERENCES supplier_factories(factory_id) ON DELETE CASCADE,
    carbon_intensity NUMERIC(10,4) NOT NULL,          -- kg CO2e/kWh (PEF 기반 산정)
    methodology      VARCHAR(50),                     -- 산정 방법론 (예: 'PEF')
    declared_at      DATE NOT NULL,
    valid_from       DATE,
    valid_to         DATE,
    source           VARCHAR(30) NOT NULL DEFAULT 'supplier_declared'
        CONSTRAINT chk_carbon_source CHECK (source IN ('supplier_declared', 'third_party_verified', 'estimated')),
    is_active        BOOLEAN DEFAULT TRUE,
    created_at       TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_factory_carbon_factory ON factory_carbon_declarations(factory_id) WHERE is_active = TRUE;


-- ============================================================
-- 영역 9. 운영 / 배치 (A, E 담당)
-- ============================================================

-- [테이블 역할] LangGraph 에이전트 실행 배치의 스냅샷 상태 저장소. (결정 #1 Ingest 보완 완료)
CREATE TABLE batches (
    batch_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    product_id       UUID REFERENCES products(product_id),
    bom_version_id   UUID REFERENCES bom_versions(bom_version_id),
    tenant_id        UUID REFERENCES tenants(tenant_id),
    received_at      TIMESTAMPTZ DEFAULT now(),
    destination      VARCHAR(2) CONSTRAINT chk_batch_destination CHECK (destination IN ('US', 'EU', 'KR')),
    
    -- [A-7 상태] batch_stage 접두어 일괄 적용 (verification/readiness/issuance는 스코프 축소로 제거)
    current_stage    VARCHAR(50) DEFAULT 'stage_queued'
        CONSTRAINT chk_batch_stage CHECK (
            current_stage IN ('stage_queued', 'stage_extraction', 'stage_geo', 'stage_compliance', 'stage_risk')
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
    embedding        vector(1536) -- Cohere embed-v4 (1536) 대응
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

-- [테이블 역할] 규제별 협력사 필수 제출 필드 명세. C2 gap 계산의 기준 데이터.
-- regulation_id FK + field_name + field_type + provider_type_applicable(해당 업종 필터).
CREATE TABLE regulation_required_fields (
    field_id                   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    regulation_id              UUID NOT NULL REFERENCES regulations(regulation_id) ON DELETE CASCADE,
    field_name                 VARCHAR(100) NOT NULL,  -- 예: 'carbon_intensity', 'mine_coordinates'
    field_type                 VARCHAR(50)  NOT NULL,  -- 예: 'numeric', 'geojson', 'jsonb', 'text'
    provider_type_applicable   JSONB,                  -- 예: ["manufacturer","miner"] — NULL이면 전업종
    is_mandatory               BOOLEAN DEFAULT TRUE
);

-- [테이블 역할 — C-1 신규] 규제 원문 조항 단위 청킹 + 임베딩. (가산적·무회귀: regulations 컬럼 불변)
-- search_regulations()가 regulation_code=UNIQUE 1행에 갇혀 cited_clauses 강제가 데이터로
-- enforce되지 않던 문제를 해결한다. citation(조항번호)+content(조항원문) 단위로 RAG 검색 대상을 만든다.
CREATE TABLE regulation_clauses (
    clause_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    regulation_id    UUID NOT NULL REFERENCES regulations(regulation_id) ON DELETE CASCADE,
    citation         VARCHAR(100) NOT NULL,  -- 예: 'Art.7(2)', 'Annex XII §3'
    content          TEXT NOT NULL,          -- 조항 원문(또는 정제된 조항 텍스트)
    embedding_status VARCHAR(20) DEFAULT 'pending'
        CONSTRAINT chk_clause_embedding_status CHECK (embedding_status IN ('pending', 'indexed')),
    embedding        vector(1536), -- regulations.embedding과 동일 차원(Cohere embed-v4)
    created_at       TIMESTAMPTZ DEFAULT now(),

    CONSTRAINT uq_regulation_clause_citation UNIQUE (regulation_id, citation)
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

-- [테이블 역할] 데이터 요청 및 수령 SLA 추적 관리 대장
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
    -- submit 시 생성된 batch_id 보관(파이프라인 enqueue 키). ORM DataRequestLog.batch_id 와 정합(미적용 DDL 반영).
    -- [REVERT-NON-SUPPLIER] supplier 외(schema) — 자료요청 목록 500 수정용. 소유자 적용 전제.
    batch_id            UUID,  -- [REVERT-NON-SUPPLIER] 이 줄 주석처리

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
    -- 허용값 SSOT = data_gateway._DOC_CATEGORY_ENUM (AI 분류 + supplier_document_ingest 매핑이 쓰는 값과 1:1).
    -- enum 확장 시 양쪽을 함께 갱신한다.
    doc_category  VARCHAR(50)
        CONSTRAINT chk_doc_category CHECK (doc_category IN (
            'business_registration', 'origin_certificate', 'dd_audit_report',
            'product_spec', 'manufacturing_process_doc',
            'carbon_footprint_declaration', 'recycled_content_report', 'mining_permit',
            'mineral_production_report', 'safety_health_report', 'environmental_impact_assessment',
            'smelter_identification', 'rmap_certificate', 'cmrt_declaration',
            'cbam_declaration', 'uflpa_documentation', 'other'
        )),
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
    detected_document_type VARCHAR(255),       -- AI가 분류한 문서 유형 (원어 표기)
    evidence_summary       TEXT,               -- 문서 내용 1-2문장 요약
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
            'hitl_queue', 'notification_queue',
            'batch_pipeline_queue', 'dead_letter_queue'
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
-- 뷰 (Views) 정의
-- ============================================================

-- [뷰 역할] 공급망 허브 중앙 지도 컬러링 지원 뷰. (수정된 접두어 반영)
-- [축 정의] 두 축을 모두 노출한다 (ADR 분리축):
--   · hop_level = 공급망 차수 (원청 0 기준 경로 순번, +1 연속)
--   · bom_depth = 부품 tier  (parts.tier_level, 0-base: Pack=0 … 광산=6)
--   두 값은 독립축이다. 겸업/계층건너뜀 시 hop_level != bom_depth 일 수 있음.
CREATE OR REPLACE VIEW v_supply_chain_node_status AS
SELECT
    scm.map_id,
    scm.parent_supplier_id,
    scm.child_supplier_id,
    scm.part_id,
    scm.link_status,
    s.company_name,
    s.company_name_en,
    s.provider_type,
    scm.hop_level,              -- 경로 순번(원청 0 기준 +1 연속)
    p.tier_level        AS bom_depth,   -- 부품 tier(0-base, Pack=0 … 광산=6)
    s.status            AS supplier_status,
    s.risk_level,
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
CREATE INDEX idx_suppliers_provider_type ON suppliers(provider_type);
CREATE INDEX idx_scm_hop_level           ON supply_chain_map(hop_level);
CREATE INDEX idx_suppliers_parent        ON suppliers(parent_supplier_id);
CREATE INDEX idx_suppliers_status        ON suppliers(status);
CREATE INDEX idx_suppliers_risk_level    ON suppliers(risk_level);
CREATE INDEX idx_audit_records_factory   ON supplier_audit_records(factory_id) WHERE factory_id IS NOT NULL;
CREATE INDEX idx_files_tenant            ON files(tenant_id);
CREATE INDEX idx_factories_location      ON supplier_factories USING GIST(location);
CREATE INDEX idx_miner_coords            ON supplier_miner_details USING GIST(mine_coordinates);

-- 2) 원산지 및 자재 트리 인덱스
CREATE INDEX idx_parts_parent            ON parts(parent_part_id);
CREATE INDEX idx_parts_hs_code           ON parts(hs_code);

-- [신설] 제품 3축(고객사·생산기간·BOM버전) 다차원 룩업 인덱스 4종
CREATE INDEX idx_products_customer       ON products(customer_id);
CREATE INDEX idx_products_model          ON products(customer_id, model_name);
CREATE INDEX idx_products_tenant         ON products(tenant_id);
CREATE INDEX idx_bom_versions_product    ON bom_versions(product_id, status);
CREATE INDEX idx_bom_versions_period     ON bom_versions(production_from, production_to);

-- 3) 배치 및 벡터 RAG 코사인 인덱스
CREATE INDEX idx_batches_status          ON batches(status);
CREATE INDEX idx_batches_tenant_status   ON batches(tenant_id, status);
CREATE INDEX idx_regulations_embedding   ON regulations USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_regulation_clauses_embedding ON regulation_clauses USING hnsw (embedding vector_cosine_ops); -- C-1 신규
CREATE INDEX idx_regulation_clauses_regulation_id ON regulation_clauses(regulation_id); -- C-1 신규
CREATE INDEX idx_regulation_clauses_pending   ON regulation_clauses(regulation_id) WHERE embedding_status = 'pending'; -- C-1 신규: 임베딩 시드 배치 조회용
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
-- KIRA 플랫폼 2차 확장 스키마 (프로세스 정의서 TO-BE 전수 수용)
-- 선결과제: user/org · report(결재) · watchlist/소급 · 당국제출 · 대외전송 · 사람결정 증적
-- ============================================================

-- ------------------------------------------------------------
-- [지혜-A] 결재(report) 도메인 — 다단계 결재. manager_id 는 users 테이블에 통합됨.
-- ------------------------------------------------------------
CREATE TABLE reports (
    report_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    batch_id        UUID REFERENCES batches(batch_id) ON DELETE CASCADE,
    title           VARCHAR(255) NOT NULL,
    description     TEXT,
    requester_id    UUID NOT NULL REFERENCES users(user_id),
    status          VARCHAR(30) DEFAULT 'draft'
        CONSTRAINT chk_report_status CHECK (status IN ('draft', 'approval_pending', 'fully_approved', 'returned')),
    current_step    INT DEFAULT 1,

    -- [P3 §3.2·3.3 audit/report 확장]
    type            VARCHAR(50) DEFAULT 'compliance',  -- 보고서 종류 (compliance / sustainability / due_diligence 등)
    submitted_at    TIMESTAMPTZ,                       -- draft→approval_pending 전이 시점
    severity        VARCHAR(20) DEFAULT 'medium',      -- 결재함 표시용 심각도
    deadline        TIMESTAMPTZ,                       -- 결재 기한
    key_points      JSONB DEFAULT '[]',                -- 결재함 표시용 핵심 포인트 배열

    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE report_approval_steps (
    step_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    report_id       UUID NOT NULL REFERENCES reports(report_id) ON DELETE CASCADE,
    step_number     INT NOT NULL,
    approver_id     UUID NOT NULL REFERENCES users(user_id),
    status          VARCHAR(30) DEFAULT 'pending'
        CONSTRAINT chk_step_status CHECK (status IN ('pending', 'approved', 'rejected')),
    decision_text   TEXT,
    decided_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE(report_id, step_number)
);

-- ------------------------------------------------------------
-- [은지-C] Watchlist (UFLPA/제재명단) + 소급 재검증
-- ⚠️수정#2: matched_supplier_id 추가 — 등재 entity ↔ 우리 공급사 매칭(자동 소급 강등의 연결고리).
--          텍스트 이름만으론 자동 대조가 약해 supplier FK 를 둠. 미매칭 시 NULL(텍스트 후보만).
-- ------------------------------------------------------------
CREATE TABLE watchlists (
    watchlist_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_name         VARCHAR(255) NOT NULL,
    country             VARCHAR(2),
    reason              TEXT,
    matched_supplier_id UUID REFERENCES suppliers(supplier_id) ON DELETE SET NULL, -- ⚠️#2 우리 공급사 매칭(소급 강등 연결)
    source              VARCHAR(30) DEFAULT 'UFLPA_ENTITY_LIST'
        CONSTRAINT chk_watchlist_source CHECK (source IN ('UFLPA_ENTITY_LIST', 'SANCTION', 'FEOC', 'MANUAL')),
    listed_at           TIMESTAMPTZ DEFAULT now(),
    is_active           BOOLEAN DEFAULT TRUE,
    created_at          TIMESTAMPTZ DEFAULT now()
);

-- 소급 재검증 이력 (출처9). trigger_source_id 는 watchlists/regulations 를 가리키는 polymorphic — FK 없음(의도).
CREATE TABLE reverification_logs (
    reverification_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    trigger_type          VARCHAR(30) NOT NULL
        CONSTRAINT chk_reverify_trigger CHECK (trigger_type IN ('watchlist_update', 'regulation_amendment', 'manual')),
    trigger_source_id     UUID,         -- ⚠️#3 polymorphic(watchlist_id 또는 regulation_id) → FK 없음(의도)
    status                VARCHAR(20) DEFAULT 'running'
        CONSTRAINT chk_reverify_status CHECK (status IN ('running', 'completed', 'failed')),
    affected_batch_count  INT DEFAULT 0,
    changed_verdict_count INT DEFAULT 0, -- 재검증 결과 판정이 달라진(위반 강등 등) 건수
    started_at            TIMESTAMPTZ DEFAULT now(),
    completed_at          TIMESTAMPTZ,
    results_summary       JSONB         -- {batch_id: 'old -> new'} 요약
);

-- ------------------------------------------------------------
-- [차윤-X] 외부 당국 시스템 제출 + 참조번호 (EUDR TRACES / IRA 30D / CBP)
-- ------------------------------------------------------------
CREATE TABLE authority_submissions (
    submission_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    batch_id           UUID REFERENCES batches(batch_id) ON DELETE CASCADE,
    product_id         UUID REFERENCES products(product_id) ON DELETE CASCADE,
    authority_type     VARCHAR(30) NOT NULL
        CONSTRAINT chk_auth_type CHECK (authority_type IN ('TRACES_NT', 'IRA_30D', 'CBP_DETENTION')),
    reference_number   VARCHAR(100), -- TRACES-NT 고유 참조번호 / IRS Safe Harbor 등록번호 등
    status             VARCHAR(20) DEFAULT 'pending'
        CONSTRAINT chk_auth_submission_status CHECK (status IN ('pending', 'submitted', 'approved', 'failed')),
    payload            JSONB,        -- 당국 전송 원본 JSON 스냅샷
    response_metadata  JSONB,        -- 당국 수신 응답값
    submitted_at       TIMESTAMPTZ,
    created_at         TIMESTAMPTZ DEFAULT now()
);

-- [차윤-X] 대외 전송 로그 + 도달확인(Ack) (X-10 정밀 정합). recipient_id 는 customer/supplier/authority polymorphic — FK 없음(의도).
CREATE TABLE transmission_logs (
    transmission_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    batch_id            UUID REFERENCES batches(batch_id) ON DELETE SET NULL,
    sender_id           UUID REFERENCES users(user_id), -- 최종 발송 주체(원청 담당자)
    recipient_type      VARCHAR(20) NOT NULL
        CONSTRAINT chk_recipient_type CHECK (recipient_type IN ('customer', 'supplier', 'authority')),
    recipient_id        UUID,         -- polymorphic(customers/suppliers/당국) → FK 없음(의도)
    recipient_email     VARCHAR(255) NOT NULL,
    transmission_type   VARCHAR(30) NOT NULL
        CONSTRAINT chk_trans_type CHECK (
            transmission_type IN ('compliance_summary', 'rework_request', 'post_violation_notice', 'customs_response')
        ),
    status              VARCHAR(20) DEFAULT 'sent'
        CONSTRAINT chk_trans_status CHECK (status IN ('sent', 'delivered', 'failed', 'acknowledged')),
    payload_summary     TEXT,
    attachment_urls     JSONB,        -- 동반 PDF/증거묶음 URL 배열
    ack_token           VARCHAR(64) UNIQUE, -- 수신확인 링크 검증용 유니크 토큰
    sent_at             TIMESTAMPTZ DEFAULT now(),
    delivered_at        TIMESTAMPTZ,
    acknowledged_at     TIMESTAMPTZ   -- Ack 고리 완성 시각
);

-- ------------------------------------------------------------
-- [영수-D] CBP 억류 통지(Detention) + 반증 대응 케이스 (회사경계·당국 대응)
-- ------------------------------------------------------------
CREATE TABLE detention_cases (
    case_id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    batch_id             UUID REFERENCES batches(batch_id) ON DELETE CASCADE,
    notice_number        VARCHAR(100) UNIQUE NOT NULL,
    status               VARCHAR(20) DEFAULT 'notified'
        CONSTRAINT chk_detention_status CHECK (status IN ('notified', 'preparing_package', 'submitted', 'released', 'seized')),
    detained_at          TIMESTAMPTZ NOT NULL,
    due_date             TIMESTAMPTZ NOT NULL, -- 억류일 +30일 마감(SLA)
    evidence_package_url VARCHAR(500),
    submitted_at         TIMESTAMPTZ,
    resolution_note      TEXT,
    created_at           TIMESTAMPTZ DEFAULT now()
);

-- ------------------------------------------------------------
-- [은지-C] 실사 정책 문서 (CSDDD·배터리 규제 대응)
-- ------------------------------------------------------------
CREATE TABLE due_diligence_policies (
    policy_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title              VARCHAR(255) NOT NULL,
    version            VARCHAR(20) NOT NULL,
    status             VARCHAR(20) DEFAULT 'draft'
        CONSTRAINT chk_policy_status CHECK (status IN ('draft', 'active', 'archived')),
    document_url       VARCHAR(500) NOT NULL,
    created_by         UUID REFERENCES users(user_id),
    published_at       TIMESTAMPTZ,
    created_at         TIMESTAMPTZ DEFAULT now()
);

-- ------------------------------------------------------------
-- [지혜-A·차윤-E·은진] 결정 시점 데이터 스냅샷 + 부인방지 서명 (사람결정 증적 — 책임증명 비대칭 해소)
-- step_id 는 report_approval_steps 또는 hitl_reviews 시점 polymorphic — FK 없음(의도).
-- ------------------------------------------------------------
CREATE TABLE audit_data_snapshots (
    snapshot_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    batch_id           UUID REFERENCES batches(batch_id) ON DELETE CASCADE,
    step_id            UUID,         -- polymorphic(report_approval_steps.step_id 또는 hitl_reviews.review_id) → FK 없음(의도)
    decided_by         UUID REFERENCES users(user_id), -- 승인 누른 사람(부인방지 주체)
    snapshot_data      JSONB NOT NULL, -- 승인 순간의 active BOM·협력사·규제판정 JSON 동결
    signature_hash     VARCHAR(64),  -- 무결성 검증 해시
    created_at         TIMESTAMPTZ DEFAULT now()
);

-- TO-BE 확장 인덱스
CREATE INDEX idx_reports_requester ON reports(requester_id);
CREATE INDEX idx_report_steps_approver ON report_approval_steps(approver_id, status);
CREATE INDEX idx_watchlists_entity ON watchlists(entity_name) WHERE is_active = TRUE;
CREATE INDEX idx_watchlists_matched ON watchlists(matched_supplier_id) WHERE matched_supplier_id IS NOT NULL;
CREATE INDEX idx_auth_submissions_batch ON authority_submissions(batch_id);
CREATE INDEX idx_trans_logs_ack_token ON transmission_logs(ack_token) WHERE ack_token IS NOT NULL;
CREATE INDEX idx_detention_cases_due ON detention_cases(due_date) WHERE status != 'released';
CREATE INDEX idx_reverify_logs_status ON reverification_logs(status);
CREATE INDEX idx_audit_snapshots_batch ON audit_data_snapshots(batch_id);

-- ============================================================
-- 영역 13. 에이전트 판정 결과 저장 (D·E 담당)
-- ============================================================

-- [테이블 역할] 배치별 지리 감사 판정 결과 저장. (D R5 — 영수)
--   geo_audit_node가 실행될 때마다 upsert. batch_id UNIQUE로 최신 결과 유지.
CREATE TABLE geo_audit_results (
    audit_result_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    batch_id        UUID UNIQUE REFERENCES batches(batch_id) ON DELETE CASCADE,
    risk_detected   BOOLEAN DEFAULT FALSE,
    risk_flags      JSONB DEFAULT '[]', -- ["xinjiang", "country_mismatch", ...]
    detected_risks  JSONB DEFAULT '[]', -- GeoRiskDetectedEvent 전체 목록
    created_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_geo_audit_results_batch ON geo_audit_results(batch_id);


-- ============================================================
-- 마스터 데이터 시드 (Regulations - 최종 9개, IRA/FEOC 스코프 축소로 제외)
-- ============================================================

INSERT INTO regulations (name, regulation_code, region, version, effective_from, description, embedding_status)
VALUES
('EU Deforestation Regulation',           'EUDR',             'EU', '2023/1115', '2024-12-30', 'EU 산림파괴방지법(EUDR). 소고기·코코아·커피·팜유·대두·목재·고무 등 7대 원자재 및 관련 제품의 EU 수출입 시, 2020년 12월 31일 이후 산림파괴 지역에서 생산되지 않았음을 증명하는 GPS 좌표 기반 실사 자료를 제출해야 한다. FSC 인증 등 제3자 인증도 보조 증빙으로 활용 가능. 위반 시 매출액의 4% 또는 최소 €150만 과징금.', 'pending'),
('Corporate Sustainability Due Diligence', 'CSDDD',           'EU', '2024/1760', '2027-01-01', 'EU 기업 지속가능성 실사 지침(CSDDD). 종업원 1,000명 이상·매출 €4.5억 초과 기업은 자사 및 공급망 전반에 걸쳐 아동노동·강제노동·환경훼손 등 인권·환경 리스크를 식별·예방·완화해야 한다. 실사 계획 수립, 고충처리 절차 운영, 연간 공시 의무 포함. 위반 시 매출액의 5% 과징금.', 'pending'),
('Uyghur Forced Labor Prevention Act',    'UFLPA',            'US', '2021',      '2022-06-21', '미국 위구르강제노동방지법(UFLPA). Section 3(a)(1)에 따라 신장위구르자치구(Xinjiang)에서 생산·제조·채굴된 모든 물품은 강제노동으로 생산된 것으로 간주(rebuttable presumption)하며 수입이 금지된다. 수입자가 강제노동 미사용을 명확하고 설득력 있는 증거로 반증하지 못하면 CBP가 통관을 거부한다. 공급망 내 신장 원산지 원자재 포함 여부 추적 의무.', 'pending'),
('EU Battery Regulation',                 'EU_BATTERY',       'EU', '2023/1542', '2025-02-18', 'EU 배터리법(2023/1542) Annex XII 재활용 함량 기준. 2031년부터 산업용·EV 배터리에 코발트(Co) 16%, 납(Pb) 85%, 리튬(Li) 6%, 니켈(Ni) 6% 이상의 재활용 원료 함량 의무화. 제조사는 배터리 여권(Battery Passport)에 재활용 함량 비율 및 원산지를 기재해야 하며, 제3자 검증을 거쳐야 한다. 미달 시 EU 시장 출시 금지.', 'pending'),
('EU Battery Regulation Art.7',           'EU_BATTERY_ART7',  'EU', '2023/1542', '2025-02-18', 'EU 배터리법 Article 7 탄소발자국 선언 의무. LMT·EV·산업용 배터리는 전 생명주기(원료 채굴~제조~운송) 탄소발자국을 kgCO2eq/kWh 단위로 산출해 신고해야 한다. Annex II 기준: 100 kgCO2eq/kWh 초과 시 최고 등급(A) 취득 불가, 75 kgCO2eq/kWh 초과 시 경고 등급. 선언 누락 또는 허위 신고 시 EU 시장 출시 금지 및 과징금 부과.', 'pending'),
('EU Battery Regulation Art.47',          'EU_BATTERY_ART47', 'EU', '2023/1542', '2023-07-28', 'EU 배터리법 Article 47 공급망 실사 의무. 연간 배터리 생산량 일정 규모 이상 사업자는 코발트·천연흑연·리튬·니켈 등 핵심 원자재의 공급망 리스크를 식별·관리·공시해야 한다. OECD 다국적기업 가이드라인 및 UN 기업과 인권 이행원칙 준수 요구. 실사 정책 수립, 공급업체 감사, 연간 보고서 제출 의무.', 'pending'),
('Carbon Border Adjustment Mechanism',    'CBAM',             'EU', '2023/956',  '2026-01-01', 'EU 탄소국경조정제도(CBAM). 철강·알루미늄·시멘트·비료·전력·수소 6개 섹터 수입품에 대해 EU ETS 탄소가격과 원산지국 탄소가격의 차액을 CBAM 인증서로 납부해야 한다. 2024~2025년 전환기(보고 의무만), 2026년부터 인증서 구매 의무 본격 시행. 내재 탄소배출량 산정·보고·검증(MRV) 체계 구축 필요.', 'pending'),
('EU Conflict Minerals Regulation',       'CONFLICT_MINERALS','BOTH', '2017/821',  '2021-01-01', 'EU 분쟁광물 규정(2017/821). 주석(Sn)·탄탈럼(Ta)·텅스텐(W)·금(Au) 4대 광물 및 관련 금속을 분쟁·고위험 지역에서 연간 일정량 이상 수입하는 EU 내 제련소·정제소는 OECD 실사 가이드라인에 따라 공급망 실사를 수행하고 제3자 감사를 받아야 한다. 감사 결과 및 공급망 정보 연간 공시 의무.', 'pending'),
('Critical Raw Materials Act',            'CRMA',             'EU', '2024/1252', '2024-05-23', 'EU 핵심원자재법(CRMA). 리튬·코발트·니켈·망간 등 34종 핵심원자재의 공급망 다변화·자급률 제고를 위해 2030년까지 EU 역내 채굴 10%, 가공 40%, 재활용 15% 목표를 설정한다. 대기업은 전략적 핵심원자재 공급망 취약성 감사 의무. 인·허가 절차 간소화 및 전략 프로젝트 지정 제도 포함.', 'pending');


-- ============================================================
-- 마스터 데이터 시드 (regulation_required_fields — C-2)
-- ============================================================
-- [C-2 — 은지, 2026-06-30]
-- get_required_fields()가 더미(_TEMP_REQUIRED_FIELDS)를 버리고 이 테이블을 조회한다.
-- regulations.regulation_code → regulation_id 서브쿼리로 FK를 해결해 멱등 시드.
-- 기존 더미 데이터(EU_BATTERY_ART7·EUDR·UFLPA)를 실데이터로 교체하고
-- 나머지 규제의 핵심 필수 필드도 함께 추가한다.
--
-- [REGULATION_BY_DESTINATION 정합성 기준]
-- compliance.py의 REGULATION_BY_DESTINATION dict와 동일한 규제 코드만 시드.
-- CBAM·CONFLICT_MINERALS·CRMA는 _stub_passed_judge(범위 외 자동통과)라
-- 필수 필드 매트릭스 불필요 → 시드 제외.

INSERT INTO regulation_required_fields
    (regulation_id, field_name, field_type, provider_type_applicable, is_mandatory)
SELECT r.regulation_id, v.field_name, v.field_type, v.provider_type_applicable::jsonb, v.is_mandatory
FROM regulations r
JOIN (VALUES
    -- EU_BATTERY_ART7: 탄소발자국 선언 필수 필드
    ('EU_BATTERY_ART7', 'carbon_intensity',             'numeric', '["manufacturer"]',          TRUE),
    ('EU_BATTERY_ART7', 'factory_carbon_declarations',  'jsonb',   '["manufacturer"]',          TRUE),
    ('EU_BATTERY_ART7', 'carbon_footprint_methodology', 'text',    '["manufacturer"]',          FALSE),

    -- EU_BATTERY: 재활용 함량 필수 필드
    ('EU_BATTERY',      'recycled_content_ratio',       'numeric', '["recycler","manufacturer"]', TRUE),
    ('EU_BATTERY',      'recycling_certification',      'text',    '["recycler"]',              FALSE),

    -- EU_BATTERY_ART47: 공급망 실사 정책
    ('EU_BATTERY_ART47','due_diligence_policy_url',     'text',    '["manufacturer"]',          TRUE),
    ('EU_BATTERY_ART47','audit_report_url',             'text',    '["manufacturer","miner"]',  FALSE),

    -- EUDR: 산림파괴 비발생 증빙
    ('EUDR',            'mine_coordinates',             'geojson', '["miner"]',                 TRUE),
    ('EUDR',            'deforestation_free_cert_url',  'text',    '["miner","trader"]',        TRUE),
    ('EUDR',            'gps_polygon',                  'geojson', '["miner"]',                 FALSE),

    -- CSDDD: 인권실사 의무
    ('CSDDD',           'human_rights_policy_url',      'text',    '["manufacturer","miner"]',  TRUE),
    ('CSDDD',           'grievance_mechanism_url',      'text',    '["manufacturer"]',          FALSE),

    -- UFLPA: 신장 원산지 추적
    ('UFLPA',           'origin_country',               'text',    '["miner","trader"]',        TRUE),
    ('UFLPA',           'geo_risk_flags',               'jsonb',   '["miner"]',                 FALSE),
    ('UFLPA',           'supply_chain_traceability',    'text',    '["miner","trader"]',        TRUE),

    -- IRA: FEOC 지분 검증
    ('IRA',             'feoc_direct_ownership',        'numeric', '["manufacturer","trader"]', TRUE),
    ('IRA',             'feoc_indirect_ownership',      'numeric', '["manufacturer","trader"]', TRUE),
    ('IRA',             'ownership_disclosure_doc_url', 'text',    '["manufacturer"]',          FALSE)
) AS v(regulation_code, field_name, field_type, provider_type_applicable, is_mandatory)
    ON r.regulation_code = v.regulation_code
ON CONFLICT DO NOTHING;