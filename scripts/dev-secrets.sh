#!/usr/bin/env bash
# 在本機開發時把金鑰注入 BuildSecrets.swift，提交前再清掉。
#
#   ./scripts/dev-secrets.sh inject   # 從 .env.local 讀值填進去
#   ./scripts/dev-secrets.sh clean    # 還原成空字串
#   ./scripts/dev-secrets.sh status   # 看目前狀態
#
# CI 上是另一條路（直接從 GitHub Secrets 注入），這支只給本機用。

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="$ROOT/Sources/Generated/BuildSecrets.swift"
ENV_FILE="$ROOT/.env.local"

write_value() {
    local name="$1" value="$2"
    python3 - "$TARGET" "$name" "$value" <<'PY'
import io, re, sys
target, name, value = sys.argv[1], sys.argv[2], sys.argv[3]
source = io.open(target, encoding="utf-8").read()
source = re.sub(
    rf'(static let {name} = )"[^"]*"',
    lambda m: m.group(1) + '"' + value + '"',
    source,
)
io.open(target, "w", encoding="utf-8").write(source)
PY
}

case "${1:-status}" in
    inject)
        if [ ! -f "$ENV_FILE" ]; then
            echo "找不到 .env.local，先跑 ./scripts/setup-mac.sh"
            exit 1
        fi
        # shellcheck disable=SC1090
        set -a; source "$ENV_FILE"; set +a

        write_value spotifyClientID "${SPOTIFY_CLIENT_ID:-}"
        write_value supabaseURL     "${SUPABASE_URL:-}"
        write_value supabaseAnonKey "${SUPABASE_ANON_KEY:-}"
        echo "  ✓ 已注入（提交前記得執行 clean）"
        ;;

    clean)
        write_value spotifyClientID ""
        write_value supabaseURL     ""
        write_value supabaseAnonKey ""
        echo "  ✓ 已清空，可以安全提交"
        ;;

    status)
        if grep -q 'spotifyClientID = ""' "$TARGET"; then
            echo "  BuildSecrets 目前是空的（可安全提交）"
        else
            echo "  BuildSecrets 目前有值（不要提交，先跑 clean）"
        fi
        ;;

    *)
        echo "用法：$0 [inject|clean|status]"
        exit 1
        ;;
esac
