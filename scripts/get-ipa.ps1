#Requires -Version 7
<#
.SYNOPSIS
    把 GitHub Releases 上最新的 .ipa 抓到 dist\，準備用 iloader 匯入安裝。
.DESCRIPTION
    SideStore 的無線續簽壞掉期間（Apple 擋舊 User-Agent），
    改用 iloader「匯入 IPA」安裝，每 7 天要重跑一次。
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$dist = Join-Path $repoRoot 'dist'
New-Item -ItemType Directory -Force -Path $dist | Out-Null

$tag = gh release list --limit 1 --json tagName --jq '.[0].tagName'
Write-Host "最新版本: $tag" -ForegroundColor Cyan

gh release download $tag --pattern '*.ipa' -D $dist --clobber

$ipa = Get-ChildItem $dist -Filter '*.ipa' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Write-Host "`n已下載: $($ipa.FullName)" -ForegroundColor Green
Write-Host "大小: $([math]::Round($ipa.Length/1KB,1)) KB"
Write-Host "`n接下來：iPad 接上電腦 -> 開 iloader -> 匯入 IPA -> 選上面這個檔案" -ForegroundColor Yellow

$iloader = "$env:LOCALAPPDATA\iloader\iloader.exe"
if (Test-Path $iloader) {
    Write-Host "`n要現在開啟 iloader 嗎？(Y/N) " -NoNewline
    if ((Read-Host) -match '^[Yy]') { Start-Process $iloader }
}
