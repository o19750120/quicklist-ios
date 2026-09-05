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
| `.github/workflows/ios-build.yml` | 自動編譯 + 打包 `.ipa` + 發佈 Release |

`.xcodeproj` 不進版控，每次在雲端由 `xcodegen` 重新產生 —— 這正是不需要 Mac 的關鍵。

## 日常開發流程

```bash
# 1. 改程式（Sources/ 底下的 .swift）
# 2. 送上去
git add -A
git commit -m "改了什麼"
git push
```

push 完約 3～6 分鐘，到 repo 的 **Releases** 頁面會出現新版 `.ipa`。
在 iPad 用 Safari 打開該頁面 → 下載 `.ipa` → 選「用 SideStore 開啟」→ 裝好。

想手動觸發建置：GitHub repo → Actions → Build unsigned IPA → Run workflow。

## iPad 端首次設定（只需做一次）

細節以官方文件為準：<https://docs.sidestore.io>

1. **安裝 SideStore 本體**
   Windows 裝 Apple 官網版 iTunes + iCloud（不要 Microsoft Store 版），
   用 AltServer / SideServer 把 `SideStore.ipa` 側載進 iPad。
2. **產生配對檔（pairing file）**
   用 SideStore 提供的 `jitterbugpair.exe` 產生 `.mobiledevicepairing`，傳進 iPad 匯入 SideStore。
3. **開啟 loopback VPN**
   App Store 安裝 **StosVPN**（官方現在推薦，取代早期的 WireGuard 設定檔），打開它。
   有這個 App 在，SideStore 才能在 iPad 上自己重新簽名，不用接電腦。
4. **在 SideStore 登入 Apple ID**
   建議另開一個免費 Apple ID 專用，不要用主帳號。

設定完成後，就是「Safari 下載 ipa → SideStore 開啟 → 裝好」這麼簡單。

## 免費 Apple ID 的限制（不是本專案造成的，是 Apple 的規則）

- **7 天到期**：憑證 7 天失效，App 會打不開。SideStore 會在背景自動續簽，只要 iPad 有網路 + StosVPN 開著。
- **同時最多 3 個側載 App**
- **每 7 天最多 10 個新的 Bundle ID**
- 沒有推播通知、沒有 iCloud、沒有 App 群組等付費帳號才有的能力

願意付 Apple Developer Program（US$99/年）就沒有 7 天限制，可簽 1 年。

## 常見狀況

**建置失敗？** GitHub → Actions → 點紅色那次 → 展開 `Build (no code signing)` 看錯誤訊息。
Swift 編譯錯誤都會出現在這裡。

**iPad 裝不起來？** 先確認 SideStore 能不能自己重新簽名既有 App；
不行的話通常是 StosVPN 沒開，或配對檔過期需要重新產生。

**建置時間額度**：macOS runner 在 private repo 會用掉 10 倍分鐘數。
這個 repo 設為 public，建置分鐘數不計費。
