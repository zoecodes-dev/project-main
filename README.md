# KIRA Compliance Intelligence Platform

> **N차(다단계) 공급망 추적 · Geo Audit 기반 배터리 DPP(디지털 제품 여권) 발행 백엔드**

협력사가 제출한 공급망 데이터를 **검증(verify) → 위험 평가(risk) → 규제 판정(compliance) → DPP 발행**까지
자동 파이프라인으로 처리하고, 애매한 건은 사람이 개입(HITL)하는 규제 대응 인텔리전스 플랫폼입니다.
EU 배터리법·UFLPA·IRA(FEOC)·EUDR·CSDDD 등 글로벌 규제 준수를 N차 공급망 단위로 추적합니다.

---

## 핵심 개념

| 개념 | 설명 |
|---|---|
| **N차 공급망 추적** | 원청(OEM)부터 말단 광산까지 재귀적으로 추적(`supply_chain_map` 재귀 CTE). 겸업·tier 점프 수용 |
| **Geo Audit** | PostGIS 공간쿼리로 고위험 지역(신장·DRC 등) 좌표 판정 → 지리적 위험 플래그 |
| **DPP 발행** | 검증·평가를 통과한 제품에 대해 불변(immutable) 디지털 제품 여권 JSON 생성·Lock |
| **HITL** | confidence·규제 위반·지리 위험 등으로 자동 판정이 애매하면 사람 심사 큐로 라우팅 |
| **FEOC** | IRA 우려 외국기업 — 지분 ≥25% 룰 검증 |

---

## 아키텍처

### 레이어 (단방향)
```
router → service → repository → models
```
- **router**: HTTP 진입점(얇은 라우팅). `db.commit()` 금지.
- **service**: 비즈니스 로직 + 이벤트 발행 + 커밋 일원화. 멀티 write는 단일 트랜잭션 atomic.
- **repository**: 직접 SQL. `flush`까지만.
- **도메인 격리**: 다른 도메인을 직접 import하지 않고 **이벤트(`events/types.py`) + `publish()`** 로만 통신.

### 이벤트 기반 (PostgreSQL LISTEN/NOTIFY)
도메인 간 통신은 ~30종 이벤트 계약(`backend/events/types.py`, 팀 전체 SSOT)으로 이뤄집니다.
발행 순서는 **① DB 변경 → ② `await db.commit()` → ③ 커밋 성공 후 `publish()`** (롤백 불일치 방지).

### 에이전트 파이프라인 (LangGraph, 8단계)
```
stage_queued
  → data_gateway   (supplier_ids·추출결과 검증)
  → verification   (FEOC 지분 검증 + 문서무결성)
  → geo_audit      (지리 위험 판정, risk_flags 생성)
  → compliance     (규제별 judge, verdict 판정)
  → risk_scoring   (compliance+geo+FEOC 종합 점수)
  → readiness      (8대 체크리스트 준비도)
  → issuance       (DPP 발행)
  → completed
```
공유 상태는 `backend/agents/state.py`의 `BatchState`(schema `batches` 테이블과 1:1 정렬).
HITL 분기는 `error_reason`(`low_confidence`/`feoc_violation`/`geographical_risk`/`risk_escalated`/`gray_zone`)으로 라우팅됩니다.

### AI vs 결정론 — **AI 호출은 2곳뿐**
| 노드/모듈 | AI | 기법 |
|---|---|---|
| `supervisor.route()` | ❌ 결정론 | `current_stage`/`confidence_score` 규칙 라우터 (LLM 없음) |
| `data_gateway` | ✅ AI | Claude 멀티모달 문서 추출(AWS Bedrock). S3 PDF/이미지 → 구조화 JSON |
| `geo_audit` | ❌ 결정론 | PostGIS 공간쿼리 + 고위험 좌표 판정 |
| `compliance` | ✅ AI(하이브리드) | RAG(Bedrock 임베딩 + pgvector 코사인) + Claude Sonnet judge(`cited_clauses` 강제) |
| `automation`(verification/risk/readiness/issuance) | ❌ 결정론 | 규칙 엔진: FEOC 25% / 가점식 리스크 / 8대 체크리스트 / DPP 생성·Lock |

> LLM tool-use(function calling)는 사용하지 않습니다. **구조화 JSON 프롬프트 + RAG** 방식.

---

## 기술 스택

| 영역 | 기술 |
|---|---|
| 웹 | FastAPI 0.110 · Uvicorn · Pydantic v2 |
| DB | PostgreSQL + **PostGIS**(공간) + **pgvector**(임베딩) · SQLAlchemy 2.0(async) · asyncpg · GeoAlchemy2 |
| 큐/비동기 | Redis · ARQ |
| 에이전트 | LangGraph 1.2 · LangChain · langgraph-checkpoint(-postgres) |
| AI | AWS Bedrock (Claude Sonnet 멀티모달 + Cohere Embed v4) via langchain-aws |
| 인증 | JWT (python-jose) · passlib/bcrypt |
| 인프라 | Docker Compose · Nginx(리버스 프록시) · EC2(SSM 배포) |
| 테스트 | pytest · pytest-asyncio · httpx |

---

## 프로젝트 구조

```
backend/
├── main.py                 # FastAPI 진입점 (라우터 등록 · 이벤트 구독)
├── core/config.py          # 설정 (pydantic BaseSettings)
├── agents/                 # LangGraph 오케스트레이션
│   ├── graph.py            #   그래프 빌드 · 체크포인터
│   ├── supervisor.py       #   결정론 라우터
│   ├── state.py            #   BatchState (공유 상태 SSOT)
│   ├── data_gateway.py     #   AI 문서 추출 (Bedrock)
│   ├── geo_audit.py        #   PostGIS 지리 위험
│   ├── compliance.py       #   RAG + Sonnet judge
│   └── automation.py       #   결정론 후처리 (verification/risk/readiness/issuance)
├── domains/<name>/         # 도메인별 {router, service, repository, models}.py
│   ├── supplier  supplychain  regulation  product  submission
│   ├── verification  risk  dpp  hitl  audit  batches  users  acl  report
├── events/types.py         # 이벤트 계약 (팀 전체 SSOT)
├── infrastructure/         # database · event_bus(LISTEN/NOTIFY) · queue(ARQ) · auth · trace
├── llm/                    # bedrock_factory · embedding_factory
├── workers/                # ARQ 큐 컨슈머
└── scripts/                # 시드 · 검증 스크립트
docker/
├── 01_schema.sql           # DB 베이스라인(동결) — 이후 변경은 alembic/
├── 02_seed_data.sql        # 4 데모 시나리오 시드
└── *.Dockerfile
ci/                         # 검증 시스템 (smoke · e2e · 컨벤션 체크)
```

---

## 실행

### 사전 요구
- Docker · Docker Compose
- `.env` (아래 환경변수)

### 기동
```bash
docker compose up --build
```
서비스 구성: `nginx`(:80) → `app`(FastAPI :8000) · `db`(PostgreSQL) · `redis` · ARQ 워커 5종
(parse · verification · risk · hitl · pipeline)

- API 문서: `http://localhost/docs`
- 헬스체크: `http://localhost/health`

### 스키마 변경 시 — alembic 마이그레이션
```bash
# 1) 마이그레이션 생성
docker compose exec app alembic revision -m "add_xxx_table"
# 2) alembic/versions/<rev>_add_xxx.py 의 upgrade()/downgrade()에 op.execute("...DDL...") 작성
# 3) 커밋 → 배포/부팅 시 app이 'alembic upgrade head'를 자동 실행 (데이터 보존)
```
- `docker/01_schema.sql`은 **베이스라인으로 동결** — 직접 수정하지 않는다. 모든 변경은 마이그레이션으로.
- `down -v && up --build`는 **빈 볼륨에서 베이스라인 재생성**할 때만(데이터 전삭제 — 실데이터 있으면 금지).

### 환경변수(.env)
```
POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB
DATABASE_URL=postgresql+asyncpg://<user>:<pass>@db:5432/<db>
REDIS_URL=redis://redis:6379/0
KIRA_EVENT_CHANNEL=<LISTEN/NOTIFY 채널명>
SECRET_KEY=<JWT 시크릿>
ALLOWED_ORIGINS=http://localhost:3000
# AWS Bedrock: EC2 IAM Role 자동 인증(운영) — 로컬은 자격 별도 필요
```

---

## 데이터베이스

- **스키마 관리**: `docker/01_schema.sql`은 **베이스라인(동결)**, 이후 변경은 **alembic 마이그레이션**(`alembic/versions/`).
  ORM(`models.py`)은 최종 스키마와 1:1 유지. (app 부팅 시 `alembic upgrade head` 자동 적용)
- **확장**: PostGIS(공간 좌표) · pgvector(규제 임베딩 1536-dim)
- **두 상태축**(`batches`):
  - `current_stage` — 노드 위치 (8단계, `stage_*`)
  - `batch_status` — 거친 국면 (`batch_processing`/`batch_hitl_wait`/`batch_completed`/`batch_rejected`)

---

## 데모 4시나리오 (`02_seed_data.sql`)

| 제품 | 성격 | 기대 흐름 |
|---|---|---|
| ① BMW iX3 108Ah | **Happy** | 검증 통과 → DPP 발행 |
| ② BMW i4 81Ah | **Gray** | 저신뢰 추출 → readiness gray_zone → HITL |
| ③ Mercedes GLC 94Ah | **Sad** | 신장 원산지 FEOC 위반 + geo 위험 → risk 에스컬레이션 → HITL 반려 |
| ④ Mercedes EQS 118Ah | **Happy** | 검증 통과 → DPP 발행 |

각 제품은 원청→말단까지 `supply_chain_map`에 N차 트리로 연결됩니다.

---

## 테스트 / 검증 (`ci/`)

| 파일 | 역할 |
|---|---|
| `ci/test_smoke.py` | 주요 엔드포인트 생존 (라우터 누락 회귀 방지) |
| `ci/test_e2e.py` | 기능 누적 e2e — write→read 왕복으로 행위 검증 |
| `ci/check_conventions.py` | 아키텍처 규칙 자동 점검 (커밋 경계·datetime 등) |
| `ci/verify.ps1` | 하루 끝 로컬 검증 러너 (오늘 추가 라우트 체크리스트) |

```bash
# docker compose 스택 기동 후
BASE_URL=http://localhost pytest ci/ -v
```

---

## 개발 규칙 (요약 — 상세는 [CLAUDE.md](CLAUDE.md))

1. **레이어 단방향**: router → service → repository → models
2. **커밋 경계**: router는 commit 금지. service가 일원화. repository는 flush만.
3. **이벤트 발행 순서**: DB 변경 → commit → **커밋 성공 후** publish
4. **도메인 격리**: 다른 도메인 import 금지. 이벤트 + publish()로만 통신.
5. **스키마 변경 = alembic 마이그레이션**: `01_schema.sql`은 동결, DDL 변경은 `alembic/versions/`로. ORM은 최종 스키마와 1:1.
6. 작업은 feature 브랜치에서. 커밋 메시지에 생성 표기·Co-Authored-By 트레일러 미포함.

---

## 배포

- **공유 EC2 배포는 `main` 브랜치 push에서만** (GitHub Actions → OIDC AWS 자격 → SSM RunShellScript).
- 브랜치 작업은 로컬에서. EC2용 스크립트는 UTF-8 인코딩 주의(Windows→Linux).
- 워크플로우: `.github/workflows/deploy.yml`

---

## 이벤트 계약 (발췌)

공급망(`SupplierInvited`·`SupplierConnected`) · 제출(`SubmissionCompleted`·`SubmissionApproved`) ·
검증(`VerificationCompleted`·`GeoRiskDetected`) · 리스크(`RiskEscalated`) · 컴플라이언스(`ComplianceCompleted`) ·
HITL(`HITLRequested`·`HITLApproved`) · DPP(`DPPIssued`) 등.
전체 정의는 `backend/events/types.py` 참조.
