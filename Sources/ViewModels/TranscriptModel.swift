import Foundation
import SwiftUI

/// 管理目前這一集的逐字稿：載入、跟著播放位置走、必要時請後端轉錄。
@MainActor
final class TranscriptModel: ObservableObject {

    @Published private(set) var transcript: Transcript?
    @Published private(set) var job: TranscriptJob?
    @Published private(set) var currentLineIndex: Int?
    /// 播完一句之後要做什麼。跟 `interaction`（點下去要做什麼）是兩件事。
    @Published private(set) var repeatsCurrentLine = false
    /// 正在重聽哪一句。鎖住它，播到句尾就跳回句首，直到使用者自己往下。
    @Published private(set) var repeatingLine: Int?

    /// 剛剛才跳回句首，先別再跳一次 ——
    /// seek 要等 Spotify 回應，那之前進度還會往前跑幾次，會連續觸發。
    private var lastRepeatAt = Date.distantPast

    @Published private(set) var isLoading = false
    @Published private(set) var errorMessage: String?
    @Published var showTranslation = true

    /// 點一句話要做什麼。
    ///
    /// 兩件事搶同一個手勢：點句子跳到那裡播、點詞查意思。
    /// 日文的詞佔滿整行，沒有真正的「空白處」可以分流，所以改成明講的模式切換。
    @Published var interaction: Interaction = .playback

    enum Interaction: String, CaseIterable, Identifiable {
        /// 點一句就讓 Spotify 跳到那裡
        case playback
        /// 點一個詞就查意思
        case lookup

        var id: String { rawValue }

        var label: String {
            switch self {
            case .playback: return "播放"
            case .lookup:   return "查詞"
            }
        }

        var icon: String {
            switch self {
            case .playback: return "play.fill"
            case .lookup:   return "text.magnifyingglass"
            }
        }

        var hint: String {
            switch self {
            case .playback: return "點一句跳到那裡"
            case .lookup:   return "點一個詞看意思"
            }
        }
    }

    private let service = SupabaseService()
    /// 目前這份逐字稿是哪一集的。外面要拿句數時得先確認是同一集，
    /// 不然換集的瞬間會把上一集的數字算到新的那一集頭上。
    private(set) var loadedEpisodeID: String?
    private var pollTask: Task<Void, Never>?

    var hasTranscript: Bool { !(transcript?.isEmpty ?? true) }

    /// 這一集的句數。逐字稿還沒載到、或載到的是別集的，一律回 0。
    func lineCount(for episodeID: String) -> Int {
        guard loadedEpisodeID == episodeID else { return 0 }
        return transcript?.lines.count ?? 0
    }
    var isConfigured: Bool { service.isConfigured }

    // MARK: - 載入

    /// 換集時呼叫。同一集重複呼叫不會重新抓。
    func loadIfNeeded(episodeID: String) async {
        guard loadedEpisodeID != episodeID else { return }

        loadedEpisodeID = episodeID
        transcript = nil
        job = nil
        currentLineIndex = nil
        errorMessage = nil
        repeatsCurrentLine = false
        repeatingLine = nil

        await reload(episodeID: episodeID)
    }

    func reload(episodeID: String) async {
        guard service.isConfigured else {
            errorMessage = "這個版本沒有注入 Supabase 金鑰"
            return
        }

        isLoading = true
        defer { isLoading = false }

        do {
            if let found = try await service.fetchTranscript(spotifyEpisodeID: episodeID) {
                transcript = found
                job = nil
                logInfo("逐字稿", "載入成功，共 \(found.lines.count) 句")
            } else {
                transcript = nil
                job = try await service.fetchJob(spotifyEpisodeID: episodeID)
                logInfo("逐字稿", "這一集還沒有逐字稿（任務狀態：\(job?.status.label ?? "無")）")

                // 進行中的任務要自動盯著，不然關掉 App 再打開就不會自己更新了
                if let status = job?.status, status == .queued || status == .running {
                    startPolling(episodeID: episodeID)
                }
            }
        } catch {
            errorMessage = error.localizedDescription
            logError("逐字稿", error.localizedDescription)
        }
    }

    // MARK: - 逐句重聽

    func toggleRepeat() {
        repeatsCurrentLine.toggle()
        repeatingLine = repeatsCurrentLine ? currentLineIndex : nil
        lastRepeatAt = .distantPast
        logInfo("逐句重聽", repeatsCurrentLine ? "開啟，鎖住第 \((repeatingLine ?? -1) + 1) 句" : "關閉")
    }

    /// 使用者自己跳到別句時，重聽的對象跟著換過去。
    func moveRepeat(to index: Int) {
        guard repeatsCurrentLine else { return }
        repeatingLine = index
        lastRepeatAt = Date()
    }

    /// 播到這一句的結尾了嗎？是的話回傳該跳回去的那一句。
    ///
    /// 呼叫端拿到之後負責叫 Spotify 跳轉 —— 這裡不直接碰播放，
    /// 免得這個 model 得知道 SpotifyAPI 的存在。
    func lineToRepeat(at positionMs: Int) -> TranscriptLine? {
        // 按下開關時如果還沒開始跟隨（currentLineIndex 是 nil），
        // repeatingLine 也會是 nil —— 那時退回用當下這一句，不要整個不動作。
        guard repeatsCurrentLine,
              let transcript,
              let index = repeatingLine ?? currentLineIndex,
              index >= 0, index < transcript.lines.count else { return nil }

        let line = transcript.lines[index]
        guard positionMs >= line.endMs else { return nil }
        guard Date().timeIntervalSince(lastRepeatAt) > 1.5 else { return nil }

        lastRepeatAt = Date()
        return line
    }

    // MARK: - 跟著播放位置走

    func updatePosition(_ positionMs: Int) {
        guard let transcript else { return }
        let index = transcript.indexOfLine(at: positionMs)
        if index != currentLineIndex {
            currentLineIndex = index
        }
    }

    // MARK: - 請後端轉錄

    func requestTranscription(for episode: NowPlaying) async {
        do {
            try await service.enqueue(episode: episode)
            job = TranscriptJob(id: "", status: .queued, stage: nil, error: nil)
            startPolling(episodeID: episode.id)
        } catch {
            errorMessage = error.localizedDescription
            logError("逐字稿", "排隊失敗：\(error.localizedDescription)")
        }
    }

    /// 排隊後每 8 秒看一次好了沒，好了就自動顯示。
    ///
    /// 間隔要短於一個階段的長度，不然畫面上的「轉錄中」「翻譯 122 句」
    /// 會整段跳過去，看起來像卡住。
    private func startPolling(episodeID: String) {
        pollTask?.cancel()
        pollTask = Task { [weak self] in
            for _ in 0..<225 {   // 最多盯 30 分鐘
                try? await Task.sleep(nanoseconds: 8_000_000_000)
                guard !Task.isCancelled, let self else { return }

                await self.reload(episodeID: episodeID)
                if self.hasTranscript || self.job?.status == .failed {
                    return
                }
            }
        }
    }

    func stopPolling() {
        pollTask?.cancel()
        pollTask = nil
    }
}
