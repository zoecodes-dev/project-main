-- ============================================================
-- regulations 시드 데이터 — 11종 전체 적재
-- 위치: db/initdb.d/seed_regulations.sql
--
-- DECISION_LOG 결정 #7 (라인 406~413) 기준:
--   실동작 judge(7): UFLPA / IRA / EU_BATTERY / EU_BATTERY_ART7 /
--                    EU_BATTERY_ART47 / EUDR / CSDDD
--   stub judge(4):   EUDR_FSC / CBAM / CONFLICT_MINERALS / CRMA
--
-- stub judge row: regulations 테이블에 완전히 적재되나
--   Compliance Agent REGULATION_JUDGES에서 판정은 'passed' 반환.
--   row 부재로 인한 크래시 방지 목적 (DECISION_LOG 라인 412).
--
-- embedding_status: 전체 'pending' (임베딩은 운영 시 문서 적재 후 처리).
-- embedding: NULL (시드 단계에서는 벡터 없음).
-- document_s3_url: NULL (시연 환경 — 실제 문서 URL 없음).
-- ============================================================

INSERT INTO regulations (
    regulation_id,
    name,
    regulation_code,
    region,
    description,
    version,
    effective_from,
    document_s3_url,
    embedding_status,
    embedding
) VALUES

-- ──────────────────────────────────────────────
-- 실동작 judge 7종
-- ──────────────────────────────────────────────

-- 1. UFLPA — 미국 강제노동방지법
(
    uuid_generate_v4(),
    'Uyghur Forced Labor Prevention Act',
    'UFLPA',
    'US',
    '미국 강제노동방지법',
    '2021',
    '2022-06-21',
    NULL,
    'pending',
    NULL
),

-- 2. IRA — 미국 인플레이션감축법 (FEOC)
(
    uuid_generate_v4(),
    'Inflation Reduction Act — Foreign Entity of Concern',
    'IRA',
    'US',
    '미국 인플레이션감축법',
    '2022',
    '2024-01-01',
    NULL,
    'pending',
    NULL
),

-- 3. EU_BATTERY — EU 배터리법 (기본)
(
    uuid_generate_v4(),
    'EU Battery Regulation (EU) 2023/1542',
    'EU_BATTERY',
    'EU',
    '재활용 함량',
    '2023/1542',
    '2023-08-17',
    NULL,
    'pending',
    NULL
),

-- 4. EU_BATTERY_ART7 — EU 배터리법 제7조 (탄소발자국)
(
    uuid_generate_v4(),
    'EU Battery Regulation Article 7 — Carbon Footprint',
    'EU_BATTERY_ART7',
    'EU',
    '탄소발자국',
    '2023/1542 Art.7',
    '2024-07-18',
    NULL,
    'pending',
    NULL
),

-- 5. EU_BATTERY_ART47 — EU 배터리법 제47조 (공급망 실사 DDP)
(
    uuid_generate_v4(),
    'EU Battery Regulation Article 47 — Supply Chain Due Diligence',
    'EU_BATTERY_ART47',
    'EU',
    '공급망 실사 (DDP)',
    '2023/1542 Art.47',
    '2023-08-17',
    NULL,
    'pending',
    NULL
),

-- 6. EUDR — EU 산림파괴방지규정
(
    uuid_generate_v4(),
    'EU Deforestation Regulation (EU) 2023/1115',
    'EUDR',
    'EU',
    'EU 산림파괴방지법',
    '2023/1115',
    '2023-06-29',
    NULL,
    'pending',
    NULL
),

-- 7. CSDDD — EU 기업지속가능성실사지침 (LkSG 통합)
(
    uuid_generate_v4(),
    'Corporate Sustainability Due Diligence Directive (EU) 2024/1760',
    'CSDDD',
    'EU',
    '공급망 실사',
    '2024/1760',
    '2024-07-25',
    NULL,
    'pending',
    NULL
),

-- ──────────────────────────────────────────────
-- stub judge 4종 (row 완전 적재, 판정은 통과 반환)
-- ──────────────────────────────────────────────

-- 8. EUDR_FSC — EUDR FSC 인증 경로 (향후 확장)
(
    uuid_generate_v4(),
    'EU Deforestation Regulation — FSC Certification Path',
    'EUDR_FSC',
    'EU',
    'EUDR FSC 인증 경로. 국제산림관리협의회(FSC) 인증을 통한 EUDR 준수 경로. 현재 stub — 향후 FSC 인증 데이터 연동 시 실동작 전환.',
    '2023/1115-FSC',
    '2023-06-29',
    NULL,
    'pending',
    NULL
),

-- 9. CBAM — EU 탄소국경조정제도
(
    uuid_generate_v4(),
    'Carbon Border Adjustment Mechanism (EU) 2023/956',
    'CBAM',
    'EU',
    'EU 탄소국경조정',
    '2023/956',
    '2026-01-01',
    NULL,
    'pending',
    NULL
),

-- 10. CONFLICT_MINERALS — EU 분쟁광물규정
(
    uuid_generate_v4(),
    'EU Conflict Minerals Regulation (EU) 2017/821',
    'CONFLICT_MINERALS',
    'EU',
    'EU 분쟁광물 규정',
    '2017/821',
    '2021-01-01',
    NULL,
    'pending',
    NULL
),

-- 11. CRMA — EU 핵심원자재법
(
    uuid_generate_v4(),
    'Critical Raw Materials Act (EU) 2024/1252',
    'CRMA',
    'EU',
    'EU 핵심원자재법',
    '2024/1252',
    '2024-05-23',
    NULL,
    'pending',
    NULL
);
