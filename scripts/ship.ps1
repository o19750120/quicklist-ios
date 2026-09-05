#Requires -Version 7
<#
.SYNOPSIS
    本機檢查 -> commit -> push -> 等雲端建置 -> 給你 iPad 下載連結。
.EXAMPLE
    ./scripts/ship.ps1 "把按鈕改成紅色"
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0, ValueFromRemainingArguments = $true)]
    [string[]]$Message
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

function Write-Step($text) { Write-Host "`n▶ $text" -ForegroundColor Cyan }

Write-Step '本機檢查'
python scripts/check.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "`n檢查沒過，先修好上面的問題再送出。" -ForegroundColor Red
    exit 1
}

Write-Step '提交變更'
if (-not (git status --porcelain)) {
    Write-Host '沒有任何變更可以送出。' -ForegroundColor Yellow
    exit 0
}
git add -A
git commit -m ($Message -join ' ')

Write-Step '推送到 GitHub'
git push origin main

Write-Step '等待雲端 macOS 建置（大約 3～6 分鐘）'
Start-Sleep -Seconds 8
$runId = (gh run list --limit 1 --json databaseId --jq '.[0].databaseId')
gh run watch $runId --exit-status --interval 20
$buildOk = $LASTEXITCODE -eq 0

if ($buildOk) {
    $tag = (gh release list --limit 1 --json tagName --jq '.[0].tagName')
    $url = (gh release view $tag --json url --jq '.url')
    Write-Host "`n✅ 建置完成：$tag" -ForegroundColor Green
    Write-Host "   在 iPad 用 Safari 打開這個網址，下載 .ipa 後選「用 SideStore 開啟」：" -ForegroundColor Green
    Write-Host "   $url" -ForegroundColor White
} else {
    Write-Host "`n❌ 建置失敗，看這裡的記錄：" -ForegroundColor Red
    Write-Host "   https://github.com/o19750120/quicklist-ios/actions/runs/$runId" -ForegroundColor White
    Write-Host "   （Discord 也會收到錯誤摘要）" -ForegroundColor DarkGray
    exit 1
}
