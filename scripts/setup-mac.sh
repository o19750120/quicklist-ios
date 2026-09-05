#!/usr/bin/env bash
# 在 Mac 上把這個專案準備到可以按下 Xcode 執行鍵的狀態。
#
#   ./scripts/setup-mac.sh
#
# 做四件事：裝 xcodegen、拉開發設定、注入金鑰、產生 .xcodeproj。
# 重複執行是安全的。

set -euo pipefail

GIST_ID="086e4e5e018f156212cba1a53852f8c2"
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

if ! command -v gh >/dev/null 2>&1; then
    echo "  安裝 GitHub CLI…"
    brew install gh
fi

if ! gh auth status >/dev/null 2>&1; then
    warn "GitHub CLI 還沒登入，執行：gh auth login"
    exit 1
fi
ok "gh 已登入"

step "取得開發設定"

if [ -f .env.local ]; then
    ok ".env.local 已存在，沿用（要更新就先刪掉它）"
else
    gh gist view "$GIST_ID" --raw > .env.local
    ok "已從私密 gist 取得 .env.local"
fi

step "注入金鑰到 BuildSecrets.swift"
./scripts/dev-secrets.sh inject

step "產生 Xcode 專案"
xcodegen generate
ok "Kikitori.xcodeproj 已產生"

cat <<'EOF'

準備完成。接下來：

  open Kikitori.xcodeproj

在 Xcode 左上角選一台 iPad 模擬器，按 ⌘R 就會跑起來。

要注意的兩件事：
  1. BuildSecrets.swift 現在有金鑰，不要提交。
     提交前執行：./scripts/dev-secrets.sh clean
     （scripts/check.py 也會擋，但自己記得比較好）
  2. 模擬器上沒有 Spotify App，但 Spotify 連動是走 Web API，
     只要模擬器有網路、你在 Spotify 網頁版或手機上播放，一樣讀得到。

EOF
