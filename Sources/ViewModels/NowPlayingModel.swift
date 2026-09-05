import Foundation
import SwiftUI

/// 負責持續追蹤 Spotify 正在播什麼。
///
/// 兩個節奏分開跑：
/// - 每 5 秒跟 Spotify 對一次答案（省 API 額度，也避免被限流）
/// - 每 0.2 秒在本地把進度往前推（畫面上的秒數才會平順地走）
@MainActor
final class NowPlayingModel: ObservableObject {

    /// 還沒有東西可顯示的時候，是哪一種「沒有」。
    /// 這三件事使用者要做的處置完全不同，不該都丟同一句錯誤訊息。
    enum IdleState: Equatable {
        case connecting
        case offline
        case nothingPlaying
        case failed(String)
    }

    @Published private(set) var idleState: IdleState = .connecting

    /// 網路斷了。跟 `isDisconnected`（Spotify 沒有裝置在播）是兩回事。
    @Published private(set) var isOffline = false

    @Published private(set) var nowPlaying: NowPlaying?
    @Published private(set) var displayProgressMs: Int = 0
    @Published private(set) var statusMessage: String?
    @Published private(set) var isRunning = false

    /// Spotify 回報沒有裝置在播，但我們還留著最後一集的畫面。
    /// 暫停太久就會變成這樣，不代表出錯。
    @Published private(set) var isDisconnected = false

    /// 使用者手動校正的偏移量（毫秒）。
    /// Spotify 會在 podcast 插入動態廣告，導致它的進度跟原始音檔對不上，
    /// 之後接上逐字稿時，使用者點「現在講的是這句」就會更新這個值。
    @Published var alignmentOffsetMs: Int = 0

    private let auth: SpotifyAuth
    private var api: SpotifyAPI { SpotifyAPI(auth: auth) }

    private var pollTask: Task<Void, Never>?
    private var tickTask: Task<Void, Never>?

    // 3 秒對一次是 20 次／分鐘，離 Spotify 的限流還很遠，
    // 但在 Spotify 那頭拖動進度條時，這裡跟上的速度明顯有感。
    private let pollInterval: UInt64 = 3_000_000_000
    private let tickInterval: UInt64 = 200_000_000

    init(auth: SpotifyAuth) {
        self.auth = auth
    }

    /// 對齊後的進度，之後餵給逐字稿用的就是這個值
    var alignedProgressMs: Int {
        max(0, displayProgressMs + alignmentOffsetMs)
    }

    func start() {
        guard !isRunning else { return }
        isRunning = true

        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.refresh()
                try? await Task.sleep(nanoseconds: self?.pollInterval ?? 5_000_000_000)
            }
        }

        tickTask = Task { [weak self] in
            while !Task.isCancelled {
                self?.tick()
                try? await Task.sleep(nanoseconds: self?.tickInterval ?? 200_000_000)
            }
        }
    }

    func stop() {
        pollTask?.cancel()
        tickTask?.cancel()
        pollTask = nil
        tickTask = nil
        isRunning = false
    }

    func refresh() async {
        do {
            let state = try await api.fetchNowPlaying()

            if isOffline {
                logInfo("Spotify", "網路回來了")
            }
            isOffline = false

            if let state {
                // 換一集就把手動校正歸零，因為偏移量是綁在單集上的
                if state.id != nowPlaying?.id {
                    alignmentOffsetMs = 0
                }
                idleState = .connecting
                nowPlaying = state
                displayProgressMs = state.progressMs
                statusMessage = nil
                isDisconnected = false

            } else if nowPlaying != nil {
                // Spotify 暫停一陣子後會回報「沒有裝置在播」。
                // 但學語言一定會暫停 —— 停下來查單字、重聽、抄筆記，
                // 這時候把逐字稿收走等於廢掉這支 App。
                // 所以保留最後一集的內容，只把進度停住。
                if !isDisconnected {
                    // 只在狀態轉換時記一次，不然每 5 秒就寫一筆
                    logInfo("Spotify", "回報沒有裝置在播，畫面保留在最後一集")
                }
                isDisconnected = true
                statusMessage = nil
                if var last = nowPlaying {
                    last.isPlaying = false
                    last.progressMs = displayProgressMs
                    last.fetchedAt = Date()
                    nowPlaying = last
                }

            } else {
                // 從頭到尾都沒抓到過東西，才是真的沒在播
                idleState = .nothingPlaying
                statusMessage = nil
            }
        } catch {
            if Self.isOfflineError(error) {
                // 網路斷掉時畫面保留最後一集 —— 跟暫停一樣，不該把逐字稿收走。
                // 只在狀態轉換時記一次，不然每 3 秒就寫一筆。
                if !isOffline {
                    logInfo("Spotify", "連不上網路，畫面保留在最後一集")
                }
                isOffline = true
                if nowPlaying == nil {
                    idleState = .offline
                }
                statusMessage = nil
            } else {
                idleState = .failed(error.localizedDescription)
                statusMessage = error.localizedDescription
                logError("Spotify", error.localizedDescription)
            }
        }
    }

    /// 這個錯誤是「連不上網路」還是「Spotify 那頭有問題」。
    /// 前者使用者要去開網路，後者才需要看錯誤訊息。
    static func isOfflineError(_ error: Error) -> Bool {
        guard let urlError = error as? URLError else { return false }
        switch urlError.code {
        case .notConnectedToInternet, .networkConnectionLost, .timedOut,
             .cannotFindHost, .cannotConnectToHost, .dnsLookupFailed,
             .dataNotAllowed, .internationalRoamingOff:
            return true
        default:
            return false
        }
    }

    /// 在兩次 API 之間，用本地時間把進度往前推
    private func tick() {
        guard let state = nowPlaying else { return }
        displayProgressMs = state.extrapolatedProgressMs()
    }

    /// 使用者說「現在講的是第 X 句」，據此算出偏移量並記起來
    func align(toLineStartMs lineStartMs: Int) {
        alignmentOffsetMs = lineStartMs - displayProgressMs
        if let id = nowPlaying?.id {
            AlignmentStore.save(alignmentOffsetMs, for: id)
        }
    }

    /// 進到一集時決定要用哪個偏移。
    ///
    /// 優先用使用者校正過的；沒有的話，用 Spotify 與原始音檔的長度差推估
    /// —— Spotify 版本多出來的時間幾乎都是開場廣告，這個猜測多半直接對上。
    func applyAlignment(episodeID: String, transcript: Transcript?) {
        if let saved = AlignmentStore.load(for: episodeID) {
            alignmentOffsetMs = saved
            logInfo("對齊", String(format: "沿用上次校正 %+.1f 秒", Double(saved) / 1000))
            return
        }

        guard let transcript,
              let playing = nowPlaying,
              let guess = Self.guessOffsetMs(
                  spotifyDurationMs: playing.durationMs,
                  sourceDurationMs: transcript.sourceDurationMs
              ) else {
            alignmentOffsetMs = 0
            return
        }

        alignmentOffsetMs = guess
        logInfo("對齊", String(format: "自動推估偏移 %+.1f 秒（Spotify 版本較長）", Double(guess) / 1000))
    }

    /// Spotify 比原始音檔長多少，就往前推多少。
    /// 差太小（可能只是編碼誤差）或差太大（可能根本抓錯集數）都不套用。
    static func guessOffsetMs(spotifyDurationMs: Int, sourceDurationMs: Int) -> Int? {
        guard spotifyDurationMs > 0, sourceDurationMs > 0 else { return nil }
        let diff = spotifyDurationMs - sourceDurationMs
        guard diff > 5_000, diff < 600_000 else { return nil }
        return -diff
    }

    /// 讓 Spotify 跳到指定位置（逐句重聽）
    func seek(toMs positionMs: Int) async {
        do {
            try await api.seek(toMs: positionMs)
            displayProgressMs = positionMs
            if var state = nowPlaying {
                state.progressMs = positionMs
                state.fetchedAt = Date()
                nowPlaying = state
            }
            logInfo("Spotify", "跳到 \(positionMs.asPlaybackTime)")
            statusMessage = nil
        } catch {
            statusMessage = error.localizedDescription
            logError("Spotify", "跳轉失敗：\(error.localizedDescription)")
        }
    }

    /// 讓 Spotify 接著播。成功的話下一輪輪詢就會把內容帶進來，
    /// 使用者從頭到尾不用離開這個 App。
    func resumePlayback() async {
        do {
            try await api.resumePlayback()
            logInfo("Spotify", "從 App 這頭恢復播放")
            // 不等下一輪，立刻對一次
            try? await Task.sleep(nanoseconds: 700_000_000)
            await refresh()
        } catch {
            idleState = .failed(error.localizedDescription)
            statusMessage = error.localizedDescription
            logError("Spotify", "恢復播放失敗：\(error.localizedDescription)")
        }
    }

    /// 從書庫點一集：叫 Spotify 播它，並從上次聽到的地方接下去。
    ///
    /// 回傳有沒有成功。失敗多半是 Spotify 那頭完全沒有裝置（404），
    /// 那種情況只能真的把 Spotify 打開一次，呼叫端會處理。
    func play(entry: LibraryEntry) async -> Bool {
        do {
            // 快聽完的那幾集從頭開始，不然一進去就播到片尾
            let resumeAt = entry.isFinished ? 0 : entry.lastPositionMs
            try await api.play(episodeID: entry.episodeID, positionMs: resumeAt)
            logInfo("Spotify", "從書庫播放：\(entry.episodeTitle)")

            // 不等下一輪輪詢，立刻把畫面接上
            try? await Task.sleep(nanoseconds: 900_000_000)
            await refresh()
            return true
        } catch {
            statusMessage = error.localizedDescription
            logError("Spotify", "從書庫播放失敗：\(error.localizedDescription)")
            return false
        }
    }

    func resetAlignment() {
        alignmentOffsetMs = 0
        if let id = nowPlaying?.id {
            AlignmentStore.remove(for: id)
        }
    }
}
