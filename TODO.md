# Kikitori 開發清單

聽 Spotify 上的 podcast 學語言：逐句顯示原文與翻譯，跟著播放進度走。
主力語言日文，但架構做成語言無關，之後能加其他語言。

**開發環境**：Windows 寫程式 → GitHub Actions 雲端 macOS 編譯 → iloader 匯入 iPad。
因為每次上機測試都得接電腦，所以盡量累積到一整塊功能可測了再匯入。

---

## 階段 0：建置管線 ✅ 已完成

- [x] XcodeGen + GitHub Actions 無簽名編譯，自動打包 `.ipa` 發到 Releases
- [x] Windows 端 Apple USB 驅動（Store 版 iTunes 缺驅動，改從安裝包解出 MSI 單獨安裝）
- [x] iloader 安裝，走通「匯入 IPA → iPad 上跑起來」
- [x] 建置結果推播到 Discord（成功給下載連結，失敗給 error 摘要）
- [x] `scripts/check.py` push 前本機驗證（YAML / plist / 資源檔 / Swift 括號）
- [x] `scripts/ship.ps1`、`scripts/get-ipa.ps1` 一鍵送出與取件

## 階段 1：Spotify 連動 ✅ 程式完成，待實機驗證

- [x] PKCE 授權流程（手機端不需要 client secret）
- [x] Client ID 由 GitHub Secret 在建置時注入，不進公開 repo
- [x] `kikitori://spotify-callback` URL scheme
- [x] 讀 `/me/player`，帶 `additional_types=episode` 才拿得到 podcast 資訊
- [x] 每 5 秒對一次 API、每 0.2 秒本地推算進度，畫面秒數平順前進
- [x] 登入、正在播什麼、設定三個畫面
- [ ] **實機驗證 OAuth 能不能跳回 App** ← 這是地基，沒過的話後面全部要改
- [ ] 診斷畫面：顯示 token 狀態、最近幾次 API 回應、錯誤堆疊
      （讓一次匯入能回報最多資訊，減少來回次數）

## 階段 2：逐字稿產生管線（後端）

- [ ] 用 iTunes Search API 從「節目名」找到公開 RSS
- [ ] 從 RSS 比對出「集數名」對應的那一集，拿到音檔網址
      （名稱不會完全一樣，需要模糊比對；先在 Windows 本機用真實日文節目驗證）
- [ ] Supabase 建表（用 `kikitori_` 前綴，放在 trendrace 專案，不動既有的 `daily`）
      - `kikitori_episodes`：節目名、集名、Spotify episode id、RSS 音檔網址、時長
      - `kikitori_transcripts`：逐句時間軸、原文、翻譯
      - `kikitori_jobs`：轉錄任務狀態
- [ ] GitHub Actions 轉錄工作流
      - [ ] 下載音檔
      - [ ] ffmpeg 壓成 16kHz 單聲道（Groq 免費版單檔上限 25MB，不壓縮送不進去）
      - [ ] Groq Whisper 轉錄，取得逐句時間軸
      - [ ] 翻譯成中文
      - [ ] 寫回 Supabase
- [ ] **需要你提供：Groq API key**（https://console.groq.com → API Keys）

## 階段 3：App 端逐字稿

- [ ] 從 Supabase 讀逐字稿
- [ ] 逐句顯示，目前這句高亮，畫面自動捲動跟上
- [ ] 中日對照：原文下方顯示翻譯，可切換只看原文
- [ ] **點某句「現在講的是這句」重新對齊** ← 解決 Spotify 動態廣告造成的時間偏移
- [ ] 這一集還沒有逐字稿時，顯示「產生逐字稿」按鈕與進度

## 階段 4：語言學習功能

- [ ] 日文假名標注（漢字上方顯示讀音）
- [ ] 點單字查字典
- [ ] 生字本（先存 iPad 本機）
- [ ] 重點句收藏，之後可複習
- [ ] 逐句重聽（需要 Premium 的播放控制 API，把 Spotify 拉回該句開頭）

## 階段 5：體驗

- [ ] 換掉 App 圖示（現在還是最初測試用的打勾清單）
- [ ] 打開 App 時自動喚起 Spotify（靈動歌詞那個體驗）
- [ ] 沒網路、Spotify 沒在播、逐字稿還沒好等狀態的畫面
- [ ] 減少匯入次數：能遠端更新的東西（逐字稿、字典）都走後端，不綁進 App

---

## 已知限制

- **憑證 7 天到期**：免費 Apple 帳號的限制。到期要接電腦用 iloader 重裝。
  SideStore 的無線續簽目前壞掉（Apple 擋掉舊 User-Agent，AltSign PR #47 未合併），
  修好之後就能免接線。
- **Spotify 開發者模式**：限 5 個授權使用者且需 Premium。自己用沒問題，不能公開發布。
- **Spotify 獨家節目**：沒有公開 RSS，拿不到音檔，做不了逐字稿。
- **不能碰的東西**：Supabase 專案 `jglxgtumcbtquwuuqqep` 是公司專案。
