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

## 階段 1：Spotify 連動 ✅ 已完成

- [x] PKCE 授權流程（手機端不需要 client secret）
- [x] Client ID 由 GitHub Secret 在建置時注入，不進公開 repo
- [x] `kikitori://spotify-callback` URL scheme
- [x] 讀 `/me/player`，帶 `additional_types=episode` 才拿得到 podcast 資訊
- [x] 每 5 秒對一次 API、每 0.2 秒本地推算進度，畫面秒數平順前進
- [x] 登入、正在播什麼、設定三個畫面
- [x] **驗證 OAuth 能跳回 App**（iPad 實機與 Mac 模擬器都通過）
- [x] 診斷畫面：token 狀態、播放狀態、任務狀態、App 內執行紀錄（可一鍵複製）
      （讓一次匯入能回報最多資訊，減少來回次數）

## 階段 2：逐字稿產生管線（後端）✅ 本機與雲端都跑通

- [x] 用 iTunes Search API 從「節目名」找到公開 RSS
- [x] 從 RSS 比對出「集數名」對應的那一集，拿到音檔網址（四個真實日文節目全部命中）
      （名稱不會完全一樣，需要模糊比對；先在 Windows 本機用真實日文節目驗證）
- [x] Supabase 建表（`kikitori_` 前綴，放在 trendrace，未動既有的 `daily`）
      - `kikitori_episodes`：節目名、集名、Spotify episode id、RSS 音檔網址、時長
      - `kikitori_transcripts`：逐句時間軸、原文、翻譯
      - `kikitori_jobs`：轉錄任務狀態
- [x] GitHub Actions 轉錄工作流（Ubuntu runner）
      - [x] 下載音檔
      - [x] ffmpeg 壓成 16kHz 單聲道並依實際長度切段
      - [x] Groq Whisper 轉錄，逐段累加時間軸偏移
      - [x] 分批翻譯，強制輸出等長陣列確保逐句對齊
      - [x] 寫回 Supabase
- [x] 金鑰改用 `C:\Me\YT起號` 既有的（Deepgram / Groq×6 / Gemini×11 / AssemblyAI），全部驗證可用
- [x] 轉錄改用 Deepgram 直讀音檔網址：11 分鐘節目 4 秒轉完，省掉下載與 ffmpeg
- [x] 重寫斷句演算法：最長 51 秒 → 16.5 秒，平均 3.4 秒 22 字
- [x] 雲端實跑驗證：122 句已寫進 Supabase

## 階段 3：App 端逐字稿 ✅ 已完成

- [x] 從 Supabase 讀逐字稿（anon key，查詢格式已用測試資料驗證通過）
- [x] 逐句顯示，目前這句高亮，自動捲動（手動捲動時暫停跟隨 6 秒）
- [x] 中日對照，工具列可切換
- [x] **長按某句「現在講的是這句」重新對齊**，單擊則跳到該句（Spotify seek）
- [x] 沒有逐字稿時顯示「產生逐字稿」，排隊中每 20 秒自動查一次，好了自動出現

## 階段 3.5：書庫 ✅ 已完成

- [x] 聽過的節目與集數列表，依節目分組（`LibraryView`）
- [x] 每一集顯示：句數、上次聽到哪、完成度
- [x] 點進去可以純閱讀，不必正在播放也能看逐字稿（`ReaderView`），
      並停在上次聽到的那句
- [x] 播放進度記錄（`LibraryStore`，存 UserDefaults，之後可考慮同步）
- [x] 書庫點一集就叫 Spotify 播它，並從上次聽到的地方接下去，不必離開 App
      （已聽完的那幾集從頭開始；長按才是純閱讀。
      Spotify 完全沒有裝置時才退回用 URL scheme 把 Spotify 打開）

## 階段 6：轉錄品質工程（核心，優先於階段 4）

這是這支 App 的命脈 —— 逐字稿不準，後面的翻譯、假名、生字本全部沒有意義。

### 金鑰使用策略

- [ ] 一把金鑰要把它所有能用的模型額度都耗盡，才換下一把
      （目前是撞到錯誤就換，等於每把都只用一點點）
- [ ] 禁止同一把金鑰並行跑多個任務
- [ ] 關閉或降到最低的思考模式 —— 各模型參數不同，要逐一查證後寫進設定

### 送 API 的批次大小

- [x] 查清楚上限：`gemini-3.8-flash` 輸入 1,048,576 tokens、輸出 65,536；
      `gemini-3.5-transcribe` 輸入 98,304、輸出 32,768
- [x] 算過一集的量：約 2500 字日文 ≈ 4000 tokens，只佔輸入上限的 0.4%
- [ ] 翻譯改成整集一次送，取消現在的 40 句分批
      （分批唯一的好處是失敗時只掉一批，改成整集後要另外處理重試）

### 轉錄品質，照這個優先序做

**1. 總時長必須等於音檔長度** —— 這是硬性檢查，不通過就不要往下做
- [x] `backend/verify.py`：比對逐字稿時間範圍與音檔實際長度（ffprobe 讀網址即可，
      0.7 秒，不必下載）。涵蓋不足就當這家失敗、換下一家
- [x] 同時檢查中間有沒有超過 30 秒的空白 —— 這道檢查立刻抓到真問題（見下）

**2. 音軌與逐字稿對齊**
- [ ] 抽樣驗證中段與尾段的時間戳沒有累積漂移

**3. 內容準確性**

- [ ] **填充詞、語尾助詞絕對不能被清掉**（あのー／その／なんか／えーと、
      句尾的 ね／よ／な／かな）。這些是口語與書面語的分界，
      學語言的人要聽懂真實對話靠的就是這些節奏。
      最怕的是「音檔轉文字」那一步模型就自作主張過濾掉 —— 
      那階段丟掉的東西，後面任何修正都救不回來。
      要逐一確認 Deepgram / Whisper / gemini-3.5-transcribe 各自的行為與參數。
- [x] **抓到並修掉一個嚴重的幻覺來源**：指定 `language=ja` 去轉英文段落時，
      Deepgram 不會跳過，而是把英文音節硬拼成假名
      （"thankyouismaybeyouronecoose。。。"），那串垃圾會被當成正常內容
      寫進逐字稿再拿去翻譯。雙語節目 バイリンガルニュース 有 45% 的內容如此。
      改用 `nova-3 + language=multi` 之後：空白從 50 段降到 0 段、
      字數 26,582 → 57,631、涵蓋 99.8%。而且純日文節目也沒變差 ——
      填充詞 16 vs 14、語尾助詞 51 vs 46，反而保留更多
- [x] 英文詞之間補空格（原本 "".join 讓英文黏成 "thinkitismaybeaonecause"）
- [ ] 其他幻覺來源：音樂與長靜默段落
- [ ] 修正斷詞錯誤（例如「日本語」被拆成「日」「本語」）
- [ ] 找日文標準答案語料：頻道主自己提供的逐字稿，有時間軸更好，
      用來挖掘實際會出現的錯誤模式，讓負責修正的模型知道要對症下藥

### 多人辨識（不強求）

- [ ] 逐字稿標出誰在講話。每個節目、每一集人數都不同，
      但 Deepgram 的 diarize 與 gemini-3.5-transcribe 都是自動偵測人數，
      不需要事先指定，所以可行性比想像中高
- [ ] App 端要怎麼呈現（顏色、縮排、名字）再議

### 架構評估

- [x] 確認 `gemini-3.5-transcribe` 可用（2026-08 發布），有詞級時間戳、
      說話者辨識、自訂詞彙偏向。限制：啟用詞級時間戳時單檔上限 30 分鐘，
      且準確度會略降
- [ ] 實測比較：Deepgram vs Whisper vs gemini-3.5-transcribe，同一集音檔
- [ ] 規劃中的流程：Whisper 與 Gemini 各自轉一份，再用另一個 Gemini
      對照兩份產出最終版

## 階段 4：語言學習功能

- [ ] 日文假名標注（漢字上方顯示讀音）
- [ ] 點單字查字典
- [ ] 生字本（先存 iPad 本機）
- [ ] 重點句收藏，之後可複習
- [x] 逐句重聽：點逐字稿某一句，Spotify 就跳到那句開頭。
      程式本來就寫好了，這次用 UI 測試實測確認：點第 58 句（326.7 秒），
      Spotify 跳到 327.5 秒，誤差 0.8 秒；也順帶確認帳號是 Premium（否則會 403）。

## 階段 5：體驗與可觀測性

- [x] App 遙測回傳：錯誤與關鍵事件寫進 `kikitori_logs`，
      開發時直接查得到裝置上發生什麼，不必靠截圖轉述
      （只送技術事件，device_id 是隨機碼，App 只能寫不能讀）
- [x] `backend/status.py`：一次看完轉錄任務、逐字稿存量、App 紀錄、
      憑證剩餘天數、CI 狀態
- [x] 排隊改為即時觸發（Supabase 觸發器 → GitHub，實測 3 秒開工）
- [x] 每日健康檢查排程：憑證快到期、卡住的任務、SideStore 修好沒 → 推 Discord
      （`backend/healthcheck.py`，每天台北 9 點跑，沒事就安靜）
- [x] Claude Code 這端掛 hook：push 後自動盯 CI，失敗把摘要寫進
      `build/ci-last-failure.log` 並跳通知（`.claude/settings.json` + `scripts/ci-watch.sh`）

- [x] 換掉 App 圖示（波形，配色取自 Theme；`scripts/make-icon.py` 可重新產生）
- [x] 排隊中的畫面看得到進度階段（尋找音檔 → 轉錄中 → 斷句 → 翻譯 N 句 →
      寫入資料庫），App 輪詢也從 20 秒縮到 8 秒才跟得上
- [x] 長按選單閃爍（contextMenu 沒有固定預覽，跟著高亮狀態重繪）
- [x] 自動推估時間軸偏移（Spotify 與原始音檔的長度差），校正過的偏移會記住
- [x] 輪詢 5 秒縮短為 3 秒，從背景切回來立刻同步
- [x] Mac 開發環境：setup-mac.sh 一鍵設定、DEVELOPMENT.md、金鑰外洩防呆
- [x] 不必離開 App 就能接上 Spotify：沒在播時給「接著播」，
      直接用 Web API 恢復播放；真的沒有裝置時才需要「打開 Spotify」
      （iOS 不允許 App 一啟動就把使用者踢去別的 App，那也是壞體驗）
- [x] 沒網路、Spotify 沒在播、逐字稿還沒好各有各的畫面與說法
      （離線時保留最後一集，跟暫停一樣不把逐字稿收走）
- [x] 減少匯入次數：逐字稿已經全走後端，App 只存「聽到哪」這種本機才有意義的東西。
      階段 4 的字典也要照這個原則走後端，不要綁進 App。
- [ ] 斷句還有零星瑕疵（Deepgram 把「日本語」拆成「日」「本語」時會跟著切錯），
      之後可讓 Gemini 依語意重新分句

---

## 已知限制

- **憑證 7 天到期**：免費 Apple 帳號的限制。到期要接電腦用 iloader 重裝。
  SideStore 的無線續簽目前壞掉（Apple 擋掉舊 User-Agent，AltSign PR #47 未合併），
  修好之後就能免接線。
- **Spotify 開發者模式**：限 5 個授權使用者且需 Premium。自己用沒問題，不能公開發布。
- **Spotify 獨家節目**：沒有公開 RSS，拿不到音檔，做不了逐字稿。
- **不能碰的東西**：Supabase 專案 `jglxgtumcbtquwuuqqep` 是公司專案。
