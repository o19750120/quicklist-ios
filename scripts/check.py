"""push 前的本機檢查：能在 Windows 上驗證的東西先驗證，省下 CI 來回時間。

檢查項目：
  1. workflow YAML 語法
  2. project.yml 語法，以及裡面列到的路徑是否存在
  3. Info.plist 是否為合法 plist
  4. Assets.xcassets 的 Contents.json 是否為合法 JSON、圖示檔是否存在
  5. Swift 檔的括號 / 引號是否平衡（粗略，真正的編譯錯誤只有 CI 抓得到）
"""
import json
import plistlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
errors = []
notes = []


def check_yaml():
    try:
        import yaml
    except ImportError:
        notes.append("沒裝 pyyaml，跳過 YAML 檢查（pip install pyyaml 可啟用）")
        return
    for path in list(ROOT.glob(".github/workflows/*.yml")) + [ROOT / "project.yml"]:
        try:
            yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"{path.relative_to(ROOT)}: YAML 語法錯誤 -> {exc}")


def check_project_paths():
    try:
        import yaml
    except ImportError:
        return
    spec = yaml.safe_load((ROOT / "project.yml").read_text(encoding="utf-8"))
    for name, target in (spec.get("targets") or {}).items():
        for source in target.get("sources", []):
            rel = source["path"] if isinstance(source, dict) else source
            if not (ROOT / rel).exists():
                errors.append(f"project.yml target {name}: 找不到 sources 路徑 {rel}")
        plist = (target.get("settings", {}).get("base", {}) or {}).get("INFOPLIST_FILE")
        if plist and not (ROOT / plist).exists():
            errors.append(f"project.yml target {name}: 找不到 INFOPLIST_FILE {plist}")


def check_plist():
    path = ROOT / "Resources/Info.plist"
    if not path.exists():
        errors.append("找不到 Resources/Info.plist")
        return
    try:
        with path.open("rb") as handle:
            plistlib.load(handle)
    except Exception as exc:
        errors.append(f"Info.plist 格式錯誤 -> {exc}")


def check_assets():
    catalog = ROOT / "Resources/Assets.xcassets"
    if not catalog.exists():
        errors.append("找不到 Resources/Assets.xcassets")
        return
    for contents in catalog.rglob("Contents.json"):
        try:
            data = json.loads(contents.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"{contents.relative_to(ROOT)}: JSON 錯誤 -> {exc}")
            continue
        for image in data.get("images", []):
            filename = image.get("filename")
            if filename and not (contents.parent / filename).exists():
                errors.append(f"{contents.relative_to(ROOT)}: 少了圖檔 {filename}")


def check_no_leaked_secrets():
    """本機開發會把金鑰填進 BuildSecrets.swift，那個檔案在版控裡，填了值不能提交。"""
    path = ROOT / "Sources/Generated/BuildSecrets.swift"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    filled = [
        line.split("static let ")[1].split(" =")[0]
        for line in text.splitlines()
        if "static let " in line and '= ""' not in line
    ]
    if filled:
        errors.append(
            "BuildSecrets.swift 有填入的金鑰（"
            + ", ".join(filled)
            + "），提交前請執行 ./scripts/dev-secrets.sh clean"
        )


def check_swift():
    swift_files = list((ROOT / "Sources").rglob("*.swift"))
    if not swift_files:
        errors.append("Sources/ 裡沒有任何 .swift 檔")
        return
    for path in swift_files:
        text = path.read_text(encoding="utf-8")
        stripped = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("//")
        )
        for opener, closer in [("{", "}"), ("(", ")"), ("[", "]")]:
            if stripped.count(opener) != stripped.count(closer):
                errors.append(
                    f"{path.relative_to(ROOT)}: {opener}{closer} 數量不對 "
                    f"({stripped.count(opener)} vs {stripped.count(closer)})"
                )
        if stripped.count('"') % 2 != 0:
            notes.append(f'{path.relative_to(ROOT)}: 雙引號是奇數個，注意是否漏了一個')
    notes.append(f"檢查了 {len(swift_files)} 個 Swift 檔")


for check in (check_yaml, check_project_paths, check_plist, check_assets,
              check_no_leaked_secrets, check_swift):
    check()

for note in notes:
    print(f"  · {note}")

if errors:
    print("\n本機檢查沒過：")
    for error in errors:
        print(f"  ✗ {error}")
    sys.exit(1)

print("\n本機檢查通過（真正的 Swift 編譯錯誤仍以 CI 為準）")
