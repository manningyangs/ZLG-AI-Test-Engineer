# 运行全部自动化测试
param(
    [string]$Path = "tests",
    [string]$ExtraArgs = ""
)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
& ".\.venv\Scripts\python.exe" -m pytest $Path -q @ExtraArgs
exit $LASTEXITCODE
