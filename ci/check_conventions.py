#!/usr/bin/env python3
"""
ci/check_conventions.py  (W5 — 코드리뷰 자동화: KIRA 고질병 정합성 체크)

이 프로젝트에서 반복적으로 500을 유발한 알려진 패턴을 정적으로 잡아낸다.
주니어 LLM 코딩 특성상 같은 실수가 재발하므로, 사람 리뷰 전에 도는 1차 스크리너.

[경고 모드] 위반을 출력하되 exit 0 (머지 차단 안 함). 안정화 후 STRICT=1로 차단 전환.

검사 항목:
  C1. Enum(..., native_enum=False) 컬럼에 values_callable 누락
      → LookupError 500의 주범
  C2. datetime.utcnow() 사용 (tz-naive)
      → datetime.now(timezone.utc) 권장
  C3. queue.py QUEUE_NAMES ↔ schema processed_jobs CHECK 큐 이름 불일치
      → enqueue는 되는데 processed_jobs INSERT에서 500
  C4. repository.py 안에서의 commit()
      → 멀티라이트 원자성 파괴
"""
import os
import re
import sys
import glob
import io
import tokenize

# Windows 콘솔(cp949)에서 한글/이모지 출력 깨짐·크래시 방지 — stdout을 UTF-8로.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(REPO, "backend")
SCHEMA = os.path.join(REPO, "docker", "01_schema.sql")
STRICT = os.getenv("STRICT", "0") == "1"

violations = []


def add(code, path, line, msg):
    rel = os.path.relpath(path, REPO)
    violations.append(f"[{code}] {rel}:{line}  {msg}")


def py_files():
    return glob.glob(os.path.join(BACKEND, "**", "*.py"), recursive=True)


def code_lines(path):
    """
    파일을 토큰화해 '주석·문자열을 공백으로 지운 코드만' 라인 리스트를 반환한다(1-based 보존).
    → 주석/독스트링 안에 적힌 'commit()' · 'datetime.utcnow()' 같은 문구를 위반으로 오탐하지 않음.
      (예: "이 함수는 db.commit()을 호출하지 않는다" 주석은 코드가 아니므로 건너뜀)
    토큰화 실패(문법오류 등) 시에는 원본 라인을 그대로 반환(안전 폴백).
    """
    with open(path, encoding="utf-8") as f:
        src = f.read()
    lines = src.splitlines()
    grid = [list(line) for line in lines]  # 라인별 문자 리스트(수정용)
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type not in (tokenize.COMMENT, tokenize.STRING):
                continue
            (srow, scol), (erow, ecol) = tok.start, tok.end
            for row in range(srow, erow + 1):  # 멀티라인 문자열(독스트링) 포함
                if row - 1 >= len(grid):
                    break
                line = grid[row - 1]
                c0 = scol if row == srow else 0
                c1 = ecol if row == erow else len(line)
                for c in range(c0, min(c1, len(line))):
                    line[c] = " "
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return lines  # 폴백: 원본
    return ["".join(c) for c in grid]


# ── C1: native_enum=False 인데 values_callable 없는 컬럼 ──────────────────────
def check_enum_values_callable():
    for f in py_files():
        # 주석·문자열 제외한 코드 라인만 스캔(오탐 방지)
        for i, line in enumerate(code_lines(f), 1):
            if "native_enum=False" in line and "values_callable" not in line:
                add("C1", f, i,
                    "Enum(native_enum=False)에 values_callable 누락 → LookupError 위험")


# ── C2: datetime.utcnow() 금지 ────────────────────────────────────────────────
def check_utcnow():
    pat = re.compile(r"datetime\.utcnow\s*\(")
    for f in py_files():
        # 주석·문자열 제외(예: "datetime.utcnow() 사용 금지" 주석 오탐 방지)
        for i, line in enumerate(code_lines(f), 1):
            if pat.search(line):
                add("C2", f, i,
                    "datetime.utcnow() 금지 → datetime.now(timezone.utc) 사용")


# ── C3: queue.py QUEUE_NAMES ↔ schema CHECK 정합성 ───────────────────────────
def check_queue_schema_sync():
    qpath = os.path.join(BACKEND, "infrastructure", "queue.py")
    if not (os.path.exists(qpath) and os.path.exists(SCHEMA)):
        return
    qtext = open(qpath, encoding="utf-8").read()
    stext = open(SCHEMA, encoding="utf-8").read()

    # queue.py에서 *_QUEUE = "..." 우변의 값들
    py_queues = set(re.findall(r'=\s*"([a-z_]+_queue)"', qtext))
    # schema chk_processed_queue CHECK IN (...) 의 값들
    m = re.search(r"chk_processed_queue\s+CHECK\s*\(\s*queue_name\s+IN\s*\((.*?)\)\)",
                  stext, re.DOTALL)
    sql_queues = set(re.findall(r"'([a-z_]+_queue)'", m.group(1))) if m else set()

    only_py = py_queues - sql_queues
    only_sql = sql_queues - py_queues
    for q in sorted(only_py):
        add("C3", qpath, 0, f"큐 '{q}'가 queue.py에 있으나 schema CHECK에 없음")
    for q in sorted(only_sql):
        add("C3", SCHEMA, 0, f"큐 '{q}'가 schema CHECK에 있으나 queue.py에 없음")


# ── C4: repository.py 안에서의 commit() ──────────────────────────────────────
#   메모리 원칙: 커밋은 service.py가 소유. repository-level commit이 원자성을 깬다.
#   router/state_machine 등은 정당한 단일 트랜잭션일 수 있어 C4 대상에서 제외하고,
#   가장 명확한 위반 위치인 repository.py만 잡는다(초기 19건 오탐 → 한정으로 좁힘).
def check_commit_ownership():
    pat = re.compile(r"\.commit\s*\(\s*\)")
    for f in py_files():
        if os.path.basename(f) != "repository.py":
            continue
        # 주석·문자열 제외(예: "db.commit()을 호출하지 않는다" 독스트링 오탐 방지)
        for i, line in enumerate(code_lines(f), 1):
            if pat.search(line):
                add("C4", f, i,
                    "repository.py 내 commit() → 원자성 파괴 (커밋은 service가 소유)")


def main():
    check_enum_values_callable()
    check_utcnow()
    check_queue_schema_sync()
    check_commit_ownership()

    print("=" * 60)
    print("KIRA 정합성 체크 (코드리뷰 자동화)")
    print("=" * 60)
    if not violations:
        print("✅ 위반 0건")
        return 0

    by_code = {}
    for v in violations:
        code = v[1:3]
        by_code.setdefault(code, []).append(v)
    for code in sorted(by_code):
        print(f"\n── {code} ({len(by_code[code])}건) ──")
        for v in by_code[code]:
            print(f"  {v}")

    print(f"\n총 {len(violations)}건 위반.")
    if STRICT:
        print("STRICT 모드 → 머지 차단 (exit 1)")
        return 1
    print("경고 모드 → 머지 허용 (exit 0). 위 항목은 수정 권장.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
