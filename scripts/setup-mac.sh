#!/usr/bin/env bash
# 在 Mac 上把這個專案準備到可以按下 Xcode 執行鍵的狀態。
#
#   ./scripts/setup-mac.sh
#
# 重複執行是安全的。

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

step() { printf '\n\033[36m▶ %s\033[0m\n' "$1"; }
ok()   { printf '  ✓ %s\n' "$1"; }
warn() { printf '  ! %s\n' "$1"; }

step "檢查工具"

if ! xcode-select -p >/dev/null 2>&1; then
    warn "還沒裝 Xcode 命令列工具，執行：xcode-select --install"
    exit 1
fi
ok "Xcode $(xcodebuild -version | head -1 | cut -d' ' -f2)"

if ! command -v brew >/dev/null 2>&1; then
    warn "沒有 Homebrew。先裝它：https://brew.sh"
    exit 1
fi

if ! command -v xcodegen >/dev/null 2>&1; then
    echo "  安裝 xcodegen…"
    brew install xcodegen
fi
ok "xcodegen $(xcodegen --version 2>&1 | tail -1)"

step "開發設定"

if [ -f .env.local ]; then
    ok ".env.local 已就位"
else
    cat <<'MISSING'
  ! 找不到 .env.local

    金鑰不放在任何雲端服務上（包括 gist、雲端硬碟、聊天軟體），
    請自己從 Windows 那台複製過來：

        Windows 端： C:\Me\IOS APP\.env.local
        複製到    ： 這個專案根目錄

    用隨身碟或你自己的加密管道傳，傳完把中間的副本刪掉。
    範本可以參考 .env.local.example。

MISSING
    exit 1
fi

step "注入金鑰到 BuildSecrets.swift"
./scripts/dev-secrets.sh inject

step "產生 Xcode 專案"
xcodegen generate
ok "Kikitori.xcodeproj 已產生"

cat <<'DONE'

準備完成。接下來：

  open Kikitori.xcodeproj

左上角選一台 iPad 模擬器，按 ⌘R。

兩件要記得的事：
  1. 提交前執行 ./scripts/dev-secrets.sh clean
     （BuildSecrets.swift 在版控裡，填了金鑰不能提交；check.py 也會擋）
  2. 模擬器沒有 Spotify App，但連動走的是 Web API，
     你在手機或網頁版播放，模擬器一樣讀得到

DONE
