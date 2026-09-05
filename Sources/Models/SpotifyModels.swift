import Foundation

/// Spotify 正在播放的內容。podcast 單集與歌曲都收斂成同一個型別，
/// 因為畫面上要顯示的東西是一樣的。
struct NowPlaying: Equatable {
    enum Kind: String {
        case episode
        case track
        case unknown
    }

    var kind: Kind
    var id: String
    var title: String
    /// 節目名（podcast）或演出者（歌曲）
    var subtitle: String
    var artworkURL: URL?
    var durationMs: Int
    var progressMs: Int
    var isPlaying: Bool
    /// 這一份狀態是什麼時候從 Spotify 拿到的，用來在兩次輪詢之間本地推算進度
    var fetchedAt: Date = Date()

    /// 把 API 回來的進度，加上「從拿到之後又過了多久」，得到當下的實際進度
    func extrapolatedProgressMs(now: Date = Date()) -> Int {
        guard isPlaying else { return progressMs }
        let elapsed = Int(now.timeIntervalSince(fetchedAt) * 1000)
        return min(progressMs + elapsed, durationMs)
    }
}

/// 解析 GET /v1/me/player 的回應。
/// item 有可能是 track 也有可能是 episode，結構不同，所以手動解。
struct PlayerStateResponse {
    let nowPlaying: NowPlaying?

    init?(json: [String: Any]) {
        guard let item = json["item"] as? [String: Any] else {
            self.nowPlaying = nil
            return
        }

        let typeString = item["type"] as? String ?? "unknown"
        let kind = NowPlaying.Kind(rawValue: typeString) ?? .unknown
        let id = item["id"] as? String ?? ""
        let title = item["name"] as? String ?? "（沒有標題）"
        let duration = item["duration_ms"] as? Int ?? 0
        let progress = json["progress_ms"] as? Int ?? 0
        let isPlaying = json["is_playing"] as? Bool ?? false

        var subtitle = ""
        var images: [[String: Any]] = []

        switch kind {
        case .episode:
            let show = item["show"] as? [String: Any]
            subtitle = show?["name"] as? String ?? ""
            images = (item["images"] as? [[String: Any]])
                ?? (show?["images"] as? [[String: Any]])
                ?? []
        case .track:
            let artists = item["artists"] as? [[String: Any]] ?? []
            subtitle = artists.compactMap { $0["name"] as? String }.joined(separator: ", ")
            let album = item["album"] as? [String: Any]
            images = album?["images"] as? [[String: Any]] ?? []
        case .unknown:
            images = item["images"] as? [[String: Any]] ?? []
        }

        // Spotify 的圖由大到小排，取第一張最大的
        let artworkURL = (images.first?["url"] as? String).flatMap(URL.init(string:))

        self.nowPlaying = NowPlaying(
            kind: kind,
            id: id,
            title: title,
            subtitle: subtitle,
            artworkURL: artworkURL,
            durationMs: duration,
            progressMs: progress,
            isPlaying: isPlaying
        )
    }
}

extension Int {
    /// 毫秒轉成 12:34 或 1:02:03
    var asPlaybackTime: String {
        let total = self / 1000
        let hours = total / 3600
        let minutes = (total % 3600) / 60
        let seconds = total % 60
        if hours > 0 {
            return String(format: "%d:%02d:%02d", hours, minutes, seconds)
        }
        return String(format: "%d:%02d", minutes, seconds)
    }
}
