import Foundation
import SwiftUI

/// 負責持續追蹤 Spotify 正在播什麼。
///
/// 兩個節奏分開跑：
/// - 每 5 秒跟 Spotify 對一次答案（省 API 額度，也避免被限流）
/// - 每 0.2 秒在本地把進度往前推（畫面上的秒數才會平順地走）
@MainActor
final class NowPlayingModel: ObservableObject {

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

    private let pollInterval: UInt64 = 5_000_000_000   // 5 秒
    private let tickInterval: UInt64 = 200_000_000     // 0.2 秒

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

            if let state {
                // 換一集就把手動校正歸零，因為偏移量是綁在單集上的
                if state.id != nowPlaying?.id {
                    alignmentOffsetMs = 0
                }
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
                statusMessage = "Spotify 現在沒有在播東西。去 Spotify 按播放，再回來這裡。"
            }
        } catch {
            statusMessage = error.localizedDescription
            logError("Spotify", error.localizedDescription)
        }
    }

    /// 在兩次 API 之間，用本地時間把進度往前推
    private func tick() {
        guard let state = nowPlaying else { return }
        displayProgressMs = state.extrapolatedProgressMs()
    }

    /// 使用者說「現在講的是第 X 句」，據此算出偏移量
    func align(toLineStartMs lineStartMs: Int) {
        alignmentOffsetMs = lineStartMs - displayProgressMs
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

    func resetAlignment() {
        alignmentOffsetMs = 0
    }
}
