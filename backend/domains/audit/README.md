# W2 D3 — Audit 응답 스키마 models.py 이동 + main 머지

## 📌 한 줄 요약

router.py에 있던 Pydantic 응답 스키마 4개를 models.py로 옮겨 팀 컨벤션에 맞추고, main에 머지된 C(Product) 도메인 수정을 내 브랜치로 합쳤다.

## 🛠️ 변경 사항 (What & How)

- **기존**: Audit 응답 스키마(`AuditTrailRow`, `ChainBreakOut`, `ChainWarningOut`, `ChainVerificationOut`)가 `router.py` 안에 정의돼 있었음. 다른 도메인은 스키마를 `models.py`에 모으는 컨벤션.
- **변경**:
  - 응답 스키마 4개를 `router.py` → `models.py`로 이동. `router.py`는 `from backend.domains.audit.models import AuditTrailRow, ChainVerificationOut`로 가져와 사용.
  - `models.py`는 ORM(`AuditTrail`)과 Pydantic 스키마를 한 파일에 두되, `# === ORM ===` / `# === API 응답 스키마 ===` 섹션 주석으로 구분.
  - 쿼리 파라미터용 `NodeType` Enum은 입력 검증 용도라 `router.py`에 유지.
- **이유**:
  - 팀 전체가 "응답 스키마는 models.py로 모은다"로 통일 → 도메인 간 구조 일관성. 별도 `schemas.py`를 만들면 도메인 폴더 표준 파일 규칙(임의 파일 금지) 위반이므로, 기존 `models.py`에 합치는 것이 규칙에 부합.
  - 동작은 바뀌지 않는 리팩토링(위치만 이동). 엔드포인트 2개와 404 가드 동작 모두 그대로 유지.

## 🐛 이슈 및 트러블슈팅

- **발생 1 — ORM/Pydantic 이름 충돌 가능성**: ORM의 `UUID`(postgres 타입)와 Pydantic의 `uuid.UUID`(파이썬 타입)가 한 파일에 공존.
  - 해결: Pydantic 필드는 `uuid.UUID`로 풀네임 사용, postgres `UUID`는 그대로 두어 충돌 회피.
- **발생 2 — 앱 부팅 실패**: `sqlalchemy.exc.ArgumentError: Type annotation for "Product.bom_versions" can't be correctly interpreted ... ORM annotations should make use of Mapped[]`
  - 원인: Product(C 도메인) models.py의 `bom_versions` relationship이 SQLAlchemy 2.0 `Mapped[]` 없이 정의됨. 앱이 모든 도메인 ORM을 한꺼번에 로딩하므로 한 도메인 ORM이 깨지면 전체 부팅 실패. (Audit 코드와 무관)
  - 해결: C 도메인 책임이라 직접 수정하지 않고 C에게 줄 번호와 함께 전달 → C가 수정 후 main 머지 → `git pull origin main`으로 받아 해결.
- **git 머지 트러블**: 커밋 시 편집기(Vim)가 열려 빠져나오지 못함. 이후 머지 마무리 과정에서 충돌 표시 없이 정리됨.
  - 메모: `git commit -m "메시지"`처럼 `-m`을 붙이면 편집기가 열리지 않음. 머지 커밋은 `git commit --no-edit`로 마무리.

## 💡 후속 논의 및 의견

- DECISION_LOG.md가 새 SSOT로 지정됨. 향후 "일괄 수정" 실행 시 schema.sql이 변경될 수 있으므로(hitl_reviews 신설, 각종 status enum 확정 등), 그 시점에 Audit의 `models.py` ORM을 새 schema와 다시 대조 필요. 단, 현재 결정 #1~9에 audit_trail 컬럼을 바꾸는 결정은 없어 지금 ORM은 영향 없음.
- 다음 작업(W2 D4): 단계별 소요시간(`duration_ms`) 집계 조회 + 프론트 PM 컨텍스트 적용.

## ✅ 동작 확인

- `GET /audit/trail/{batch_id}/verify` — 존재하지 않는 batch_id(`00000000-...`) → **404** (없는 배치가 chain_valid:true로 거짓 통과하는 것 방지)
- `GET /audit/trail/{batch_id}` — 존재하지 않는 batch_id → **200 []** (조회는 빈 목록이 정상)
- `docker-compose up --build` 후 앱 정상 부팅, `/docs`에 엔드포인트 2개 표시 확인