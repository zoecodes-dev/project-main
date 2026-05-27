-- ============================================================
-- pgvector IVFFlat 인덱스 — regulations.embedding
-- 위치: db/initdb.d/seed_regulations_index.sql
--
-- 실행 전제: seed_regulations.sql로 regulations row가 먼저 적재돼 있어야 한다.
-- IVFFlat 인덱스는 데이터가 있어야 lists 파티션이 의미 있음.
-- (빈 테이블에 생성해도 오류는 없으나 효과 없음)
--
-- schema.sql 인덱스 섹션 (라인 1442~1443)과 동일한 정의.
-- schema.sql 초기화 시 이미 생성됐다면 IF NOT EXISTS로 중복 방지.
--
-- 인덱스 파라미터:
--   USING ivfflat        — IVFFlat 알고리즘 (approximate nearest neighbor)
--   vector_cosine_ops    — 코사인 유사도 기준 검색
--   lists = 100          — 약 10만 벡터 이하 적합 파티션 수
--
-- 검색 쿼리 패턴 (Compliance Agent 은지):
--   SELECT * FROM regulations
--   ORDER BY embedding <=> :query_vector
--   LIMIT 5;
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_regulations_embedding
    ON regulations
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
