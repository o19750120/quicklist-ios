# Kikitori

聽 Spotify 上的 podcast 學語言：逐句顯示原文與翻譯，跟著播放進度走。
靈感來自 App Store 的「靈動歌詞」。主力學日文，但架構做成語言無關。

**開發者沒有 iOS 開發經驗**，說明時不要假設他知道 Xcode 慣例。
用繁體中文回覆。

---

## 這個專案怎麼運作

```
Spotify Web API ──「正在播 XX 節目第 12 集，進度 12:34」──→ App
                                                            ↓ 查逐字稿
                                                        Supabase
                                                            ↑ 寫入
GitHub Actions ← Deepgram 轉錄 ← 公開 RSS 音檔 ← iTunes Search API
```

Spotify 不給音檔，所以逐字稿是從該集的**公開 RSS** 反查音檔再轉錄的
（`backend/find_episode.py`）。Spotify 獨家節目因此做不了。

## 目前狀態

看 `TODO.md`。階段 0–3 已完成並在實機驗證過，下一輪要做「書庫」
（聽過的節目列表、上次聽到哪、完成度）。

## 開發環境

`DEVELOPMENT.md` 有完整說明。摘要：

**Mac（有模擬器，改完幾秒看到結果）**
```bash
./scripts/setup-mac.sh     # 首次
open Kikitori.xcodeproj
```

**Windows（沒有 Swift 編譯器，靠雲端 CI）**
```powershell
./scripts/ship.ps1 "改了什麼"   # 檢查 → commit → push → 等 CI
./scripts/get-ipa.ps1           # 抓 ipa，再用 iloader 匯入 iPad
```

`.xcodeproj` 不進版控，由 XcodeGen 從 `project.yml` 產生。
新增 Swift 檔案不用手動加進專案，重跑 `xcodegen generate` 即可。

## 一定要遵守的事

1. **提交前執行 `./scripts/dev-secrets.sh clean`**
   本機開發會把金鑰填進 `Sources/Generated/BuildSecrets.swift`，
   那個檔案在版控裡，填了值不能提交。`scripts/check.py` 會擋。

2. **金鑰不放任何雲端服務**
   不放 gist、雲端硬碟、聊天訊息。這裡犯過一次：
   secret gist 只是不公開列出，會被自動掃描，Groq 與 Google
   幾分鐘內就偵測到並發出撤銷通知。換機器請用實體或加密管道搬 `.env.local`。

3. **Supabase 專案 `jglxgtumcbtquwuuqqep` 是公司專案，絕對不要碰。**
   本專案用的是 `ouwvxdzuvwfzpdozbaby`（trendrace），
   同專案還有使用者原本的 `daily` 表，也不要動。
   所有本專案的表都以 `kikitori_` 開頭。

4. **這個 repo 是 public**（為了 macOS runner 免費額度），
   不要放任何公司相關內容。

## 已經否決的方案，不要再提

- **改做網頁 / PWA** —— 使用者明確要原生 iOS App。
- **App 自己播放音檔取代 Spotify 連動** —— 他要的就是「回到 App 就接上」的體驗。
- **完全自動的時間軸對齊** —— App 沒辦法聽音訊判斷播到哪一句，
  那需要音訊指紋比對。現行做法是用長度差推估，加上使用者長按校正並記住。

## 常用指令

```bash
python backend/status.py            # 一次看完：轉錄任務、逐字稿存量、
                                    # App 從 iPad 回報的紀錄、憑證剩餘天數、CI 狀態
python scripts/check.py             # 提交前的本機檢查
python backend/transcribe.py --preview --show "節目名" --episode "集名"
```

`backend/status.py` 特別有用 —— App 會把錯誤與關鍵事件回報到
`kikitori_logs`，所以裝置上發生什麼事，這裡查得到，不必請使用者截圖。

## 已知限制

- **憑證 7 天到期**：免費 Apple 帳號的限制，到期要接電腦用 iloader 重裝。
  SideStore 的無線續簽目前壞掉（Apple 擋掉舊 User-Agent，AltSign PR #47 未合併）。
- **Spotify 開發者模式**：限 5 個授權使用者且需 Premium，不能公開發布。
- Deepgram 的日文詞切分偶爾會把「日本語」拆成「日」「本語」，
  造成斷句瑕疵。之後可考慮讓 Gemini 依語意重新分句。
