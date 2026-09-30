param(
    [string]$NotionParent = "",
    [switch]$SkipInstall
)
$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ProjectPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
Push-Location $ProjectRoot
try {
    if (-not (Test-Path -LiteralPath $ProjectPython)) {
        & py -3.12 -m venv (Join-Path $ProjectRoot ".venv")
        if ($LASTEXITCODE -ne 0) { throw "Python 3.12 가상환경 생성 실패" }
    }
    if (-not $SkipInstall) {
        & $ProjectPython -m pip install -e ".[dev]" -c requirements.lock
        if ($LASTEXITCODE -ne 0) { throw "의존성 설치 실패" }
    }
    & $ProjectPython scripts/configure_codex.py
    if ($LASTEXITCODE -ne 0) { throw "Codex 프로젝트 구성 실패" }
    if ($NotionParent) {
        & $ProjectPython -m cs_study_mcp configure --notion-parent $NotionParent
        if ($LASTEXITCODE -ne 0) { throw "Notion 대상 설정 실패" }
    }
    & $ProjectPython -m cs_study_mcp doctor
    if ($LASTEXITCODE -ne 0) { throw "로컬 구성 검사 실패" }
} finally {
    Pop-Location
}
