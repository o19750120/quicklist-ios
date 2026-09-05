import Foundation
import SwiftUI

/// 管理目前這一集的逐字稿：載入、跟著播放位置走、必要時請後端轉錄。
@MainActor
final class TranscriptModel: ObservableObject {

    @Published private(set) var transcript: Transcript?
    @Published private(set) var job: TranscriptJob?
    @Published private(set) var currentLineIndex: Int?
    @Published private(set) var isLoading = false
    @Published private(set) var errorMessage: String?
    @Published var showTranslation = true

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

    /// 排隊後每 20 秒看一次好了沒，好了就自動顯示。
    private func startPolling(episodeID: String) {
        pollTask?.cancel()
        pollTask = Task { [weak self] in
            for _ in 0..<90 {   // 最多盯 30 分鐘
                try? await Task.sleep(nanoseconds: 20_000_000_000)
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
