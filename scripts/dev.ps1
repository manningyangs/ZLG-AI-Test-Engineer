# 开发环境启动（桌面应用）
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
& ".\.venv\Scripts\zlg-ai-test-engineer.exe"
exit $LASTEXITCODE
