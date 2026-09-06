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

腳本會裝 xcodegen、把 `.env.local` 的金鑰注入 `BuildSecrets.swift`、
產生 `.xcodeproj`。金鑰要自己從另一台搬過來（不放任何雲端），
範本見 `.env.local.example`。完成後：

```bash
open Kikitori.xcodeproj
```

左上角選一台 iPad 模擬器，按 **⌘R**。

### 前置需求

| 需要 | 怎麼裝 |
|---|---|
| Xcode | App Store（約 15 GB，**只裝命令列工具不夠**，模擬器要完整 Xcode） |
| iOS 模擬器 runtime | `xcodebuild -downloadPlatform iOS`（約 8 GB，Xcode 26 起不內建） |
| 命令列工具 | `xcode-select --install` |
| Homebrew | <https://brew.sh> |
| GitHub CLI 並登入 | `brew install gh && gh auth login` |

裝完 Xcode 後要先同意授權，否則 `simctl` 一律報錯：

```bash
sudo xcodebuild -license accept
sudo xcodebuild -runFirstLaunch
```

### Mac 上的三個坑

**1. Homebrew 裝不動**（`/usr/local/share/*` 不屬於你，多半是另一個帳號裝的 brew）

不必動系統權限，官方 binary 直接放家目錄就好：

```bash
mkdir -p ~/.local/bin ~/.local/share
curl -L -o /tmp/x.zip https://github.com/yonaskolb/XcodeGen/releases/latest/download/xcodegen.zip
cd /tmp && unzip -oq x.zip
cp xcodegen/bin/xcodegen ~/.local/bin/ && cp -R xcodegen/share/xcodegen ~/.local/share/
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
```

`gh` 同理，從 <https://github.com/cli/cli/releases> 抓 `macOS_arm64.zip`。

**2. `backend/*.py` 報 SSL CERTIFICATE_VERIFY_FAILED**

python.org 版的 Python 沒有系統根憑證。裝 certifi 並指過去：

```bash
python3 -m pip install --user certifi
echo 'export SSL_CERT_FILE="$HOME/Library/Python/3.11/lib/python/site-packages/certifi/cacert.pem"' >> ~/.zshrc
```

**3. 每次重裝到模擬器都要重新登入 Spotify**

`simctl install` 覆蓋安裝時，iOS 會把它當成重新安裝，連帶清掉 App 的
Keychain 項目，refresh token 就沒了。UserDefaults（書庫、對齊偏移）不受影響。
實機用 iloader 覆蓋安裝不會這樣，所以只有模擬器要忍這個。

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
- **自動走過所有畫面並截圖**（見下）

```bash
./scripts/tour.sh
```

模擬器會自己從主畫面點進書庫、再點進某一集的閱讀畫面，
每一步都截圖存到 `build/screens/`。改完版面跑這支就好，
不必自己一路點過去確認。

路線寫在 `UITests/ScreenTour.swift`，要多看幾個畫面就改那裡。
定位元件時記得用 `accessibilityIdentifier`，
用 `element(boundBy:)` 按順序猜會抓到工具列的按鈕。

這個 UI 測試 target 只在 Mac 上跑，CI 只做 `build` 不做 `test`，不受影響。

**在逐字稿畫面上不要搜尋也不要遍歷元素。** 一句話拆成一個個可點的詞之後，
畫面上有七百多個 accessibility 元素，而 XCUITest 每查一個都要跟 App 來回通訊：
`allElementsBoundByAccessibilityElement` 會直接跑到超時，改用
`element(boundBy:)` 逐個取也一樣（兩種寫法都試過，都超過十分鐘沒結束）。
要驗證那個畫面上的互動，只能用 `accessibilityIdentifier` 指名單一元素。

還有一個相關的雷：`.accessibilityIdentifier` 加在自訂 `Layout`（例如
`WordFlowLayout`）裡面的 `Text` 上似乎不會生效，用 identifier 找不到那些詞。
原因還沒查清楚，先用 `app.scrollViews` 之類的範圍限制繞過。

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

底層還是 git，但兩台上的 Claude session 可以直接對話，
所以協調不必透過你轉述。

**兩台的 Claude 互相傳訊息**

兩邊都連上 Remote Control 之後，`/list-agents`（別名 `/peers`）
就看得到對方，直接說「跟 @對方的名字 說 ⋯」即可。設定方式：

```json
// ~/.claude/settings.json（Windows 是 %USERPROFILE%\.claude\settings.json）
{
  "crossSessionInbound": "accept",
  "remoteControlAtStartup": true
}
```

`crossSessionInbound` 不設的話，bypass permissions 模式的 session
會把收到的訊息扣住等人按核准，五分鐘沒回應就丟掉。
用 `claude --name <名字>` 或 `/rename <名字>` 給 session 取個好記的名字。

原生 Windows 需要 Claude Code 2.1.234 以上，macOS / WSL 是 2.1.224 以上。
注意 **WSL 裡的 session 和原生 Windows 的 session 互相看不到**
（註冊在不同的家目錄、用不同的 socket 型別）。

**避免兩邊改到打架**

1. **換機器前先 push**，開工前先 `git pull`
2. **`.xcodeproj` 和 `.env.local` 都不進版控**，兩邊各自產生
3. **預設分工**：Swift 在 Mac 做（有模擬器），`backend/` 與 CI 兩邊都可能動，
   動共用檔案前先用 SendMessage 知會一聲
4. **真的撞到**：後推的那台放棄自己的版本、pull 對方的，再把自己獨有的補回去。
   同一個檔案的兩套改法不要硬 merge。

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
