# Due Diligence Domain (담당 D · 영수)

## 1. 개요
협력사/공장에 대한 **실사(Audit)** 기록을 관리하는 도메인. 실사 등록 → 보고서 업로드(파일 연동) → 발견사항(findings)·시정조치(CAPA) 추적까지의 라이프사이클을 담당함. 스펙 §5(5.1~5.5) 대응. 신규 폴더로 신설됨.

## 2. 주요 책임
- **실사 목록/상세 조회**: 내 테넌트 소유 실사 기록만 노출(존재 은닉 404).
- **실사 등록**: supplier/factory 대상으로 신규 실사 생성(`audit_status='requested'`).
- **보고서 업로드**: multipart 파일을 `/files` 모듈로 저장 후 `report_file_id` 연결 + `result`/`score` 갱신.
- **CAPA 관리**: `corrective_actions` JSONB 배열 내 개별 과제(capa_id)의 상태 갱신.

## 3. 관리 테이블
- `supplier_audit_records`: 실사 기록 본체. (0004 마이그레이션으로 `audit_name`/`factory_id`/`score`/`report_file_id` 컬럼 추가, `audit_date` NOT NULL 완화 + `DEFAULT CURRENT_DATE`).
- 참조: `supplier_risk_profiles`(riskScore), `suppliers`(tenant 격리·supplier_name), `files`(보고서 FK).
- 연관 테이블(스펙): `due_diligence_policies`, `detention_cases` (현 단계 미사용 — 향후 정책/구금 케이스 확장 지점).

## 4. tenant 격리 전략
`supplier_audit_records`에는 `tenant_id` 컬럼이 없으므로 **`suppliers.tenant_id`와 JOIN**하여 스코프(CLAUDE.md §4). 단건/하위리소스가 타 테넌트면 **404**(403 아님 — 존재 은닉). 전 엔드포인트 `Depends(get_current_user)` 필수.

## 5. API 엔드포인트 (prefix `/due-diligence`)
| # | Method | Path | 설명 | 응답 |
| :--- | :--- | :--- | :--- | :--- |
| 5.1 | `GET` | `/due-diligence?status=&search=&page=&size=` | 실사 목록(테넌트 스코프, 회사명 검색) | bare array + `X-Total-Count` |
| 5.2 | `GET` | `/due-diligence/{auditId}` | 실사 단건 상세(findings/capa 포함) | object / 404 |
| 5.3 | `POST` | `/due-diligence` | 실사 신규 등록 | `{ auditId }` (201) |
| 5.4 | `PATCH` | `/due-diligence/{auditId}/report` | 보고서 multipart 업로드 + result/score 갱신 | `{ auditId, result, score, reportFileId }` |
| 5.5 | `PATCH` | `/due-diligence/{auditId}/capa/{capaId}` | CAPA 과제 상태 갱신 | 갱신된 capa 배열 |

### 응답 계약(프론트 `lib/api.ts` 1:1 — 백엔드 snake_case → 프론트 snakeToCamel)
- **5.1 항목**: `audit_id, supplier_id, supplier_name, factory_id, type, status, result, score, risk_score, capa_count, has_report`
- **5.2 추가**: `scope, agency, completed_at, findings[{title,severity,description}], capa[{capa_id,title,status,due_date}], report_file_id`
- **5.3 요청**: `{ supplier_id?, factory_id?, name, scope }`
- **5.5 요청**: `{ status: "완료" }`

## 6. 레이어 / 커밋 규약 (CLAUDE.md §1)
- 단방향: router → service → repository. 역방향·횡단 import 금지.
- **커밋은 service 일원화**. repository는 `flush`만(commit 없음).
- 도메인 격리: 타 도메인 import 금지. **예외 — 파일 저장은 공통 `/files` 모듈(B의 P1-C 산출물)** 을 §7 합의에 따라 service에서 호출(`backend.domains.files.service.upload_file`).

## 7. 보고서 업로드 흐름 (5.4)
1. multipart field `file` 수신 → `await file.read()`.
2. `file_service.upload_file(...)` 로 S3 저장 + `files` insert(내부 commit) → `file_id` 수령.
3. `update_report`로 `report_file_id`/`result`/`score` `COALESCE` 갱신(미전달 값은 기존 유지).
4. 테넌트 소유 확인 실패 시 `None` → 404.

## 8. 제약 사항
- 모든 주요 쿼리 `@trace_tool` 적용.
- `result` 값은 스키마 CHECK 제약(`pass|conditional_pass|fail|pending`) 준수 필요.
- `findings`/`corrective_actions`는 JSONB — raw `text()` 쿼리에서 문자열로 반환될 수 있어 service `_normalize_jsonb`로 list 정규화.
