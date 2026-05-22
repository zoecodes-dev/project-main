-- ============================================================
-- KIRA Compliance Intelligence Platform
-- 공급망 데이터 백본 + AI 자동화 레이어
-- PostgreSQL 16 + PostGIS + pgvector
--
-- 단일 시작 스키마 파일 — 이 파일 하나로 DB를 초기화한다.
-- 실행 순서: 확장 → 영역 1~12(테이블) → 트리거 함수 → 뷰 → 인덱스
-- ============================================================


-- ============================================================
-- 확장 활성화
-- ============================================================

CREATE EXTENSION IF NOT EXISTS postgis;     -- 공장·광산 좌표(GEOMETRY), ST_DWithin 등 공간 쿼리
CREATE EXTENSION IF NOT EXISTS vector;      -- 규제 문서 RAG용 pgvector 임베딩
CREATE EXTENSION IF NOT EXISTS "uuid-ossp"; -- uuid_generate_v4() 기본키 생성


-- ============================================================
-- 영역 1. 테넌트 / 사용자 / 권한
-- 담당: 팀원 A (공통 인프라)
-- ============================================================

-- [테이블 역할] 멀티테넌트 SaaS의 최상위 조직 단위.
--               원청사(OEM) 1개가 1개의 tenant. 모든 공급망 데이터는 tenant 하위에 격리.
CREATE TABLE tenants (
    -- [역할] 테넌트 고유 식별자. 모든 하위 테이블의 tenant_id FK 기준.
    tenant_id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- [역할] 원청사 법인명
    company_name        VARCHAR(255) NOT NULL,

    -- [역할] 사업자등록번호. 테넌트 중복 가입 방지용 UNIQUE 제약.
    business_reg_no     VARCHAR(50)  UNIQUE,

    -- [역할] 구독 상태. active 이외 상태이면 API 접근 차단.
    --        허용값: active / suspended / trial
    subscription_status VARCHAR(20)  DEFAULT 'active',

    joined_at           TIMESTAMPTZ  DEFAULT now(),
    created_at          TIMESTAMPTZ  DEFAULT now(),
    updated_at          TIMESTAMPTZ  DEFAULT now()
);

-- [테이블 역할] 플랫폼 사용자 계정. 원청사 내부 담당자와 협력사 담당자 모두 포함.
--               role에 따라 접근 가능한 공급망 범위가 달라짐(view_permissions 연동).
CREATE TABLE users (
    -- [역할] 사용자 고유 식별자
    user_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- [역할] 소속 테넌트(원청사) FK
    tenant_id      UUID REFERENCES tenants(tenant_id) ON DELETE CASCADE,

    -- [역할] 로그인 이메일. 전체 플랫폼에서 UNIQUE.
    email          VARCHAR(255) UNIQUE NOT NULL,

    -- [역할] bcrypt 등 해시된 비밀번호
    password_hash  VARCHAR(255) NOT NULL,

    name           VARCHAR(100),

    -- [역할] 역할 기반 접근 제어(RBAC) 기준 컬럼.
    --        허용값: admin / owner_esg / owner_purchasing / supplier_ceo / supplier_esg
    role           VARCHAR(50),

    is_active      BOOLEAN DEFAULT TRUE,
    last_login_at  TIMESTAMPTZ,
    created_at     TIMESTAMPTZ DEFAULT now()
);

-- [테이블 역할] 사용자별 공급망 열람 범위 제어.
--               옆 라인 차단(can_view_siblings=FALSE 기본값)이 핵심 보안 요구사항.
--               depth_limit으로 몇 차까지 볼 수 있는지 제한.
CREATE TABLE view_permissions (
    permission_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- [역할] 권한을 부여받는 사용자 FK
    user_id              UUID REFERENCES users(user_id) ON DELETE CASCADE,

    -- [역할] 이 사용자가 열람 가능한 협력사 ID
    viewable_supplier_id UUID,

    -- [역할] 상위 협력사 데이터 열람 가능 여부
    can_view_parent      BOOLEAN DEFAULT FALSE,

    -- [역할] 하위 협력사 데이터 열람 가능 여부
    can_view_children    BOOLEAN DEFAULT FALSE,

    -- [역할] 같은 계층의 다른 라인(옆 라인) 열람 가능 여부. 기본 차단.
    can_view_siblings    BOOLEAN DEFAULT FALSE,

    -- [역할] 하위 몇 차(Tier)까지 열람 가능한지 제한. 1이면 직접 연결 협력사만.
    depth_limit          INT DEFAULT 1,

    granted_by           UUID REFERENCES users(user_id),
    granted_at           TIMESTAMPTZ DEFAULT now()
);


-- ============================================================
-- 영역 2. 협력사 마스터 (Provider Type별 CTI)
-- 담당: 팀원 B (Supplier Domain)
-- ============================================================

-- [테이블 역할] 공급망에 참여하는 모든 협력사의 마스터 데이터.
--               supplier_type에 따라 영역 3의 CTI 상세 테이블과 연결됨.
CREATE TABLE suppliers (
    -- [역할] 협력사 고유 식별자
    supplier_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- [역할] 소속 테넌트(원청사) FK. 멀티테넌트 데이터 격리 기준.
    tenant_id           UUID REFERENCES tenants(tenant_id),

    -- [역할] 내부 관리용 기본 표시명 (한글 또는 영문 혼용 가능)
    company_name        VARCHAR(255) NOT NULL,

    -- [역할] 협력사 영문 공식 명칭.
    --        공급망 맵 노드 라벨·DPP 문서·영문 이메일 등 외부 출력 시 사용.
    --        프론트엔드 SupplierName.nameEn 에 대응.
    company_name_en     VARCHAR(255),

    -- [역할] 협력사 한글 공식 명칭.
    --        국내 협력사 문서 및 한글 UI 표기용. 프론트엔드 SupplierName.nameKo 에 대응.
    company_name_ko     VARCHAR(255),

    -- [역할] 협력사 영문 약칭.
    --        공급망 맵처럼 공간이 좁은 UI에서 축약 표시.
    --        예: 'Hanyang Cell Manufacturing Co., Ltd.' → 'Hanyang Cell'
    short_name_en       VARCHAR(100),

    -- [역할] 협력사 한글 약칭. 예: '한양셀 제조(주)' → '한양셀'
    short_name_ko       VARCHAR(100),

    -- [역할] 대표자(CEO) 이름
    ceo_name            VARCHAR(100),

    -- [역할] 사업자등록번호 (한국 기준)
    business_reg_no     VARCHAR(50),

    -- [역할] 법인등록번호
    corporate_reg_no    VARCHAR(50),

    -- [역할] D-U-N-S 번호. 글로벌 기업 식별자, UFLPA 실사 시 활용.
    duns_number         VARCHAR(20),

    -- [역할] 세금 식별번호 (국가별 상이)
    tax_number          VARCHAR(50),

    website             VARCHAR(255),

    -- [역할] 협력사 사업 유형. CTI 상세 테이블 분기 기준이자 FEOC·EUDR 규제 적용 범위 결정 키.
    --        허용값: manufacturer / recycler / trader / miner
    supplier_type       VARCHAR(30) NOT NULL,

    -- [역할] 원청사 기준 공급망 차수(Tier). 1=직접 협력사, 5=광산.
    tier                INT,

    -- [역할] 상위 협력사 자기참조 FK. 공급망 트리 구성용.
    parent_supplier_id  UUID REFERENCES suppliers(supplier_id),

    -- [역할] 협력사 설립 연도.
    --        CSDDD·LKSG 사업 지속성 판단 기준. 프론트엔드 SupplierExtended.establishedYear 대응.
    established_year    INT,

    -- [역할] 전체 임직원 수.
    --        CSDDD 적용 기업 규모 기준(EU 250인 이상) 판단에 사용.
    employee_count      INT,

    -- [역할] 데이터 완성도 점수 0~100.
    --        onboarding_data_requirements 기준 필드 충족률. DPP Readiness 계산 입력값.
    completeness_score  INT DEFAULT 0,

    -- [역할] 현재 워크플로우 상태.
    --        공급망 허브 노드 컬러·협력사 리스트 필터 기준 컬럼.
    --        상태 전이는 반드시 domains/supplier/state_machine.py 를 통해서만 수행.
    --        허용값: pending / requested / in_progress / review /
    --                verified / violation / suspended
    status              VARCHAR(20) DEFAULT 'pending',

    -- [역할] 종합 리스크 레벨. supplier_risk_profiles.overall_risk_score를 구간 변환한 요약값.
    --        공급망 맵 노드 컬러 및 Dashboard High Risk 탭 필터에 직접 사용.
    --        RiskDetected 이벤트 수신 시 Risk Domain이 자동 업데이트.
    --        허용값: low / medium / high / critical
    risk_level          VARCHAR(20) DEFAULT 'low',

    -- [역할] IRA FEOC(Foreign Entity of Concern) 적격 여부 요약.
    --        협력사 리스트 FEOC 필터 및 Supplier Workspace FEOC 탭 뱃지 표시용.
    --        상세 지분율은 supplier_risk_profiles 테이블에 저장하고 이 컬럼은 빠른 조회용 캐시.
    --        Verification Domain FEOC 룰 실행 결과로 자동 갱신.
    --        허용값: eligible / ineligible / under_review / unknown
    feoc_status         VARCHAR(20) DEFAULT 'unknown',

    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now()
);

-- [테이블 역할] 협력사 담당자 연락처. 협력사당 여러 담당자 등록 가능(부서별, 공장별).
--               Notification 발송 시 수신자 결정 및 SLA 긴급 연락에 사용.
CREATE TABLE supplier_contacts (
    contact_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id   UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,

    -- [역할] 담당자가 속한 공장 FK.
    --        공장별 담당자가 다를 경우 분리 조회용(포항공장장 vs 광양공장장).
    --        NULL이면 본사 담당자.
    factory_id    UUID, -- supplier_factories 생성 후 FK 추가 (하단 ALTER 불필요 — 이미 통합)

    name          VARCHAR(100),

    -- [역할] 담당자 영문 이름. 외국 협력사 담당자 표기 및 영문 문서 출력용.
    name_en       VARCHAR(100),

    -- [역할] 역할 구분. CEO/ESG/Sales/Purchasing 등 수신자 필터링 기준.
    role          VARCHAR(50),

    -- [역할] 소속 부서. 부서별 Notification 수신자 분리에 활용.
    department    VARCHAR(100),

    email         VARCHAR(255),
    phone         VARCHAR(50),

    -- [역할] 휴대폰 번호. SLA 긴급 연락·현장 담당자 직통용. phone(사무실 대표)과 분리.
    mobile        VARCHAR(50),

    is_primary    BOOLEAN DEFAULT FALSE,

    -- [역할] 주요 사용 언어. 다국어 이메일 본문 선택 기준.
    --        예: 'KO/EN', 'ZH/EN', 'JA/EN', 'FR/EN'
    language      VARCHAR(50)
);

-- [테이블 역할] 협력사 사업장(공장·광산·본사) 정보.
--               회사가 아닌 공장 단위로 원산지를 추적하는 이 시스템의 핵심 단위.
--               PostGIS POINT 좌표가 Geo Audit Agent(영수)의 검증 대상.
CREATE TABLE supplier_factories (
    factory_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id   UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,

    -- [역할] 공장 한글 명칭
    factory_name     VARCHAR(255),

    -- [역할] 공장 영문 명칭. DPP payload producedAtFactory 필드 및 영문 문서 출력용.
    factory_name_en  VARCHAR(255),

    address       TEXT,

    -- [역할] 공장 소재 국가 코드 (ISO 3166-1 alpha-2).
    --        Compliance Agent의 UFLPA(CN 신장 여부), IRA 적격국 판단 기준.
    country       VARCHAR(2),

    region        VARCHAR(100),

    -- [역할] 공장 좌표 (PostGIS POINT, WGS84).
    --        Geo Audit Agent가 ST_DWithin으로 신장·DRC 고위험 지역 근접 여부 판정.
    --        EUDR 산림훼손 분석의 기준점.
    location      GEOMETRY(POINT, 4326),

    -- [역할] 공장 역할 유형.
    --        허용값: headquarters / production / outsourcing / processing / mining
    factory_role  VARCHAR(30),

    is_active     BOOLEAN DEFAULT TRUE,

    -- [역할] 공장 가동 시작일.
    --        EUDR 기준일(2020-12-31) 이전 가동 여부 판단에 사용.
    operating_period_from DATE,

    -- [역할] 공장 가동 종료일. NULL이면 현재 가동 중.
    operating_period_to   DATE,

    -- [역할] 월간 생산 가능 용량. 공급 병목 파악 및 대체 공급망 추천 시 참조.
    --        단위 포함 자유 텍스트. 예: '2.4 GWh', '850 t', '1,250 t LiOH'
    monthly_capacity      VARCHAR(100),

    -- [역할] 이 공장 생산품의 최종 수출 목적지 시장.
    --        Compliance Agent가 applicable_regulations 결정 시 우선 참조.
    --        허용값: EU / US / KR / BOTH
    destination           VARCHAR(10),

    -- [역할] destination의 상세 설명. 공급망 맵 Drawer 표시용 자유 텍스트.
    --        예: '한양셀 → BMW 폴란드 (EU)'
    destination_detail    TEXT,

    -- [역할] 이 공장에 실제로 적용되는 규제 코드 배열(JSONB).
    --        같은 협력사도 공장마다 수출처가 달라 적용 규제가 다를 수 있음.
    --        예: 포항공장(EU용)=["EUDR","CSDDD","EU_BATTERY",...],
    --            광양공장(US용)=["UFLPA","IRA","CSDDD",...]
    --        NULL이면 batches.destination 기반 기본값 사용.
    applicable_regulations JSONB,

    -- [역할] 이 공장에 명시적으로 적용 제외되는 규제 코드 배열.
    --        applicable_regulations와 세트로 관리. 중복 검증 방지 및 판정 근거 기록용.
    hidden_regulations    JSONB,

    -- [역할] 동일 부품 전체 공급량 대비 이 공장의 납품 비율(%).
    --        한 부품을 여러 공장에서 분할 납품할 때 기여 비중.
    --        공급망 맵 "65% / 35% 분할 납품" 표시에 사용.
    supply_ratio_percent  NUMERIC(5,2),

    -- [역할] 월간 실제 납품량(단위 포함 자유 텍스트). supply_ratio_percent의 절대량 버전.
    --        공급망 병목 분석 및 대체 공급망 용량 비교용.
    --        예: '550 t/월', '720 MWh/월'
    supply_quantity       VARCHAR(100),

    created_at    TIMESTAMPTZ DEFAULT now()
);

-- supplier_contacts.factory_id FK — supplier_factories 생성 후 참조 가능
ALTER TABLE supplier_contacts
    ADD CONSTRAINT fk_contact_factory
    FOREIGN KEY (factory_id) REFERENCES supplier_factories(factory_id);

-- [테이블 역할] 협력사 온보딩 진행 상태 및 SLA 추적.
--               등록 즉시 row 생성, sla_due_date = 등록일 + 14일 자동 설정.
CREATE TABLE supplier_onboarding (
    onboarding_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id         UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,

    -- [역할] 개인정보 처리 동의 상태. pending → agreed 전이 시 포털 접근 허용.
    --        허용값: pending / agreed / rejected
    consent_status      VARCHAR(20) DEFAULT 'pending',

    consent_signed_at   TIMESTAMPTZ,
    agreement_status    VARCHAR(20) DEFAULT 'pending',
    agreement_signed_at TIMESTAMPTZ,
    last_invited_at     TIMESTAMPTZ,
    last_reminded_at    TIMESTAMPTZ,

    -- [역할] 데이터 제출 SLA 마감일. 등록일 + 14일 자동 설정.
    --        이 날짜 기준 14일 경과 → Reminder, 21일 경과 → Escalation 발송.
    sla_due_date        TIMESTAMPTZ,

    -- [역할] 리마인드 발송 횟수. escalation 기준 및 이력 추적용.
    reminder_count      INT DEFAULT 0
);

-- [테이블 역할] ISO·IRMA·IATF·Bettercoal 등 품질·환경·안전 인증서 관리.
--               원산지 증명서(origin_certificates)와 별도 — 이 테이블은 품질·환경 인증 전용.
CREATE TABLE supplier_certifications (
    cert_id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id        UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,

    -- [역할] 인증서 종류. 예: 'ISO 9001:2015', 'ISO 14001:2015', 'IRMA-75', 'RMI-CRT'
    certification_type VARCHAR(100),

    certification_no   VARCHAR(100),
    issued_at          DATE,

    -- [역할] 인증서 만료일. 만료 시 DPP Readiness의 certifications_valid 항목 미충족.
    expires_at         DATE,

    issuing_body       VARCHAR(255),
    document_url       VARCHAR(500)
);


-- ============================================================
-- 영역 3. Provider Type별 상세 (CTI 구조)
-- 담당: 팀원 B (Supplier Domain)
-- ============================================================

-- [테이블 역할] supplier_type='manufacturer' 협력사의 제조 상세 정보.
--               탄소 집약도(carbon_intensity)가 EU Battery Regulation Art.7 탄소발자국 신고 입력값.
CREATE TABLE supplier_manufacturer_details (
    detail_id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id           UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,

    -- [역할] 제조 공정 설명 (자유 텍스트)
    manufacturing_process TEXT,

    -- [역할] 주 에너지원. CBAM 탄소국경조정 계산 및 ESG 평가 기준.
    --        허용값: 재생 / 원자력 / 화석
    energy_source         VARCHAR(100),

    capacity              VARCHAR(100),

    -- [역할] 생산 단위당 탄소 배출 집약도 (kgCO2eq/kg).
    --        EU Battery Regulation Art.7 탄소발자국 신고 및 CBAM 세율 계산에 사용.
    --        Verification Domain carbon_rule이 허용 범위 이탈 여부 검증.
    carbon_intensity      NUMERIC(10,4)
);

-- [테이블 역할] supplier_type='recycler' 협력사의 재활용 공정 상세.
--               EU Battery Regulation 재활용 함량(Co/Ni/Li 비율) 검증에 사용.
CREATE TABLE supplier_recycler_details (
    detail_id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id             UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,

    -- [역할] 재활용 광물별 함량 비율(JSONB). 예: {"Co": 18, "Ni": 8, "Li": 7}
    --        EU Battery Regulation 2031년 이후 재활용 함량 의무 기준 검증에 사용.
    recycled_materials      JSONB,

    recycling_certification VARCHAR(255),

    -- [역할] 재활용 원료 출처.
    --        허용값: post-consumer / post-industrial
    input_source            VARCHAR(50),

    -- [역할] 전체 소재 대비 재활용 함량 비율(%). DPP payload의 recycledContent 필드 원본.
    recycled_content_ratio  NUMERIC(5,2)
);

-- [테이블 역할] supplier_type='trader' 협력사의 중개 상세 정보.
--               disclosure_completeness가 낮으면 상위 원산지 추적 불가 → DPP 발행 차단.
CREATE TABLE supplier_trader_details (
    detail_id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id             UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    trading_license         VARCHAR(100),
    broker_certification    VARCHAR(255),

    -- [역할] 상위 공급망 원산지 공개율(%). 75% 미만이면 DPP Readiness 미충족.
    --        trader_disclosure_obligation 테이블과 연동해 추적.
    disclosure_completeness NUMERIC(5,2) DEFAULT 0
);

-- [테이블 역할] supplier_type='miner' 협력사의 광산 상세 정보.
--               mine_coordinates가 Geo Audit Agent의 신장·DRC 고위험 판정 기준점.
CREATE TABLE supplier_miner_details (
    detail_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id        UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    mine_name          VARCHAR(255),

    -- [역할] 채굴 방식. 환경영향 평가 및 EUDR 산림훼손 리스크 판단에 사용.
    --        허용값: open-pit / underground
    mining_method      VARCHAR(50),

    extraction_volume  NUMERIC(15,2),

    -- [역할] 광산 좌표 (PostGIS POINT). 공장 좌표(supplier_factories.location)와 별도.
    --        Geo Audit Agent가 신장 위구르 자치구 경계 및 DRC 고위험 지역 판정에 사용.
    mine_coordinates   GEOMETRY(POINT, 4326),

    active_period_from DATE,
    active_period_to   DATE
);

-- [테이블 역할] 트레이더가 상위 공급망을 공개해야 하는 의무 관계 추적.
--               upstream_supplier_id별 disclosure_completeness 75% 미만이면 경고.
CREATE TABLE trader_disclosure_obligation (
    obligation_id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    trader_supplier_id      UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    upstream_supplier_id    UUID REFERENCES suppliers(supplier_id),

    -- [역할] 이 트레이더가 해당 상위 공급사에 대해 공개한 정보의 완성도(%).
    --        Verification Domain이 이 값을 기준으로 FEOC 검증 가능 여부를 판단.
    disclosure_completeness NUMERIC(5,2),

    last_audited_at         TIMESTAMPTZ
);


-- ============================================================
-- 영역 4. 협력사 리스크 프로필
-- 담당: 팀원 B (Supplier Domain)
-- ============================================================

-- [테이블 역할] 협력사별 종합 리스크 평가 결과 메인 프로필.
--               supplier당 1개 row(UNIQUE). AI 리스크 분석 + 사람 감사 결과를 통합.
--               Supplier Workspace > Risk 탭 및 Dashboard High Risk 탭의 데이터 소스.
CREATE TABLE supplier_risk_profiles (
    profile_id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id             UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,

    -- [역할] 0~100 점수. 높을수록 위험.
    --        violation=-30, warning=-10, geo_high_risk=-20 등 감점 방식으로 산출.
    --        이 점수를 구간 변환해 suppliers.risk_level 컬럼에 비정규화 저장.
    overall_risk_score      INT DEFAULT 0,

    -- [역할] overall_risk_score의 구간 요약값.
    --        0~29=critical / 30~49=high / 50~69=medium / 70~100=low
    risk_level              VARCHAR(20) DEFAULT 'low',

    -- [역할] FEOC 적격 여부 상세 판정 원본값. suppliers.feoc_status(캐시)의 원본.
    feoc_status             VARCHAR(20) DEFAULT 'unknown',

    -- [역할] IRA FEOC 기준 직접 지분율(%). 25% 이상이면 violation 즉시 확정.
    feoc_direct_ownership   NUMERIC(5,2),

    -- [역할] 국영기업 등을 통한 간접 지분율(%).
    --        직접 지분 미만이어도 합산 25% 이상이면 violation. 회색지대 → HITL 근거.
    feoc_indirect_ownership NUMERIC(5,2),

    -- [역할] FEOC 지분 평가 마지막 수행일. 주기 초과 시 재평가 알림 트리거 기준.
    feoc_last_assessed_at   TIMESTAMPTZ,

    -- [역할] FEOC 적격 인증서 만료일. 만료 30일 전 OriginCertExpiring 이벤트 발행.
    feoc_cert_expiry        DATE,

    -- [역할] 고위험 플래그. true이면 Dashboard High Risk 탭에 항상 노출.
    --        overall_risk_score < 50 또는 명시적 고위험 사유가 있으면 자동 설정.
    is_high_risk_flag       BOOLEAN DEFAULT FALSE,

    -- [역할] 고위험 판정 근거 목록(JSONB 배열).
    --        공급망 맵 Drawer 및 Supplier Workspace에서 "고위험 사유" 목록으로 표시.
    --        예: ["FEOC 직접 지분율 25% 초과(28.5%)", "ISO 14001 만료"]
    high_risk_reasons       JSONB,

    -- [역할] 리스크 프로필 마지막 검토일. 연 1회 의무 검토 SLA 초과 여부 판단 기준.
    last_risk_review_at     TIMESTAMPTZ,

    created_at              TIMESTAMPTZ DEFAULT now(),
    updated_at              TIMESTAMPTZ DEFAULT now(),
    UNIQUE(supplier_id)
);

-- [테이블 역할] 협력사 실사(Due Diligence) 감사 기록.
--               CSDDD·LKSG 공급망 실사 의무 이행 증빙 데이터.
--               Supplier Workspace > ESG 탭 감사 이력 및 DPP Readiness 체크 입력.
CREATE TABLE supplier_audit_records (
    audit_record_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id        UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,

    -- [역할] 감사 실시일. next_audit_due와 비교해 감사 주기 준수 여부 추적.
    audit_date         DATE NOT NULL,

    -- [역할] 감사 방식. CSDDD는 on_site 또는 third_party 권장.
    --        허용값: on_site / remote / document_review / third_party
    audit_type         VARCHAR(30),

    -- [역할] 감사 기관 또는 담당자명. 예: 'TÜV Rheinland Korea', 'Bureau Veritas'
    auditor            VARCHAR(255),

    audit_scope        TEXT,

    -- [역할] 감사 결과 판정.
    --        허용값: pass / conditional_pass / fail / pending
    result             VARCHAR(30),

    -- [역할] 감사에서 발견된 이슈 목록(JSONB 배열).
    --        예: ["공정도 4단계 문서 미비", "Scope 3 배출량 산정 방법론 개선 권고"]
    findings           JSONB,

    -- [역할] 시정 요구 사항 및 기한 목록(JSONB).
    --        Notification 시스템이 이 목록 기반으로 협력사에 이행 알림 발송.
    corrective_actions JSONB,

    -- [역할] 다음 감사 예정일. 초과 시 알림 자동 발송.
    next_audit_due     DATE,

    -- [역할] 감사 보고서 저장 URL. 규제 당국 제출 시 직접 링크 제공.
    report_url         VARCHAR(500),

    created_at         TIMESTAMPTZ DEFAULT now()
);

-- [테이블 역할] 협력사 사업장 인권 침해 이슈 기록.
--               CSDDD Art.7(인권 실사)·LKSG §3·CONFLICT_MINERALS 기준 추적.
--               open 상태 이슈 존재 시 DPP Readiness의 no_open_human_rights 항목 미충족.
CREATE TABLE supplier_human_rights_issues (
    issue_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,

    -- [역할] 이슈 발생 특정 공장 FK. NULL이면 협력사 전체 수준 이슈.
    factory_id  UUID REFERENCES supplier_factories(factory_id),

    -- [역할] 인권 이슈 유형. CSDDD Annex 분류 기준.
    --        허용값: forced_labor / child_labor / freedom_of_association /
    --                discrimination / harassment / wages / working_hours / other
    issue_type  VARCHAR(50),

    -- [역할] 이슈 심각도. critical이면 즉시 HITL 에스컬레이션 및 공급망 차단 검토.
    --        허용값: critical / major / minor
    severity    VARCHAR(20),

    description TEXT,
    detected_at TIMESTAMPTZ,

    -- [역할] 현재 이슈 처리 상태.
    --        open 상태 이슈가 존재하면 DPP 발행 차단 가능.
    --        허용값: open / in_remediation / resolved / monitoring
    status      VARCHAR(30),

    -- [역할] 이슈 발견 경로. 예: '현장 감사', 'NGO 제보 (Global Witness)', '내부 고충처리'
    source      VARCHAR(255),

    -- [역할] 이슈 해결 완료일. resolved 전이 시 자동 기록.
    resolved_at TIMESTAMPTZ,

    created_at  TIMESTAMPTZ DEFAULT now()
);

-- [테이블 역할] 협력사 사업장 산업재해 기록.
--               CSDDD·LKSG 안전 의무 이행 증빙.
--               fatality 발생 시 RiskEscalated 이벤트 즉시 발행.
--               investigating 상태 재해 존재 시 DPP Readiness의 no_open_accidents 항목 미충족.
CREATE TABLE supplier_industrial_accidents (
    accident_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id       UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    factory_id        UUID REFERENCES supplier_factories(factory_id),

    accident_date     DATE NOT NULL,

    -- [역할] 재해 유형. fatality 발생 시 즉시 HITL 트리거.
    --        허용값: fatality / serious_injury / minor_injury / near_miss / environmental
    accident_type     VARCHAR(30),

    description       TEXT,

    -- [역할] 재해 관련 인원 수(사망+부상 합산). 심각도 가중치 계산에 사용.
    casualties        INT DEFAULT 0,

    -- [역할] Lost Time Injury Frequency Rate (연간 100만 근무시간당 휴업재해 건수).
    --        국제 기준(ILO 권고 ltifr < 1.0) 비교 및 위험 등급 자동 판정.
    ltifr             NUMERIC(6,2),

    -- [역할] 재해 처리 현황. investigating 상태이면 DPP Readiness 체크 차단.
    --        허용값: reported / investigating / closed
    status            VARCHAR(20),

    corrective_action TEXT,

    created_at        TIMESTAMPTZ DEFAULT now()
);


-- ============================================================
-- 영역 5. 원산지 증명서
-- 담당: 팀원 B (Supplier Domain — Origin 탭)
-- ============================================================

-- [테이블 역할] 규제 대응 전용 원산지 증명서 관리.
--               supplier_certifications(품질·환경 인증)과 완전히 별도.
--               FTA 원산지 증명·UFLPA 반증 서류·IRA 원산지 적격 증명 등을 관리.
--               Supplier Workspace > Origin 탭의 데이터 소스.
--               만료 30일 전 스케줄러가 OriginCertExpiring 이벤트를 자동 발행.
CREATE TABLE origin_certificates (
    cert_id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id       UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,

    -- [역할] 증명서가 적용되는 특정 공장 FK. NULL이면 협력사 전체 적용.
    factory_id        UUID REFERENCES supplier_factories(factory_id),

    -- [역할] 원산지 증명서 유형.
    --        FTA: 한-EU·한-미 FTA 원산지 증명서 (관세 혜택용)
    --        GSP: 일반특혜관세 원산지 증명서
    --        UFLPA_REBUTTAL: 미국 CBP 제출용 신장 관련 반증 서류
    --        IRA_ORIGIN: 북미 생산 세액공제용 FEOC 비해당 적격 증명
    --        CONFLICT_FREE: RMI RMAP 분쟁광물 무분쟁 선언
    --        GENERAL: 기타 일반 원산지 증명
    cert_type         VARCHAR(30) NOT NULL,

    cert_number       VARCHAR(100),

    -- [역할] 발급 기관. 예: '산업통상자원부', 'CBP (US Customs)', 'RMI RMAP'
    issuing_authority VARCHAR(255),

    issued_at         DATE,

    -- [역할] 만료일. NOT NULL — 만료일 없는 증명서 불허.
    --        스케줄러가 매일 스캔하여 만료 30일 이내 건을 expiring_soon으로 갱신.
    --        FTA 원산지(포괄)확인서 기준 12개월 유효.
    expires_at        DATE NOT NULL,

    -- [역할] 원산지 국가 코드 (ISO 3166-1 alpha-2).
    --        UFLPA 대상(CN) 및 IRA FTA 적격국 확인에 사용.
    origin_country    VARCHAR(2),

    -- [역할] 증명서가 커버하는 광물 목록(JSONB 배열).
    --        예: ["코발트","니켈","리튬"]
    --        Compliance Agent가 광물별 증명서 보유 여부 확인에 사용.
    covered_minerals  JSONB,

    -- [역할] 증명서 유효 상태. 스케줄러 자동 갱신.
    --        expired 상태이면 DPP Readiness의 origin_certs_valid 항목 미충족.
    --        허용값: valid / expiring_soon / expired / under_review
    status            VARCHAR(20) DEFAULT 'valid',

    -- [역할] 증명서 원본 파일 URL. OCR 검증 및 규제 당국 제출용 직접 링크.
    document_url      VARCHAR(500),

    created_at        TIMESTAMPTZ DEFAULT now(),
    updated_at        TIMESTAMPTZ DEFAULT now()
);


-- ============================================================
-- 영역 6. 교육 관리
-- 담당: 팀원 B (Supplier Domain — Training 탭)
-- ============================================================

-- [테이블 역할] 규제별 의무 교육 자료 마스터 카탈로그.
--               어떤 규제에 어떤 교육이 필요한지 정의. 협력사별 이수 현황은 training_records.
CREATE TABLE training_materials (
    material_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- [역할] 교육 자료 한글 제목
    title            VARCHAR(255) NOT NULL,

    -- [역할] 교육 자료 영문 제목. 외국 협력사 발송용.
    title_en         VARCHAR(255),

    -- [역할] 교육 카테고리. 자료 분류 및 Dashboard 필터 기준.
    --        허용값: human_rights / safety / environmental / anti_corruption /
    --                conflict_minerals / data_protection / esg_general
    category         VARCHAR(50),

    description      TEXT,

    -- [역할] 교육 형식. 협력사 이수 방식 결정에 영향.
    --        허용값: pdf / video / online / onsite
    format           VARCHAR(20),

    -- [역할] 예상 이수 소요 시간(분). 협력사 일정 계획 수립 참조.
    duration_minutes INT,

    -- [역할] 이 교육이 의무인 규제 코드 배열(JSONB).
    --        예: ["CSDDD","LKSG"] → 해당 규제 적용 협력사는 필수 이수.
    --        Compliance Agent가 규제 위반 시 연관 교육 미이수 여부 함께 점검.
    required_for     JSONB,

    -- [역할] 자료 버전. 규제 개정 시 버전 업 → 기존 이수자도 재이수 트리거.
    version          VARCHAR(20),

    updated_at       TIMESTAMPTZ DEFAULT now(),

    -- [역할] 자료 접근 URL. 협력사 Portal에서 직접 열람 링크 제공.
    url              VARCHAR(500)
);

-- [테이블 역할] 협력사별·공장별 교육 이수 현황 기록.
--               due_date 초과 미이수 건 → TrainingOverdue 이벤트 발행 →
--               DPP Readiness의 training_completed 항목 미충족.
CREATE TABLE training_records (
    record_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id     UUID REFERENCES suppliers(supplier_id) ON DELETE CASCADE,

    -- [역할] 이수 대상 특정 공장 FK. 공장별 이수 현황이 다를 수 있음.
    factory_id      UUID REFERENCES supplier_factories(factory_id),

    material_id     UUID REFERENCES training_materials(material_id),

    -- [역할] 실제 이수 완료 인원 수
    trainee_count   INT DEFAULT 0,

    -- [역할] 이수 대상 총 인원. trainee_count / total_eligible = completion_rate.
    total_eligible  INT DEFAULT 0,

    -- [역할] 이수율(%). 100% 미만이면 DPP Readiness 경고.
    completion_rate NUMERIC(5,2) DEFAULT 0,

    -- [역할] 교육 완료 일자. completed 전이 시 기록.
    completed_at    TIMESTAMPTZ,

    -- [역할] 이수 기한. 초과 시 status가 overdue로 자동 전이.
    --        TrainingOverdue 이벤트 발행 트리거 기준.
    due_date        DATE NOT NULL,

    -- [역할] 현재 이수 진행 상태.
    --        overdue 상태이면 Notification Queue에 reminder 적재.
    --        허용값: completed / in_progress / overdue / not_started
    status          VARCHAR(20) DEFAULT 'not_started',

    -- [역할] 교육 강사 또는 담당자. 현장 교육(onsite) 시 필수 기록.
    instructor      VARCHAR(255),

    -- [역할] 특이사항 메모. 예: "반복 요청에도 이수 미진행"
    notes           TEXT,

    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);


-- ============================================================
-- 영역 7. 제품 / BOM / 부품
-- 담당: 팀원 C (Product Domain)
-- ============================================================

-- [테이블 역할] 배터리 제품 마스터. 모든 공급망·DPP는 이 product를 기준으로 연결됨.
CREATE TABLE products (
    product_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- [역할] 제품 코드. UNIQUE — 중복 등록 방지. 예: 'BAT-NCM811-100Ah'
    product_code    VARCHAR(50) UNIQUE NOT NULL,

    product_name    VARCHAR(255),
    manufacturer_id UUID REFERENCES suppliers(supplier_id),

    -- [역할] 배터리 형태. 예: 각형 / 파우치형 / 원통형
    type            VARCHAR(50),

    -- [역할] 제품 규격(JSONB). 예: {"무게": "650kg", "용량": "100Ah", "전압": "3.7V"}
    specs           JSONB,

    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

-- [테이블 역할] 제품 BOM의 버전 관리. 같은 제품도 시점별로 다른 BOM을 가질 수 있음.
--               status 전이는 반드시 domains/product/state_machine.py 를 통해서만.
CREATE TABLE bom_versions (
    bom_version_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    product_id     UUID REFERENCES products(product_id) ON DELETE CASCADE,
    version_number VARCHAR(20) NOT NULL,
    effective_from DATE,
    effective_to   DATE,

    -- [역할] BOM 버전 상태. 한 product에 active 버전은 1개만 존재해야 함.
    --        허용값: draft / active / deprecated
    status         VARCHAR(20) DEFAULT 'draft',

    approved_by    UUID REFERENCES users(user_id),
    approved_at    TIMESTAMPTZ,
    created_at     TIMESTAMPTZ DEFAULT now()
);

-- [테이블 역할] 부품 마스터. Pack→Module→Cell→전구체→광물 5계층 자기참조 트리.
--               hs_code 6자리 이상 필수 — FTA 세번변경기준(CTC) 판정의 전제 조건.
CREATE TABLE parts (
    part_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- [역할] 원청 기준 부품 코드. UNIQUE. 예: 'PACK-NCM811-100Ah'
    part_code        VARCHAR(50) UNIQUE NOT NULL,

    part_name        VARCHAR(255),

    -- [역할] 부품 계층 레벨. 1=Pack / 2=Module / 3=Cell / 4=전구체 / 5=광물
    tier_level       INT,

    -- [역할] 상위 부품 자기참조 FK. Pack이 루트(NULL), 광물이 말단.
    --        parts 테이블 재귀 CTE 쿼리의 핵심 컬럼.
    parent_part_id   UUID REFERENCES parts(part_id),

    -- [역할] HS Code (6자리 이상 필수). FTA 세번변경기준(CTC2/CTC4/CTC6) 판정 키.
    --        6자리 미만 입력 시 API 422 반환.
    hs_code          VARCHAR(15),

    material_type    VARCHAR(100),
    function_purpose TEXT,

    -- [역할] 단가. RVC(Regional Value Content) 부가가치기준 FTA 판정 계산에 사용.
    unit_price       NUMERIC(15,4),

    purchase_unit    VARCHAR(20),
    specs            JSONB,
    created_at       TIMESTAMPTZ DEFAULT now()
);

-- [테이블 역할] BOM 버전 내 부품 구성 항목. 부품별 소요량·원산지·재료비 기록.
CREATE TABLE bom_items (
    bom_item_id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    bom_version_id         UUID REFERENCES bom_versions(bom_version_id) ON DELETE CASCADE,
    part_id                UUID REFERENCES parts(part_id),
    required_quantity      NUMERIC(15,4),
    required_quantity_unit VARCHAR(20),
    percentage             NUMERIC(5,2),

    -- [역할] 직접재료비. RVC 계산 시 역내 부가가치 산정 기준.
    direct_material_cost   NUMERIC(15,4),

    -- [역할] 원산지 국가 코드 (ISO 3166-1 alpha-2). FTA 원산지 기준 판정 입력값.
    origin_country         VARCHAR(2)
);

-- [테이블 역할] 같은 부품의 원청 코드와 협력사 코드를 매핑.
--               협력사가 다른 코드를 사용해도 동일 부품으로 추적 가능.
CREATE TABLE part_code_mapping (
    mapping_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    part_id             UUID REFERENCES parts(part_id) ON DELETE CASCADE,
    supplier_id         UUID REFERENCES suppliers(supplier_id),

    -- [역할] 협력사 내부 사용 부품 코드. 예: 'POS-CAM-NCM-811-A'
    supplier_part_code  VARCHAR(50),

    -- [역할] 원청 기준 부품 코드. 예: 'CAM-NCM811'
    original_part_code  VARCHAR(50)
);

-- [테이블 역할] 부품별 제조 공정도. 아웃소싱 공정은 협력사 FK로 연결.
--               CSDDD·LKSG 실사 시 제조 공정 투명성 증빙에 사용.
CREATE TABLE manufacturing_process (
    process_id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    part_id                   UUID REFERENCES parts(part_id) ON DELETE CASCADE,
    sequence_no               INT,
    process_name              VARCHAR(255),
    process_description       TEXT,
    is_outsourced             BOOLEAN DEFAULT FALSE,

    -- [역할] 아웃소싱 대상 협력사 FK. is_outsourced=TRUE일 때 필수.
    outsourced_to_supplier_id UUID REFERENCES suppliers(supplier_id),

    -- [역할] 제조 공정도 이미지 URL. DPP 발행 시 첨부 및 규제 당국 제출용.
    process_image_url         VARCHAR(500)
);


-- ============================================================
-- 영역 8. 공급망 맵
-- 담당: 팀원 D (SupplyChain Domain)
-- ============================================================

-- [테이블 역할] 협력사 간 공급 관계 그래프. parent→child 방향으로 공급망 트리 구성.
--               재귀 CTE로 N차 공급망 전체를 탐색하는 핵심 테이블.
CREATE TABLE supply_chain_map (
    map_id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    bom_version_id     UUID REFERENCES bom_versions(bom_version_id),

    -- [역할] 구매하는 쪽(상위) 협력사. NULL이면 원청사가 직접 구매.
    parent_supplier_id UUID REFERENCES suppliers(supplier_id),

    -- [역할] 납품하는 쪽(하위) 협력사. 공급망 그래프의 자식 노드.
    child_supplier_id  UUID REFERENCES suppliers(supplier_id),

    part_id            UUID REFERENCES parts(part_id),

    -- [역할] 구매 발주 번호. part_code_mapping과 연계해 실물 거래 추적.
    po_number          VARCHAR(50),

    invoice_number     VARCHAR(50),
    supply_period_from DATE,
    supply_period_to   DATE,
    created_at         TIMESTAMPTZ DEFAULT now()
);

-- [테이블 역할] 한 부품을 여러 공장에서 분할 납품할 때 공장별 비율과 물량을 기록.
--               supplier_factories.supply_ratio_percent와 연동.
CREATE TABLE supply_ratio (
    ratio_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    map_id           UUID REFERENCES supply_chain_map(map_id) ON DELETE CASCADE,
    factory_id       UUID REFERENCES supplier_factories(factory_id),

    -- [역할] 이 공장의 해당 부품 납품 비율(%). 모든 공장의 합산이 100이어야 함.
    ratio_percentage NUMERIC(5,2),

    volume           NUMERIC(15,4),
    unit             VARCHAR(20)
);


-- ============================================================
-- 영역 9. 운영 / 배치 / DPP
-- 담당: 팀원 A (배치 처리) / 팀원 E (DPP Domain)
-- ============================================================

-- [테이블 역할] AI 파이프라인 처리 단위. LangGraph StateGraph의 상태 원본.
--               하나의 배치는 특정 제품의 특정 BOM 버전을 기준으로 생성.
CREATE TABLE batches (
    batch_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    product_id       UUID REFERENCES products(product_id),
    bom_version_id   UUID REFERENCES bom_versions(bom_version_id),

    -- [역할] 소속 테넌트 FK. 멀티테넌트 데이터 격리 필수 컬럼.
    --        모든 배치 조회 쿼리에 WHERE tenant_id = :current_tenant 조건 필수.
    tenant_id        UUID REFERENCES tenants(tenant_id),

    received_at      TIMESTAMPTZ DEFAULT now(),

    -- [역할] 최종 수출 목적지. Compliance Agent 규제 분기 기준.
    --        허용값: US / EU / KR
    destination      VARCHAR(2),

    -- [역할] LangGraph 현재 처리 단계. BatchState.current_stage와 동기화.
    current_stage    VARCHAR(50),

    -- [역할] 배치 처리 상태. LangGraph Supervisor가 단계 전이 시 갱신.
    --        허용값: processing / hitl_wait / completed / rejected
    status           VARCHAR(20),

    -- [역할] 현재 단계의 AI 판정 신뢰도 점수 (0.0~1.0).
    --        0.85 미만이면 Supervisor가 hitl_interrupt 노드로 라우팅.
    confidence_score NUMERIC(5,4)
);

-- [테이블 역할] 최종 발행된 DPP 기록. issued 이후 immutable — 수정 불가.
--               DB 레벨 트리거(trg_dpp_immutable)와 앱 레벨 가드(immutable_guard.py) 이중 차단.
CREATE TABLE dpp_records (
    dpp_id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    batch_id         UUID REFERENCES batches(batch_id),
    product_id       UUID REFERENCES products(product_id),
    issued_at        TIMESTAMPTZ,

    -- [역할] DPP 상태. issued 이후 이 컬럼 포함 모든 컬럼 수정 불가.
    --        수정 필요 시 반드시 새 dpp_id로 신규 발행.
    --        허용값: issued / revoked
    status           VARCHAR(20),

    -- [역할] 제품 탄소발자국 (kgCO2eq). EU Battery Regulation Art.7 신고 필드.
    carbon_footprint NUMERIC(10,4),

    -- [역할] 재활용 함량(JSONB). 예: {"Co": 18, "Ni": 8, "Li": 7}
    --        EU Battery Regulation 재활용 함량 의무 기준 충족 증빙.
    recycled_content JSONB,

    qr_code_url      VARCHAR(500),

    -- [역할] Annex XIII 80개 필드를 담은 DPP 전체 payload(JSONB).
    --        외부 EU Battery Passport API 전송용.
    payload          JSONB,

    approved_by      UUID REFERENCES users(user_id)
);


-- ============================================================
-- 영역 10. 규제 / 컴플라이언스
-- 담당: 팀원 C (Compliance Agent — 시드 데이터 적재 포함)
-- ============================================================

-- [테이블 역할] 적용 규제 마스터. 총 12개 규제 코드 시드 데이터 필요.
--               document_s3_url에서 텍스트를 추출해 embedding 컬럼에 저장 후
--               Compliance Agent(은지)의 pgvector RAG 검색에 사용.
CREATE TABLE regulations (
    regulation_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- [역할] 규제 전체 명칭 (사람이 읽는 용도).
    --        예: 'EU Battery Regulation', 'Uyghur Forced Labor Prevention Act'
    name             VARCHAR(100),

    -- [역할] 규제 단축 코드. Compliance Agent REGULATION_JUDGES 딕셔너리 키와 1:1 대응.
    --        프론트엔드 Regulation 타입과 동일한 값 사용. UNIQUE.
    --        예: 'EUDR', 'UFLPA', 'IRA', 'EU_BATTERY_ART47', 'LKSG'
    regulation_code  VARCHAR(50) UNIQUE,

    -- [역할] 규제 적용 지역. Compliance Agent REGULATION_BY_DESTINATION 분기 기준.
    --        허용값: EU / US / DE / BOTH
    region           VARCHAR(10),

    -- [역할] 규제 설명 (한글). 프론트엔드 regulationMeta.description과 동기화.
    --        공급망 허브·Compliance 결과 화면 툴팁으로 표시.
    description      TEXT,

    version          VARCHAR(20),
    effective_from   DATE,
    document_s3_url  VARCHAR(500),

    -- [역할] 규제 문서 임베딩 처리 상태.
    --        indexed 상태인 규제만 RAG 검색 대상.
    --        허용값: pending / indexed
    embedding_status VARCHAR(20) DEFAULT 'pending',

    -- [역할] 규제 문서 전체 텍스트의 벡터 임베딩 (1536차원).
    --        text-embedding-3-small 모델 기준.
    --        document_s3_url에서 텍스트 추출 → 청크 분할 → 저장.
    embedding        vector(1536)
);

-- [테이블 역할] 배치별 규제 준수 판정 결과. Compliance Agent(은지)가 규제별로 INSERT.
--               cited_clauses에 법조항 인용 필수 — 없으면 gray_zone으로 처리.
CREATE TABLE compliance_results (
    result_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    batch_id         UUID REFERENCES batches(batch_id) ON DELETE CASCADE,
    regulation_id    UUID REFERENCES regulations(regulation_id),

    -- [역할] 협력사 기준 직접 조회용 FK.
    --        Supplier Workspace > AI Verification 탭에서 협력사별 최근 판정 결과 표시.
    --        기존 batch_id만으로는 batches→products→supply_chain_map→suppliers 경로 JOIN 필요.
    supplier_id      UUID REFERENCES suppliers(supplier_id),

    -- [역할] 규제 준수 판정 결과.
    --        gray_zone이면 confidence_score를 0.85 미만으로 설정 → Supervisor가 HITL 라우팅.
    --        허용값: passed / violation / gray_zone
    verdict          VARCHAR(20),

    -- [역할] 판정 근거 법조항 인용 목록(JSONB). 예: ["§3(a)(1)", "§7(b)"]
    --        은지 에이전트 시스템 프롬프트에 인용 필수 명시.
    cited_clauses    JSONB,

    confidence_score NUMERIC(5,4),
    reasoning_text   TEXT,
    created_at       TIMESTAMPTZ DEFAULT now()
);

-- [테이블 역할] 규제별 적용 대상 Provider Type 및 Tier 정의.
--               어떤 규제가 어떤 협력사 유형·차수에 의무인지 관리.
CREATE TABLE regulation_applicability (
    applicability_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    regulation_id            UUID REFERENCES regulations(regulation_id),
    applicable_provider_type VARCHAR(30),
    applicable_tier          INT,

    -- [역할] 적용 강도. mandatory=위반 시 DPP 발행 차단, recommended=경고만.
    severity                 VARCHAR(20)
);

-- [테이블 역할] Provider Type별 온보딩 시 필수 입력 필드·문서 정의.
--               completeness_score 계산 기준으로 사용.
CREATE TABLE onboarding_data_requirements (
    requirement_id   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    provider_type    VARCHAR(30),

    -- [역할] 필수 입력 필드명 배열(JSONB). completeness_score 분모 기준.
    required_fields  JSONB,

    -- [역할] 필수 제출 문서 종류 배열(JSONB). 예: ["ISO 14001", "제조공정도", "원산지확인서"]
    required_documents JSONB
);


-- ============================================================
-- 영역 11. 데이터 흐름 추적 / Submission 상태머신
-- 담당: 팀원 E (Submission Domain)
-- ============================================================

-- [테이블 역할] 원청사가 협력사에 데이터 제출을 요청하는 기록.
--               response_status: SLA 기반 이메일 추적용.
--               submission_status: 비즈니스 상태머신 전이 추적용 — 두 컬럼의 목적이 다름.
CREATE TABLE data_request_log (
    request_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    requester_user_id   UUID REFERENCES users(user_id),
    target_supplier_id  UUID REFERENCES suppliers(supplier_id),

    -- [역할] 요청한 데이터 종류. 예: 'BOM', '원산지확인서', 'ISO 14001 인증서'
    requested_data_type VARCHAR(100),

    requested_at        TIMESTAMPTZ DEFAULT now(),

    -- [역할] SLA 마감일. 요청일 + 14일 자동 설정.
    due_date            TIMESTAMPTZ,

    -- [역할] SLA 기반 이메일 응답 추적 상태.
    --        허용값: pending / responded / overdue / escalated
    response_status     VARCHAR(20) DEFAULT 'pending',

    reminder_count      INT DEFAULT 0,
    last_reminder_at    TIMESTAMPTZ,
    responded_at        TIMESTAMPTZ,

    -- [역할] 비즈니스 워크플로우 상태머신 전이 추적 컬럼.
    --        직접 UPDATE 금지 — 반드시 transition_submission() 함수를 통해서만 변경.
    --        공급망 허브 노드 컬러 계산(v_supply_chain_node_status 뷰)의 핵심 입력.
    --        허용값: pending / requested / in_progress / submitted / review /
    --                approved / archived / rejected / violation
    submission_status   VARCHAR(20) DEFAULT 'pending'
);

-- [테이블 역할] submission_status 상태 전이 이력 완전 기록.
--               모든 전이는 이 테이블에 자동 INSERT — transition_submission() 함수가 처리.
--               Supplier Workspace > Submission Timeline 탭의 데이터 소스.
--               누가, 언제, 왜 상태를 바꿨는지 완전 감사 추적 가능.
CREATE TABLE submission_status_history (
    history_id  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    request_id  UUID REFERENCES data_request_log(request_id) ON DELETE CASCADE,

    -- [역할] 전이 이전 상태. 최초 생성 시 NULL.
    from_status VARCHAR(20),

    -- [역할] 전이 이후 상태.
    to_status   VARCHAR(20) NOT NULL,

    -- [역할] 상태를 변경한 사용자 FK.
    --        시스템 자동 전이는 NULL, 사람 액션은 user_id 기록.
    actor_id    UUID REFERENCES users(user_id),

    -- [역할] 전이 사유. rejected 전이 시 협력사 통보 메시지의 원본.
    --        SubmissionRejected 이벤트 payload에 포함됨.
    reason      TEXT,

    -- [역할] 전이 발생 시각. Submission Timeline 정렬 기준.
    changed_at  TIMESTAMPTZ DEFAULT now()
);

-- [테이블 역할] 엔티티별 데이터 완성도 현황. completeness_score 계산 원본.
CREATE TABLE data_completeness_status (
    status_id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- [역할] 완성도를 추적하는 엔티티 종류.
    --        허용값: supplier / part / bom / factory
    entity_type          VARCHAR(30),

    entity_id            UUID,
    required_field_count INT,
    filled_field_count   INT,

    -- [역할] 완성도 비율(%). 80% 미만이면 DPP Readiness all_tiers_completeness 항목 미충족.
    completion_rate      NUMERIC(5,2),

    -- [역할] 미입력 필드명 목록(JSONB). Supplier Workspace에서 "누락 데이터" 목록으로 표시.
    missing_fields       JSONB,

    last_updated_by      UUID REFERENCES users(user_id),
    last_updated_at      TIMESTAMPTZ DEFAULT now()
);

-- [테이블 역할] 이메일·Slack·in-app 알림 발송 기록.
CREATE TABLE notifications (
    notification_id   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id           UUID REFERENCES users(user_id),

    -- [역할] 발송 채널.
    --        허용값: email / slack / in-app
    channel           VARCHAR(20),

    -- [역할] 알림 유형. 수신자 필터링 및 템플릿 선택 기준.
    --        허용값: reminder / violation / approval_needed / sla_warning / training_overdue
    notification_type VARCHAR(50),

    subject           VARCHAR(255),
    body              TEXT,
    sent_at           TIMESTAMPTZ,
    read_at           TIMESTAMPTZ,

    -- [역할] 발송 상태.
    --        허용값: pending / sent / failed / read
    status            VARCHAR(20)
);


-- ============================================================
-- 영역 12. 감사 추적 (Provenance)
-- 담당: 팀원 A (Audit Domain)
-- ============================================================

-- [테이블 역할] 모든 AI 노드·툴·사람 결정의 해시 체인 감사 로그.
--               @trace_node / @trace_tool 데코레이터가 자동 INSERT.
--               prev_hash → output_hash 연결로 위변조 감지 가능.
--               규제 당국 5년 감사 대응의 핵심 증빙.
CREATE TABLE audit_trail (
    audit_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    batch_id       UUID REFERENCES batches(batch_id),
    step_number    INT,
    timestamp      TIMESTAMPTZ DEFAULT now(),

    -- [역할] 기록 주체 유형.
    --        허용값: agent / tool / human
    node_type      VARCHAR(20),

    -- [역할] 노드 또는 툴 이름. 예: 'data_gateway', 'compliance', 'parse_pdf'
    node_name      VARCHAR(100),

    model_version  VARCHAR(50),
    prompt_version VARCHAR(20),
    duration_ms    INT,

    -- [역할] 함수 입력값의 SHA-256 해시. 입력 무결성 검증용.
    input_hash     VARCHAR(64),

    -- [역할] 함수 출력값의 SHA-256 해시. 다음 row의 prev_hash로 연결.
    output_hash    VARCHAR(64),

    -- [역할] 직전 step의 output_hash. 해시 체인 연결 — NULL이면 첫 번째 step.
    --        GET /audit/trail/{batch_id} 응답에서 chain_valid 검증에 사용.
    prev_hash      VARCHAR(64),

    decision_text  TEXT,

    -- [역할] Compliance Agent가 인용한 법조항 목록(JSONB). 은지 판정 근거 추적용.
    citations      JSONB
);

-- [테이블 역할] 규제 개정 시 기존 공급망에 미치는 영향 분석 결과.
--               newly_required_fields: 신규 필수 필드 목록.
--               gray_zone_items: HITL 검토가 필요한 모호 항목.
CREATE TABLE gap_analysis_results (
    analysis_id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    regulation_id         UUID REFERENCES regulations(regulation_id),
    previous_version_id   UUID REFERENCES regulations(regulation_id),

    -- [역할] 이번 규제 개정으로 영향받는 협력사 ID 배열(JSONB).
    affected_supplier_ids JSONB,

    -- [역할] 신규 필수 항목 목록(JSONB). 영향받는 협력사의 completeness_score 재계산 트리거.
    newly_required_fields JSONB,

    -- [역할] HITL 검토가 필요한 회색지대 항목(JSONB). gap_analysis 완료 시 HITL Queue 적재.
    gray_zone_items       JSONB,

    analyzed_at           TIMESTAMPTZ DEFAULT now(),
    reviewed_by           UUID REFERENCES users(user_id),
    reviewed_at           TIMESTAMPTZ
);


-- ============================================================
-- 트리거 함수 및 트리거
-- 담당: 팀원 E (DPP Domain)
-- ============================================================

-- [함수 역할] dpp_records가 UPDATE될 때 기존 status가 'issued'이면 예외 발생.
--             애플리케이션 레벨 immutable_guard.py와 이중 가드.
--             DPP 발행 후 데이터 무결성 보장 — 수정 필요 시 반드시 새 버전 발행.
CREATE OR REPLACE FUNCTION prevent_issued_dpp_update()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.status = 'issued' THEN
        RAISE EXCEPTION
          'DPP record % is already issued and cannot be modified. Create a new version instead.',
          OLD.dpp_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- [트리거 역할] dpp_records의 모든 UPDATE 시도 전에 위 함수 실행.
--              BEFORE UPDATE — 실제 변경이 DB에 반영되기 전에 차단.
CREATE TRIGGER trg_dpp_immutable
    BEFORE UPDATE ON dpp_records
    FOR EACH ROW EXECUTE FUNCTION prevent_issued_dpp_update();


-- ============================================================
-- 뷰
-- 담당: 팀원 D (SupplyChain Domain — 조회) / 팀원 B (값 유지)
-- ============================================================

-- [뷰 역할] 공급망 허브 중앙 맵의 노드 컬러링용 사전 JOIN 뷰.
--           프론트엔드가 매 렌더링마다 4개 테이블 직접 JOIN 대신 이 뷰를 단일 조회.
--           node_color 컬럼이 UI 노드 색상 결정:
--             red    = 규제 위반 또는 high/critical 리스크
--             green  = 검증 완료 (approved)
--             yellow = 제출 완료 또는 검토 중
--             blue   = 요청 발송 완료 (협력사 입력 대기)
--             gray   = 아직 요청 전
CREATE VIEW v_supply_chain_node_status AS
SELECT
    -- [역할] 공급망 관계 고유 식별자
    scm.map_id,

    -- [역할] 상위(구매) 협력사 ID. NULL이면 원청사 직접 구매.
    scm.parent_supplier_id,

    -- [역할] 하위(납품) 협력사 ID. 맵 노드의 실체.
    scm.child_supplier_id,

    -- [역할] 이 공급 관계에서 거래되는 부품 ID
    scm.part_id,

    -- [역할] 협력사 한국어 기본 표시명
    s.company_name,

    -- [역할] 협력사 영문 표시명. 공급망 맵 노드 라벨.
    s.company_name_en,

    -- [역할] 협력사 유형. 노드 아이콘 결정 기준.
    s.supplier_type,

    -- [역할] 공급망 차수. 노드 레이어 위치 결정.
    s.tier,

    -- [역할] 협력사 워크플로우 상태. 노드 컬러 계산 입력.
    s.status            AS supplier_status,

    -- [역할] 리스크 레벨. red 노드 판정 기준 중 하나.
    s.risk_level,

    -- [역할] FEOC 적격 여부. 노드 Drawer FEOC 뱃지 표시.
    s.feoc_status,

    -- [역할] 데이터 완성도 점수 0~100. 노드 Drawer 진행률 표시.
    s.completeness_score,

    -- [역할] 공장 소재 국가 코드. Geo 정보 표시.
    sf.country,

    -- [역할] 공장 좌표 PostGIS POINT. 지도 위 공장 위치 마커.
    sf.location,

    -- [역할] 공장 적용 규제 코드 배열. 노드 Drawer 규제 뱃지 표시.
    sf.applicable_regulations,

    -- [역할] 이 협력사의 가장 최근 데이터 요청 제출 상태. 노드 컬러 계산 입력.
    drl.submission_status,

    -- [역할] 데이터 요청 마감일. SLA 초과 여부 뱃지 표시.
    drl.due_date,

    -- [역할] SLA 추적 상태. overdue 여부 뱃지 표시.
    drl.response_status,

    -- [역할] 프론트엔드 공급망 허브 노드 색상.
    --        우선순위: violation/high_risk(red) > approved(green) >
    --                  submitted/review(yellow) > requested/in_progress(blue) > 기타(gray)
    CASE
        WHEN s.status = 'violation'                                THEN 'red'
        WHEN s.risk_level IN ('high', 'critical')                  THEN 'red'
        WHEN drl.submission_status = 'approved'                    THEN 'green'
        WHEN drl.submission_status IN ('submitted', 'review')      THEN 'yellow'
        WHEN drl.submission_status IN ('requested', 'in_progress') THEN 'blue'
        ELSE 'gray'
    END AS node_color

FROM supply_chain_map scm
JOIN suppliers s
    ON s.supplier_id = scm.child_supplier_id
LEFT JOIN supplier_factories sf
    ON sf.supplier_id = s.supplier_id AND sf.is_active = TRUE
LEFT JOIN data_request_log drl
    ON drl.target_supplier_id = s.supplier_id
   AND drl.response_status != 'responded'
   AND drl.requested_at = (
         SELECT MAX(d2.requested_at)
         FROM data_request_log d2
         WHERE d2.target_supplier_id = s.supplier_id
       );


-- ============================================================
-- 인덱스
-- ============================================================

-- 영역 1~2 — 협력사 기본 조회
CREATE INDEX idx_suppliers_type          ON suppliers(supplier_type);
-- [역할] 공급망 맵 Tier 필터 및 재귀 CTE 쿼리 성능
CREATE INDEX idx_suppliers_tier          ON suppliers(tier);
-- [역할] 공급망 트리 구성 시 parent 기준 자식 노드 빠른 조회
CREATE INDEX idx_suppliers_parent        ON suppliers(parent_supplier_id);
-- [역할] 협력사 리스트 status 필터 (공급망 허브 노드 컬러)
CREATE INDEX idx_suppliers_status        ON suppliers(status);
-- [역할] Dashboard High Risk 탭 및 공급망 맵 red 노드 필터
CREATE INDEX idx_suppliers_risk_level    ON suppliers(risk_level);
-- [역할] 협력사 리스트 FEOC 필터
CREATE INDEX idx_suppliers_feoc_status   ON suppliers(feoc_status);

-- 영역 3~4 — 공장·광산 지리 쿼리
-- [역할] Geo Audit Agent ST_DWithin 쿼리 — 공장 좌표 기준 고위험 지역 판정
CREATE INDEX idx_factories_location      ON supplier_factories USING GIST(location);
-- [역할] Geo Audit Agent — 광산 좌표 기준 신장·DRC 판정
CREATE INDEX idx_miner_coords            ON supplier_miner_details USING GIST(mine_coordinates);

-- 영역 5 — 원산지 증명서
-- [역할] Supplier Workspace Origin 탭 로딩
CREATE INDEX idx_origin_certs_supplier   ON origin_certificates(supplier_id);
-- [역할] 스케줄러 만료 임박 건 일괄 스캔 (매일 실행)
CREATE INDEX idx_origin_certs_expiry     ON origin_certificates(expires_at)
    WHERE status IN ('valid', 'expiring_soon');

-- 영역 6 — 교육
-- [역할] Training 탭 협력사별 이수 현황 조회
CREATE INDEX idx_training_records_supplier ON training_records(supplier_id);
-- [역할] 스케줄러 overdue 건 일괄 감지
CREATE INDEX idx_training_records_due    ON training_records(due_date)
    WHERE status IN ('in_progress', 'not_started');

-- 영역 7 — 부품 트리
-- [역할] 5계층 부품 트리 재귀 CTE 쿼리 성능
CREATE INDEX idx_parts_parent            ON parts(parent_part_id);
-- [역할] FTA 세번변경기준 판정 시 HS Code 기준 조회
CREATE INDEX idx_parts_hs_code           ON parts(hs_code);

-- 영역 9 — 배치·DPP
-- [역할] LangGraph Queue 페이지 배치 상태별 조회
CREATE INDEX idx_batches_status          ON batches(status);
-- [역할] 멀티테넌트 격리 배치 조회 — 모든 배치 목록 API 기준 인덱스
CREATE INDEX idx_batches_tenant_status   ON batches(tenant_id, status);
-- [역할] Dashboard DPP Ready 탭 제품별 발행 이력 조회
CREATE INDEX idx_dpp_product             ON dpp_records(product_id);

-- 영역 10 — 규제·컴플라이언스
-- [역할] Compliance Agent pgvector 코사인 유사도 RAG 검색
--        lists=100 — 약 10만 벡터 이하 적합 파티션 수
--        검색 쿼리: ORDER BY embedding <=> query_vector LIMIT 5
CREATE INDEX idx_regulations_embedding   ON regulations
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
-- [역할] Supplier Workspace AI Verification 탭 협력사별 판정 결과 조회
CREATE INDEX idx_compliance_supplier     ON compliance_results(supplier_id);

-- 영역 11 — Submission 상태머신
-- [역할] SLA 만료 임박 건 일괄 조회 (스케줄러 매일 실행)
CREATE INDEX idx_data_request_due        ON data_request_log(due_date)
    WHERE response_status = 'pending';
-- [역할] Dashboard Pending Submission 탭 상태별 대기 건 조회
CREATE INDEX idx_data_request_submission ON data_request_log(submission_status);
-- [역할] Submission Timeline 탭 시간순 이력 조회
CREATE INDEX idx_submission_history      ON submission_status_history(request_id, changed_at);

-- 영역 12 — 감사 추적
-- [역할] GET /audit/trail/{batch_id} 해시 체인 조회 — step_number 순 정렬
CREATE INDEX idx_audit_batch             ON audit_trail(batch_id, step_number);
