# 開發環境

這個專案同時在兩台機器上開發：Windows 寫程式、雲端編譯、實機測試；
Mac 則能直接跑模擬器，改完幾秒就看到結果。

兩邊共用同一個 git repo，切換時 `git pull` 就好。

---

## Mac 首次設定（一次就好，約 5 分鐘）

```bash
git clone https://github.com/o19750120/quicklist-ios.git kikitori
cd kikitori
./scripts/setup-mac.sh
```

腳本會裝 xcodegen、從私密 gist 取得金鑰、產生 `.xcodeproj`。完成後：

```bash
open Kikitori.xcodeproj
```

左上角選一台 iPad 模擬器，按 **⌘R**。

### 前置需求

| 需要 | 怎麼裝 |
|---|---|
| Xcode | App Store |
| 命令列工具 | `xcode-select --install` |
| Homebrew | <https://brew.sh> |
| GitHub CLI 並登入 | `brew install gh && gh auth login` |

---

## Mac 上的日常開發

```bash
git pull                  # 接手 Windows 那邊的進度
xcodegen generate         # 只有改過 project.yml 或新增檔案時才需要
open Kikitori.xcodeproj   # 或直接在 Xcode 裡 ⌘R
```

**新增 Swift 檔案不用手動加進專案** —— XcodeGen 會掃 `Sources/`，
重跑 `xcodegen generate` 就好。這也是為什麼 `.xcodeproj` 不進版控。

### 在 Mac 上最值得做的事

模擬器有的、iPad 匯入沒有的：

- **改完 3 秒看到結果**，不用等 CI 也不用接線匯入
- **看得到 console**，`print` 和錯誤訊息直接出現在 Xcode 底下
- **SwiftUI 預覽**：`ContentView` 那類檔案按 ⌥⌘P 就能即時預覽
- **中斷點**除錯，能一行一行看變數

Spotify 連動在模擬器上一樣能用 —— 它走的是 Web API，
只要你在別的地方（手機、Spotify 網頁版）播放，模擬器就讀得到。

### 提交前一定要做

```bash
./scripts/dev-secrets.sh clean
```

本機開發時 `BuildSecrets.swift` 會被填入金鑰，這個檔案在版控裡，
**填了值不能提交**。`scripts/check.py` 會擋，但自己記得比較保險。

要繼續開發時再 `./scripts/dev-secrets.sh inject` 填回去。

---

## Windows 上的日常開發

```powershell
git pull
# 改程式
./scripts/ship.ps1 "改了什麼"     # 檢查 → commit → push → 等 CI → 給下載連結
./scripts/get-ipa.ps1             # 抓最新 ipa，然後用 iloader 匯入 iPad
```

Windows 上沒有 Swift 編譯器，語法錯誤要等 CI 才知道。
`python scripts/check.py` 能先擋掉 YAML / plist / 資源檔 / 括號不對稱這類低級錯誤。

---

## 後端（兩台都能跑）

```bash
set -a; source .env.local; set +a          # Mac / Git Bash
python backend/status.py                    # 看全局狀況
python backend/transcribe.py --preview --show "節目名" --episode "集名"
```

`backend/status.py` 一次列出：轉錄任務、逐字稿存量、
**App 從 iPad 回報的執行紀錄**、憑證推估剩餘天數、CI 狀態。

---

## 兩台機器怎麼協作

沒有什麼特別機制，就是 git。要注意的只有兩件事：

1. **換機器前先 push**，不然另一台 pull 不到
2. **`.xcodeproj` 和 `.env.local` 都不進版控**，兩邊各自產生

如果兩邊都改了同一個檔案造成衝突，`git status` 會告訴你是哪些。

---

## 檔案結構

| 路徑 | 用途 |
|---|---|
| `Sources/` | SwiftUI 程式碼（Models / Services / ViewModels / Views） |
| `Resources/` | Info.plist、App 圖示、色票 |
| `backend/` | 轉錄管線與維運工具（Python） |
| `scripts/` | 兩邊的開發腳本 |
| `project.yml` | XcodeGen 設定，取代手動維護的 .xcodeproj |
| `TODO.md` | 開發清單與已知限制 |
