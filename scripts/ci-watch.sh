#!/usr/bin/env bash
# 盯著這次 push 觸發的 CI，失敗就把錯誤摘要留下來並跳通知。
#
# 由 .claude/settings.json 的 hook 在每次 git push 之後自動叫起來，
# 也可以自己跑：./scripts/ci-watch.sh
#
# 成功的話安靜結束 —— CI 本身已經會發 Discord，不需要再吵一次。
# 失敗才寫 build/ci-last-failure.log 並跳 macOS 通知。

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PATH="$HOME/.local/bin:$PATH"

WORKFLOW="ios-build.yml"
LOG="build/ci-last-failure.log"

command -v gh >/dev/null 2>&1 || exit 0
gh auth status >/dev/null 2>&1 || exit 0

SHA="$(git rev-parse HEAD 2>/dev/null)" || exit 0

notify() {
    osascript -e "display notification \"$1\" with title \"Kikitori CI\"" 2>/dev/null || true
}

# push 完 run 不會立刻出現，等它冒出來（最多 90 秒）
RUN_ID=""
for _ in $(seq 1 18); do
    RUN_ID="$(gh run list --workflow="$WORKFLOW" --limit 5 \
        --json databaseId,headSha \
        -q "[.[] | select(.headSha == \"$SHA\")][0].databaseId" 2>/dev/null)"
    [ -n "$RUN_ID" ] && [ "$RUN_ID" != "null" ] && break
    sleep 5
done

[ -n "$RUN_ID" ] && [ "$RUN_ID" != "null" ] || exit 0

gh run watch "$RUN_ID" --exit-status --interval 20 >/dev/null 2>&1
STATUS=$?

[ "$STATUS" -eq 0 ] && exit 0

mkdir -p "$(dirname "$LOG")"
{
    echo "CI 失敗"
    echo "commit  $SHA"
    echo "run     $(gh run view "$RUN_ID" --json url -q .url 2>/dev/null)"
    echo "時間    $(date '+%Y-%m-%d %H:%M:%S')"
    echo
    echo "── 失敗的步驟 ──"
    gh run view "$RUN_ID" --json jobs \
        -q '.jobs[] | select(.conclusion == "failure") | "  \(.name): " + ([.steps[] | select(.conclusion == "failure") | .name] | join(", "))' \
        2>/dev/null
    echo
    echo "── log 裡的 error / warning ──"
    gh run view "$RUN_ID" --log-failed 2>/dev/null \
        | grep -E "(error|warning):" | head -40
    echo
    echo "── log 尾巴 ──"
    gh run view "$RUN_ID" --log-failed 2>/dev/null | tail -60
} > "$LOG"

notify "建置失敗，摘要在 $LOG"
