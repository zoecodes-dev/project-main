# CI / 검증 시스템 사용법

> 하루 끝에 한 줄로 "오늘 작업에 깨진 곳은 없는지"를 끝까지 확인하는 로컬 게이트입니다.
> GitHub Actions(PR)와 **같은 `ci/` 스크립트**를 로컬에서 먼저 돌려서, *로컬에서 통과하면 CI에서도 통과* 하도록 맞춰 두었습니다.
>
> 📖 각 파일이 **내부적으로 어떻게 동작하는지** 자세히 알고 싶으시면 [GUIDE.md](GUIDE.md)를 참고해 주세요.

---

## TL;DR — 이것만 기억하시면 됩니다

```powershell
.\ci\verify.ps1          # 퇴근 전 풀 검증 (정적 → 스택 → smoke+e2e → 리포트)
.\ci\verify.ps1 -Fast    # 빠른 검사만 (Docker 불필요, 초 단위)
```

끝에 **`PASS ✅`** 가 뜨면 통과한 것이고, **`FAIL`** 이면 바로 위 pytest 출력에서 실패한 케이스를 확인하시면 됩니다.

---

## 게이트는 2층으로 구성되어 있습니다

| 층 | 언제 | 무엇을 | 속도 |
|---|---|---|---|
| **빠른 정적 게이트** | 매 push (자동) / `-Fast` | 정합성 체크 + ruff + 오늘 변경 요약 | 초 |
| **풀 게이트** | 퇴근 전 / PR 전 | 스택을 띄우고 smoke + 기능 e2e + 메트릭 | 분 |
| **PR 게이트** | PR을 열면 (자동, GitHub) | 위 풀 게이트를 클라우드에서 실행 + PR 코멘트 | 자동 |

세 층 모두 **같은 스크립트**를 사용합니다. 그래서 로컬에서 통과하면 PR에서도 통과합니다.

---

## `verify.ps1` — 풀 게이트 러너

```powershell
.\ci\verify.ps1              # 기본: down -v → up --build → smoke+e2e → 메트릭
.\ci\verify.ps1 -Fast        # 1~2단계만 (정적 + 변경 요약). Docker를 띄우지 않습니다
.\ci\verify.ps1 -NoRebuild   # 이미 떠 있는 스택을 재사용 (재빌드 생략, 빠릅니다)
```

**5단계로 진행됩니다:**
1. 정적 검사 — `check_conventions.py` + `ruff` (경고 모드, 막지 않습니다)
2. 오늘 변경 — `git diff` 요약 + **오늘 추가된 `@router` 체크리스트** (e2e 커버 대상)
3. 스택 — `docker compose down -v && up --build` (스키마 신선화)
4. **스모크 + 기능 e2e** — `pytest` (← 여기서 실패하면 게이트가 **FAIL**)
5. 메트릭 리포트 — 변경규모·정합성·테스트·코드베이스 통계

**종료 코드:** `0` PASS · `1` 테스트 실패 · `2` Docker 미실행 · `3` 스택 기동 실패

**환경변수:** `BASE_REF` (diff 비교 기준, 기본값 `origin/develop`)

---

## 각 스크립트가 하는 일 (요약)

| 파일 | 역할 | 단독 실행 |
|---|---|---|
| `verify.ps1` | 위 5단계를 묶는 오케스트레이터 (Windows) | `.\ci\verify.ps1` |
| `check_conventions.py` | KIRA 고질병 **C1~C4** 정적 차단 | `python ci/check_conventions.py` |
| `test_smoke.py` | 엔드포인트 생존 + 라우터 누락 회귀 방지 | `pytest ci/test_smoke.py` |
| `test_e2e.py` | **기능 e2e (누적형)** — write→read 왕복 검증 | `pytest ci/test_e2e.py` |
| `metrics_report.py` | 개발 메트릭 마크다운 리포트 | `python ci/metrics_report.py` |

> 각 파일의 동작 원리·내부 함수·설계 의도는 [GUIDE.md](GUIDE.md)에 자세히 정리해 두었습니다.

**check_conventions가 잡는 것 (C1~C4):**
- **C1** `Enum(native_enum=False)`에 `values_callable` 누락 → LookupError
- **C2** `datetime.utcnow()` (tz-naive) → `datetime.now(timezone.utc)` 권장
- **C3** `queue.py` 큐 이름 ↔ schema `processed_jobs` CHECK 불일치
- **C4** `repository.py` 안의 `.commit()` → 원자성 파괴 (커밋은 service가 소유)

경고 모드라서 위반이 있어도 막지 않습니다. **차단하시려면** `STRICT=1`로 실행해 주세요:
```powershell
$env:STRICT=1; python ci/check_conventions.py   # 위반이 있으면 exit 1
```

---

## 기능 e2e는 "매일 새로 짜는 것이 아닙니다" (중요)

`test_e2e.py`는 **누적 스위트**입니다. 규칙은 하나뿐입니다.

> **기능을 만들 때 그 기능의 e2e 함수 하나를 `test_e2e.py`에 추가합니다. 하루 끝에는 전체를 다시 돌립니다.**

이렇게 하면 오늘 만든 것뿐 아니라 **지난 모든 기능이 매일 재검증**되어 회귀를 막을 수 있습니다.
"오늘 무엇을 만들었더라" 하는 부분은 `verify.ps1`이 **오늘 추가된 라우트(@router)를 체크리스트로** 띄워 주니, 그중 커버되지 않은 것이 보이면 함수를 하나 더 추가하시면 됩니다.

**추가 템플릿** (`test_e2e.py` 맨 아래에):
```python
def test_<기능>_<날짜>(client, a_supplier_id):
    resp = client.post(f"/suppliers/{a_supplier_id}/...", json={...})
    assert resp.status_code == 200
    # write 했다면 read로 되돌려 확인합니다 (왕복)
```

---

## pre-push 훅 (정적 게이트 자동화)

push 직전에 정적 검사를 자동으로 실행합니다(경고만, 막지 않음). **각자 한 번** 활성화해 주셔야 합니다:

```powershell
git config core.hooksPath .githooks
```

이후에는 `git push` 할 때마다 `check_conventions` + `ruff`가 자동으로 실행됩니다.
차단형으로 쓰시려면 `.githooks/pre-push` 안의 `STRICT` 주석을 참고해 주세요.

---

## 준비물 / 트러블슈팅

| 증상 | 원인 / 해결 |
|---|---|
| `exit 2` Docker 미실행 | Docker Desktop을 켜고 다시 `.\ci\verify.ps1` 를 실행해 주세요 |
| `exit 3` health 응답 없음 | 스택 기동 실패 → 출력된 `docker compose logs`를 확인해 주세요 |
| `ruff 미설치 — 건너뜀` | (선택) `pip install ruff` 를 하시면 정적 분석이 추가됩니다 |
| `pytest`/`httpx` 없음 | `pip install pytest httpx` |
| e2e가 "시드 협력사 없음" | `docker compose down -v && up --build` 로 시드를 다시 적재해 주세요 |
| 한글/이모지 깨짐 | 정상 동작에는 영향이 없습니다 (콘솔 표시 문제). 스크립트는 UTF-8을 강제합니다 |

**참고 사항**
- `ci/report.md`, `ci/pytest-results.xml`은 생성 아티팩트라 `.gitignore` 처리되어 있습니다.
- PR을 열면 `.github/workflows/ci.yml`이 같은 검증을 돌리고 결과를 **PR 코멘트(갱신형)**로 남깁니다.
- 경고 모드라서 CI가 빨갛게 나와도 머지를 막지 않습니다. 안정화되면 `STRICT` 차단으로 전환하실 수 있습니다.
