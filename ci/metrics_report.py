#!/usr/bin/env python3
"""
ci/metrics_report.py  (W5 — 데이터 분석: 개발 메트릭 → PR 코멘트 + 아티팩트)

PR diff와 코드베이스에서 개발 메트릭을 수집해 마크다운 리포트를 생성한다.
CI가 이 출력을 ① PR 코멘트로 게시 ② 아티팩트(report.md)로 업로드 한다.

수집 메트릭:
  M1. 변경 규모 (변경 파일 수, +/- 라인) ← git diff 기반
  M2. 정합성 위반 건수 (check_conventions 연동)
  M3. 백엔드 코드 통계 (도메인 수, 라우터 수, 워커 수, 테이블 수)
  M4. 테스트 결과 요약 (pytest 결과 파일이 있으면)

환경변수:
  BASE_REF   비교 기준 브랜치 (기본 origin/main)
  OUT        리포트 출력 경로 (기본 ci/report.md)
"""
import os
import re
import sys
import subprocess
import glob

# Windows 콘솔(cp949)에서 이모지/한글 print 크래시 방지 — stdout을 UTF-8로.
# (report.md 파일 쓰기는 이미 encoding='utf-8'. 이건 print 경로용.)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(REPO, "backend")
SCHEMA = os.path.join(REPO, "docker", "01_schema.sql")
BASE_REF = os.getenv("BASE_REF", "origin/main")
OUT = os.getenv("OUT", os.path.join(REPO, "ci", "report.md"))


def sh(cmd):
    # 자식 출력은 UTF-8로 디코딩한다(Windows 기본 cp949로 읽으면 한글 출력이
    # 깨져 정규식 파싱이 0건으로 오집계됨). git 출력(파일명)도 UTF-8 안전.
    try:
        return subprocess.check_output(cmd, cwd=REPO, encoding="utf-8",
                                       errors="replace",
                                       stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""


# ── M1. 변경 규모 ─────────────────────────────────────────────────────────────
def diff_stats():
    stat = sh(["git", "diff", "--numstat", f"{BASE_REF}...HEAD"])
    files, added, removed = 0, 0, 0
    for line in stat.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            files += 1
            added += int(parts[0]) if parts[0].isdigit() else 0
            removed += int(parts[1]) if parts[1].isdigit() else 0
    return files, added, removed


# ── M2. 정합성 위반 (check_conventions 출력 파싱) ────────────────────────────
def convention_violations():
    # "python3" 하드코딩은 Windows에 python3가 없어 실패(→ 0건 오집계). 현재 인터프리터로
    # 호출하면 OS 무관하게 동작하고 CI(python3.12)에서도 동일하게 맞는다.
    out = sh([sys.executable, os.path.join(REPO, "ci", "check_conventions.py")])
    m = re.search(r"총 (\d+)건 위반", out)
    total = int(m.group(1)) if m else 0
    by_code = dict(re.findall(r"── (C\d) \((\d+)건\)", out))
    return total, by_code


# ── M3. 코드베이스 통계 ───────────────────────────────────────────────────────
def codebase_stats():
    def count(pattern):
        return len(glob.glob(os.path.join(BACKEND, "**", pattern), recursive=True))
    domains = len([d for d in glob.glob(os.path.join(BACKEND, "domains", "*"))
                   if os.path.isdir(d)])
    routers = count("router.py")
    workers = len(glob.glob(os.path.join(BACKEND, "workers", "*_worker.py")))
    tables = 0
    if os.path.exists(SCHEMA):
        tables = len(re.findall(r"CREATE TABLE", open(SCHEMA, encoding="utf-8").read()))
    return domains, routers, workers, tables


# ── M4. 테스트 결과 (pytest junit xml 있으면) ────────────────────────────────
def test_summary():
    xml = os.path.join(REPO, "ci", "pytest-results.xml")
    if not os.path.exists(xml):
        return None
    text = open(xml, encoding="utf-8").read()
    m = re.search(r'tests="(\d+)".*?failures="(\d+)".*?errors="(\d+)"', text)
    if not m:
        return None
    total, fail, err = map(int, m.groups())
    return total, fail, err


def build_report():
    files, added, removed = diff_stats()
    vtotal, vby = convention_violations()
    domains, routers, workers, tables = codebase_stats()
    tests = test_summary()

    lines = []
    lines.append("## 📊 KIRA CI 리포트")
    lines.append("")
    lines.append("### 변경 규모")
    lines.append(f"- 변경 파일: **{files}개**  (+{added} / -{removed} 라인)")
    lines.append("")
    lines.append("### 정합성 체크 (코드리뷰 자동화)")
    if vtotal == 0:
        lines.append("- ✅ 위반 0건")
    else:
        detail = ", ".join(f"{k} {v}건" for k, v in sorted(vby.items()))
        lines.append(f"- ⚠️ 총 **{vtotal}건** ({detail}) → *경고 모드, 머지 허용*")
        lines.append("  - C1 enum values_callable / C2 utcnow / C3 큐-스키마 / C4 repo commit")
    lines.append("")
    lines.append("### 테스트 (테스트 자동화)")
    if tests is None:
        lines.append("- (스모크 테스트 결과 없음)")
    else:
        total, fail, err = tests
        mark = "✅" if (fail + err) == 0 else "❌"
        lines.append(f"- {mark} {total}개 중 실패 {fail} / 에러 {err}")
    lines.append("")
    lines.append("### 코드베이스 현황")
    lines.append(f"- 도메인 {domains} · 라우터 {routers} · 워커 {workers} · 테이블 {tables}")
    lines.append("")
    lines.append("<sub>경고 모드 — 위 항목은 머지를 차단하지 않습니다. 안정화 후 STRICT 전환 예정.</sub>")
    return "\n".join(lines)


def main():
    report = build_report()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)  # CI가 stdout을 PR 코멘트로 사용 가능


if __name__ == "__main__":
    main()
