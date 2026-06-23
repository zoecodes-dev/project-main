<#
ci/verify.ps1 — KIRA '하루 끝 풀 게이트' 로컬 러너 (Windows / PowerShell 5.1+)

목적: 퇴근 전 한 명령으로 "오늘 내가 깬 거 없나"를 끝까지 확인한다.
      GitHub Actions(ci.yml)와 '같은 ci/ 스크립트'를 로컬에서 먼저 돌려, 로컬 초록이
      CI 초록을 예측하게 만든다.

단계:
  1) 정적 게이트   : check_conventions.py + ruff (경고 모드, 비차단)
  2) 오늘 변경     : git diff 요약 + 오늘 추가된 라우트 체크리스트(@router)
  3) 스택          : docker compose down -v && up --build  (스키마 신선화 = 규칙 #8)
  4) 스모크 + e2e  : pytest ci/test_smoke.py ci/test_e2e.py  ← 실패하면 이 게이트가 FAIL
  5) 메트릭 리포트 : metrics_report.py (변경 규모 + 정합성 + 테스트 + 코드베이스 통계)

사용:
  .\ci\verify.ps1             # 풀 게이트(기본)
  .\ci\verify.ps1 -Fast       # 정적 검사 + 변경 요약만 (스택 안 띄움, 초 단위)
  .\ci\verify.ps1 -NoRebuild  # 이미 떠있는 스택에 smoke+e2e만 (재빌드 생략)

환경변수:
  BASE_REF  diff 비교 기준 (기본 origin/develop)
#>
[CmdletBinding()]
param(
    [switch]$Fast,
    [switch]$NoRebuild
)

# 레포 루트로 이동 (이 스크립트는 ci/ 아래에 있음)
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

if ($env:BASE_REF) { $BaseRef = $env:BASE_REF } else { $BaseRef = "origin/develop" }
$BaseUrl = "http://localhost"
$env:BASE_URL = $BaseUrl

$failures = @()
function Section($title) { Write-Host ""; Write-Host "=== $title ===" -ForegroundColor Cyan }

# ── 1) 정적 게이트 ───────────────────────────────────────────────────────────
Section "1/5 정적 검사 (conventions + ruff)"
python ci/check_conventions.py
if (Get-Command ruff -ErrorAction SilentlyContinue) {
    ruff check backend
    if ($LASTEXITCODE -ne 0) { Write-Host "ruff 경고 있음 (경고 모드 — 비차단)" -ForegroundColor Yellow }
} else {
    Write-Host "ruff 미설치 — 건너뜀 (pip install ruff 로 활성화)" -ForegroundColor Yellow
}

# ── 2) 오늘 변경 커버리지 체크리스트 ─────────────────────────────────────────
Section "2/5 오늘 변경 (vs $BaseRef)"
git diff --stat "$BaseRef...HEAD"
Write-Host ""
Write-Host "오늘 추가된 라우트(@router) — e2e 커버 대상:" -ForegroundColor Yellow
$addedRoutes = git diff "$BaseRef...HEAD" -- "*router.py" | Select-String '^\+\s*@router'
if ($addedRoutes) { $addedRoutes | ForEach-Object { Write-Host "  $($_.Line.TrimStart('+').Trim())" } }
else { Write-Host "  (없음)" }
Write-Host "  → 위 라우트가 ci/test_e2e.py에 커버됐는지 확인하세요(없으면 함수 한 개 추가)." -ForegroundColor DarkGray

if ($Fast) { Write-Host "`n-Fast: 스택 검증 건너뜀. (정적 게이트만 수행)" -ForegroundColor Yellow; exit 0 }

# ── 3) 스택 기동 ─────────────────────────────────────────────────────────────
Section "3/5 docker compose 스택"
docker compose version 1>$null 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Docker가 실행되고 있지 않습니다 — Docker Desktop을 켜고 다시 시도하세요." -ForegroundColor Red
    exit 2
}
if (-not $NoRebuild) {
    Write-Host "down -v && up --build (스키마/볼륨 신선화)..." -ForegroundColor DarkGray
    docker compose down -v
    docker compose up -d --build
} else {
    Write-Host "-NoRebuild: 기존 스택 재사용." -ForegroundColor Yellow
}

# ── 4) /health 폴링 ──────────────────────────────────────────────────────────
Section "health 폴링 (최대 ~150s)"
$up = $false
for ($i = 0; $i -lt 30; $i++) {
    try {
        $r = Invoke-WebRequest "$BaseUrl/health" -UseBasicParsing -TimeoutSec 5
        if ($r.StatusCode -eq 200) { $up = $true; break }
    } catch { }
    Start-Sleep -Seconds 5
}
if (-not $up) {
    Write-Host "health 응답 없음 — 스택 기동 실패. 최근 로그:" -ForegroundColor Red
    docker compose logs --tail 60
    exit 3
}
Write-Host "스택 up ✅" -ForegroundColor Green

# ── 5) 스모크 + 기능 e2e (진짜 게이트) ───────────────────────────────────────
Section "4/5 스모크 + 기능 e2e (pytest)"
python -m pytest ci/test_smoke.py ci/test_e2e.py -v --junitxml=ci/pytest-results.xml
if ($LASTEXITCODE -ne 0) { $failures += "pytest (smoke/e2e)" }

# ── 6) 메트릭 리포트 ─────────────────────────────────────────────────────────
Section "5/5 메트릭 리포트"
$env:BASE_REF = $BaseRef
python ci/metrics_report.py

# ── 결과 요약 ────────────────────────────────────────────────────────────────
Section "결과"
if ($failures.Count -eq 0) {
    Write-Host "PASS — 오늘 작업 게이트 통과 ✅" -ForegroundColor Green
    exit 0
} else {
    Write-Host ("FAIL — " + ($failures -join ", ")) -ForegroundColor Red
    Write-Host "위 pytest 출력에서 실패 케이스를 확인하세요." -ForegroundColor Red
    exit 1
}
