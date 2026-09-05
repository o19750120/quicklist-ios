import Foundation

/// 書庫裡的一集。聽過就會留下一筆，不管有沒有逐字稿。
///
/// 這份資料存在本機（UserDefaults），因為它是「我聽到哪」這種
/// 只對自己有意義的東西，不值得為它多開一張雲端表。
/// 之後要跨裝置同步再說。
struct LibraryEntry: Codable, Identifiable, Equatable {
    /// Spotify 的 episode id，也是跟逐字稿對應的鍵
    let episodeID: String
    var showName: String
    var episodeTitle: String
    var artworkURL: String?
    /// Spotify 版本的長度（含廣告），跟 NowPlaying.durationMs 同一個值
    var durationMs: Int
    /// 上次聽到哪。存的是 Spotify 的進度，不含對齊偏移，
    /// 這樣之後校正偏移改變了，書籤位置也不會跟著跑掉。
    var lastPositionMs: Int
    /// 逐字稿句數，0 表示這一集還沒有逐字稿
    var lineCount: Int
    var lastListenedAt: Date

    var id: String { episodeID }

    var hasTranscript: Bool { lineCount > 0 }

    /// 聽完幾成。回 0…1。
    var completion: Double {
        guard durationMs > 0 else { return 0 }
        return min(1, max(0, Double(lastPositionMs) / Double(durationMs)))
    }

    /// 剩最後半分鐘就算聽完了 —— podcast 結尾常是片尾曲或廣告，
    /// 要求聽到 100% 才算完成，實際上幾乎不會發生。
    var isFinished: Bool {
        durationMs > 0 && lastPositionMs >= durationMs - 30_000
    }

    var artworkImageURL: URL? {
        artworkURL.flatMap(URL.init(string:))
    }
}
