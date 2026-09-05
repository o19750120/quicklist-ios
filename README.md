# QuickList — 在 Windows 上開發、在 iPad 上執行的 iOS App

沒有 Mac 也能寫 iOS App。程式在 Windows 上寫，編譯交給 GitHub 的雲端 macOS 機器，
成品直接下載到 iPad 安裝。

```
Windows (寫程式)  →  git push  →  GitHub Actions (macOS runner 編譯)
                                        ↓
                              GitHub Releases (.ipa)
                                        ↓
                          iPad Safari 下載 → SideStore 安裝
```

## 專案結構

| 路徑 | 用途 |
|---|---|
| `project.yml` | XcodeGen 設定檔，取代手動維護的 `.xcodeproj` |
| `Sources/` | SwiftUI 程式碼 |
| `Resources/` | `Info.plist`、App 圖示、色票 |
| `.github/workflows/ios-build.yml` | 自動編譯 + 打包 `.ipa` + 發佈 Release + Discord 通知 |
| `scripts/check.py` | push 前的本機檢查 |
| `scripts/ship.ps1` | 一鍵：檢查 → commit → push → 等建置 → 給下載連結 |

`.xcodeproj` 不進版控，每次在雲端由 `xcodegen` 重新產生 —— 這正是不需要 Mac 的關鍵。

## 日常開發流程

改完 `Sources/` 底下的 `.swift`，在 PowerShell 執行：

```powershell
./scripts/ship.ps1 "改了什麼"
```

這個腳本會依序做：本機檢查 → commit → push → 等雲端建置 → 印出 iPad 的下載連結。

想手動一步步來也可以：

```bash
python scripts/check.py     # 先在 Windows 上抓得到的錯誤先抓出來
git add -A && git commit -m "改了什麼" && git push
```

push 完約 3～6 分鐘，repo 的 **Releases** 頁面會出現新版 `.ipa`。
在 iPad 用 Safari 打開該頁面 → 下載 `.ipa` → 選「用 SideStore 開啟」→ 裝好。

想手動觸發建置：GitHub repo → Actions → Build unsigned IPA → Run workflow。

## 出問題的時候怎麼看

Windows 上沒有 Swift 編譯器，真正的編譯錯誤只有雲端看得到。所以錯誤訊息有三個出口：

1. **Discord**：每次建置結束都會推播。成功給下載連結，失敗給 `error:` 摘要 + 記錄連結。
   （webhook 存在 GitHub Secret `DISCORD_WEBHOOK`，沒有寫進這個公開 repo。）
2. **GitHub Actions 頁面**：Actions → 點那次建置。每個階段都有摺疊分組，
   失敗時「抽出編譯錯誤摘要」那步會直接把 `error:` 行列出來。
3. **build.log**：完整編譯輸出當成 artifact 上傳，保留 30 天，可以整包下載回來看。

`python scripts/check.py` 會在 push 前先驗 YAML / plist / 資源檔 / Swift 括號平衡，
擋掉大部分低級錯誤，不用浪費一輪 3 分鐘的雲端建置。

## 第一次設定（只需做一次）

官方文件：<https://docs.sidestore.io/docs/installation/install>
（注意：網路上大量教學仍在教 AltServer + jitterbugpair，那套官方已標為過時；
StosVPN 也已從 App Store 下架，現在用 LocalDevVPN。）

### 電腦端

1. **Apple 官網版 iTunes**（不要 Microsoft Store 版）
   `winget install --id Apple.iTunes -e`
   真正的目的是那組 USB 驅動；iTunes 本身不會用到。
   驗證裝好沒：`Get-Service | Where-Object Name -match 'Apple'`，
   要看到 `Apple Mobile Device Service`，沒有的話 iloader 會認不到 iPad。
2. **iloader**：`winget install --id nabdev.iloader -e`
   官方來源只有 <https://github.com/nab138/iloader>。它會自己處理配對檔。
3. iPad 用 USB 接電腦，iPad 上點「信任」並輸入密碼。
4. 開 iloader → 登入 Apple 帳號（**大小寫有差**）→ 選你的 iPad → 按 **Install SideStore (Stable)**。

### iPad 端

5. App Store 安裝 **LocalDevVPN**，打開按 Connect。
   要安裝、更新、續簽 SideStore 裡的 App 時，這個都必須是連線狀態。
6. 設定 → 一般 → VPN 與裝置管理 → 「開發者 App」下點你的 Apple 帳號 → 信任。
   （iPadOS 18 / 26 選「允許並重新啟動」並輸入密碼）
7. 設定 → 隱私權與安全性 → 開啟「開發者模式」（會重開機）。
8. 打開 SideStore，用**同一個** Apple 帳號登入。
9. My Apps → 點 SideStore 旁的「7 DAYS」計數器手動刷新一次，設定完成。

建議另開一個免費 Apple ID 專用，不要用主帳號。

設定完成後就是全無線：Safari 下載 `.ipa` → 用 SideStore 開啟 → 裝好。

## 免費 Apple ID 的限制（不是本專案造成的，是 Apple 的規則）

- **7 天到期**：憑證 7 天失效，App 會打不開。SideStore 會在背景自動續簽，只要 iPad 有網路 + StosVPN 開著。
- **同時最多 3 個側載 App**
- **每 7 天最多 10 個新的 Bundle ID**
- 沒有推播通知、沒有 iCloud、沒有 App 群組等付費帳號才有的能力

願意付 Apple Developer Program（US$99/年）就沒有 7 天限制，可簽 1 年。

## 常見狀況

**建置失敗？** GitHub → Actions → 點紅色那次 → 展開 `Build (no code signing)` 看錯誤訊息。
Swift 編譯錯誤都會出現在這裡。

**iPad 裝不起來？** 九成是 LocalDevVPN 沒連線。先確認它是連線狀態，
再看 SideStore 能不能自己重新簽名既有 App。都不行的話用 iloader 重跑一次安裝。

**建置時間額度**：macOS runner 在 private repo 會用掉 10 倍分鐘數。
這個 repo 設為 public，建置分鐘數不計費。
