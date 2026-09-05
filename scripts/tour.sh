#!/usr/bin/env bash
# 讓模擬器自動走過 App 的各個畫面，並把截圖存下來。
#
#   ./scripts/tour.sh
#   SIM_DEVICE="iPad mini (A17 Pro)" ./scripts/tour.sh
#
# 截圖會放在 build/screens/，檔名就是畫面名稱。
# 改完版面跑這支，不必自己一路點過去確認。
#
# 走的路線寫在 UITests/ScreenTour.swift，要多看幾個畫面就改那裡。

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PATH="$HOME/.local/bin:$PATH"

DEVICE="${SIM_DEVICE:-iPad Pro 11-inch (M5)}"
RESULT="build/tour.xcresult"
OUT="build/screens"

command -v xcodegen >/dev/null 2>&1 && xcodegen generate >/dev/null

rm -rf "$RESULT" "$OUT"

echo "▶ 在「${DEVICE}」上跑一遍…"
xcodebuild test \
    -project Kikitori.xcodeproj \
    -scheme Kikitori \
    -configuration Debug \
    -destination "platform=iOS Simulator,name=$DEVICE" \
    -only-testing:KikitoriUITests/ScreenTour \
    -derivedDataPath build/DerivedData \
    -resultBundlePath "$RESULT" 2>&1 | grep -E "error:|Test Case|TEST SUCCEEDED|TEST FAILED" || true

xcrun xcresulttool export attachments --path "$RESULT" --output-path "$OUT" >/dev/null

# 匯出的檔名是 UUID，換回截圖當初取的名字
python3 - "$OUT" <<'PY'
import json, os, re, sys
out = sys.argv[1]
manifest = os.path.join(out, "manifest.json")
if not os.path.exists(manifest):
    print("  ! 沒有截圖，測試可能沒跑起來")
    raise SystemExit(1)

for entry in json.load(open(manifest)):
    for item in entry.get("attachments", []):
        src = os.path.join(out, item["exportedFileName"])
        name = item.get("suggestedHumanReadableName") or item["exportedFileName"]
        clean = re.sub(r"_\d+_[0-9A-F-]{36}", "", name)
        if os.path.exists(src):
            os.rename(src, os.path.join(out, clean))
            print("  ✓ " + clean)
PY

echo
echo "截圖在 $OUT/"
