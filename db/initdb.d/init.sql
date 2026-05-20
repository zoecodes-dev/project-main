-- pgvector 및 PostGIS 확장 모듈 활성화
CREATE EXTENSION IF NOT EXISTS vector;
-- PostGIS는 이미지에 내장되어 있으나 확정이 안 되어 있을 경우를 대비해 명시적 활성화 가능
CREATE EXTENSION IF NOT EXISTS postgis;