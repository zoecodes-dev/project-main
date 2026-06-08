# W4 D1-D2 Unified Action Queue API

## 작업 요약

프론트의 my-task, risk/actions 화면에서 사용할 통합 작업 큐 조회 API를 추가했다.
schema에 이미 정의된 `v_action_items` 뷰를 그대로 조회하며, 이 뷰는 제출검토(`data_request_log`),
실사(`supplier_audit_records`), HITL 보류(`hitl_reviews`) 작업을 `UNION ALL`로 합친다.

이번 작업은 조회 전용 API이므로 상태 전이, `publish`, queue enqueue는 추가하지 않았다. 조치 처리는 각 도메인의 기존 흐름에서 담당하고,
작업 큐 API는 프론트가 "내가 처리할 일"을 한 번에 읽을 수 있도록 하는 역할만 맡는다.

## 변경 사항

- `backend/domains/audit/repository.py`
  - `list_action_items` 추가.
  - `v_action_items`에서 `action_id`, `source_type`, `title`, `supplier_id`, `assigned_to`, `due_date`, `action_status`를 조회.
  - `status`, `source_type`, `assigned_to`, unresolved 필터를 지원.
  - asyncpg의 `NULL` 파라미터 타입 추론 문제를 피하기 위해 `text`, `uuid` cast를 명시.
  - `list_gap_analysis_results` 추가.
  - `gap_analysis_results`에서 `affected_supplier_ids`, `newly_required_fields`를 조회.

- `backend/domains/audit/service.py`
  - `get_action_items`
  - `get_my_action_items`
  - `get_gap_analysis_results`
  - 위 조회 함수를 service -> repository 패턴으로 연결.

- `backend/domains/audit/router.py`
  - `GET /actions`
  - `GET /actions/mine`
  - `GET /audit/gap-analysis/{regulation_id}`
  - 응답 모델 `ActionItemOut`, `GapAnalysisOut` 추가.
  - 현재 공통 인증 의존성이 아직 없어 `/actions/mine`은 임시로 `X-User-Id` 헤더를 `current_user.user_id`처럼 사용한다.

- `backend/main.py`
  - `/actions` 라우터 등록.

## API 동작

```text
GET /actions?status=&source_type=
```

- `v_action_items` 전체 조회.
- `status`: `open`, `sent`, `review`, `resolved`, `blocked`.
- `source_type`: `SUB`, `DD`, `HITL`.
- 응답 필드: `action_id`, `source_type`, `title`, `supplier_id`, `assigned_to`, `due_date`, `action_status`.

```text
GET /actions/mine
```

- `assigned_to = current_user.user_id` 기준 개인 작업 조회.
- 현재 로컬 검증에서는 `X-User-Id` 헤더로 사용자 ID를 전달.
- `resolved`는 제외하고 미해결 작업만 반환.

```text
GET /audit/gap-analysis/{regulation_id}
```

- `gap_analysis_results` 조회.
- 결과가 없으면 빈 배열 `[]` 반환.
- 반환 필드: `affected_supplier_ids`, `newly_required_fields`.

## Docker 검증 결과

Docker compose는 현재 app이 외부 `8000` 포트를 직접 publish하지 않고, nginx가 `80`으로 받아 app 컨테이너의 내부 `8000`으로 전달하는 구조다.
따라서 로컬 검증 URL은 `http://localhost` 기준으로 확인했다.

```text
GET http://localhost/actions
GET http://localhost/actions?source_type=HITL
GET http://localhost/actions/mine
GET http://localhost/audit/gap-analysis/0fee34da-4411-4372-ba7a-1a8163fdd553
```

- `/actions`에서 SUB, DD, HITL 작업이 함께 반환됨.
- seed 기준 HITL 2건 반환 확인.
- `/actions/mine`에서 `X-User-Id: 11111111-0000-4000-8000-000000000002` 기준 미해결 개인 작업 반환 확인.
- gap-analysis 결과가 없을 때 `[]` 정상 응답 확인.

## 참고

- `localhost:8000`은 현재 compose에서 외부 publish 대상이 아니므로 직접 테스트 대상이 아니다.
- 프론트 연동 기준은 nginx 경유 `http://localhost`이다.
- 예전 단일 worker 컨테이너 `kira-worker`가 orphan으로 남아 `backend.workers.geo_risk_worker.WorkerSettings`를 찾으며 재시작하던 문제는 `docker compose up -d --remove-orphans`로 정리했다.
