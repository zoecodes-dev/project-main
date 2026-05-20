-- ============================================================
-- KIRA — Audit Domain 관련 테이블 발췌
-- 담당: 팀원 A (지혜) | Pipeline Coordinator
-- 새 대화 시작 시 이 파일을 첨부할 것
-- ============================================================


-- ============================================================
-- 직접 담당 테이블
-- ============================================================

-- [테이블 역할] 파이프라인 각 단계의 실행 결과를 row로 기록.
--              해시 체인(prev_hash → output_hash)으로 무결성 보장.
--              @trace_node 데코레이터가 자동으로 INSERT.
CREATE TABLE audit_trail (
    audit_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- [역할] 어떤 배치의 기록인지. batches.batch_id FK.
    batch_id       UUID REFERENCES batches(batch_id),

    -- [역할] 배치 내 실행 순서. GET /audit/trail 응답 정렬 기준.
    step_number    INT,

    timestamp      TIMESTAMPTZ DEFAULT now(),

    -- [역할] 실행된 노드 유형. 허용값: agent / tool / human
    node_type      VARCHAR(20),

    -- [역할] 실행된 노드 이름. 예: data_gateway, compliance, hitl_interrupt
    node_name      VARCHAR(100),

    -- [역할] 사용된 LLM 버전. LLM 미사용 노드는 NULL.
    model_version  VARCHAR(50),

    prompt_version VARCHAR(20),

    -- [역할] 노드 실행 소요 시간 (밀리초).
    duration_ms    INT,

    -- [역할] 입력값의 SHA-256 해시.
    input_hash     VARCHAR(64),

    -- [역할] 출력값의 SHA-256 해시. 다음 row의 prev_hash로 연결.
    output_hash    VARCHAR(64),

    -- [역할] 직전 step의 output_hash. NULL이면 첫 번째 step.
    --        GET /audit/trail/{batch_id} 응답에서 chain_valid 검증에 사용.
    prev_hash      VARCHAR(64),

    decision_text  TEXT,

    -- [역할] Compliance Agent가 인용한 법조항 목록(JSONB).
    citations      JSONB
);

-- [테이블 역할] 규제 개정 시 기존 공급망에 미치는 영향 분석 결과.
--              newly_required_fields: 신규 필수 필드 목록.
--              gray_zone_items: HITL 검토가 필요한 모호 항목.
CREATE TABLE gap_analysis_results (
    analysis_id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    regulation_id         UUID REFERENCES regulations(regulation_id),
    previous_version_id   UUID REFERENCES regulations(regulation_id),

    -- [역할] 이번 규제 개정으로 영향받는 협력사 ID 배열(JSONB).
    affected_supplier_ids JSONB,

    -- [역할] 신규 필수 항목 목록(JSONB).
    newly_required_fields JSONB,

    -- [역할] HITL 검토가 필요한 회색지대 항목(JSONB).
    gray_zone_items       JSONB,

    analyzed_at           TIMESTAMPTZ DEFAULT now(),
    reviewed_by           UUID REFERENCES users(user_id),
    reviewed_at           TIMESTAMPTZ
);


-- ============================================================
-- 읽기 전용 참조 테이블
-- ============================================================

-- [테이블 역할] LangGraph 배치 처리 단위.
--              audit_trail.batch_id FK 기준. Supervisor 라우팅 조건 판단에 사용.
CREATE TABLE batches (
    batch_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    product_id       UUID REFERENCES products(product_id),
    bom_version_id   UUID REFERENCES bom_versions(bom_version_id),

    -- [역할] 소속 테넌트 FK. 모든 배치 조회 쿼리에 WHERE tenant_id = :current_tenant 필수.
    tenant_id        UUID REFERENCES tenants(tenant_id),

    received_at      TIMESTAMPTZ DEFAULT now(),

    -- [역할] 최종 수출 목적지. Compliance Agent 규제 분기 기준.
    --        허용값: US / EU / KR
    destination      VARCHAR(2),

    -- [역할] LangGraph 현재 처리 단계. BatchState.current_stage와 동기화.
    current_stage    VARCHAR(50),

    -- [역할] 배치 처리 상태. Supervisor가 단계 전이 시 갱신.
    --        허용값: processing / hitl_wait / completed / rejected
    status           VARCHAR(20),

    -- [역할] 현재 단계의 AI 판정 신뢰도 점수 (0.0~1.0).
    --        0.85 미만이면 Supervisor가 hitl_interrupt 노드로 라우팅.
    confidence_score NUMERIC(5,4)
);


-- ============================================================
-- 인덱스 (Audit Domain 관련)
-- ============================================================

-- [역할] GET /audit/trail/{batch_id} 해시 체인 조회 — step_number 순 정렬
CREATE INDEX idx_audit_batch ON audit_trail(batch_id, step_number);

-- [역할] LangGraph Queue 페이지 배치 상태별 조회
CREATE INDEX idx_batches_status ON batches(status);

-- [역할] 멀티테넌트 격리 배치 조회
CREATE INDEX idx_batches_tenant_status ON batches(tenant_id, status);
